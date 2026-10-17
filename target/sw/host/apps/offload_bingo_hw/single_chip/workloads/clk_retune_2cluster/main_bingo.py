# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# The host reprograms its own clock while the bingo PM changes the cluster
# clocks, both in the clock/reset controller (test of the per-domain division
# registers). Copy i (global task i) moves a chunk of an L3 vector into the L1
# of cluster i % 2 on its DM core (cluster c fills its buffer with half c of the
# vector, in order) and depends on copy i - 1, so the two clusters
# take turns and the PM changes a cluster level at every copy. A host node
# without dependencies reprograms the host clock (domain 0) retune_count times
# with enable_clk_domain meanwhile. The host checks both buffers and reports the
# division registers once all of it is done.
import os
import sys
import argparse
import pathlib

current_dir = os.path.dirname(os.path.abspath(__file__))
WORKLOADS_DIR = os.path.dirname(current_dir)
sys.path.append(WORKLOADS_DIR)
ROOT_DIR = os.path.abspath(os.path.join(current_dir, "../../../../../../../../"))
ROOT_DIR = os.path.normpath(ROOT_DIR)
APP_NAME = "Single-Chip Clock Retune 2-Cluster"

sys.path.append(f"{ROOT_DIR}/target/sw/host/runtime/libbingo/mini_compiler")
sys.path.append(f"{ROOT_DIR}/util/sim")
import _usg_paths  # noqa: F401,E402  (registers util/sim/{common,gemm,xdma,ara} on sys.path)

from bingo_dfg import BingoDFG  # noqa: E402
from bingo_platform import guard_cluster_count, parse_platform_cfg  # noqa: E402
from bingo_node import BingoNode  # noqa: E402
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol  # noqa: E402
from bingo_kernel_args import (  # noqa: E402
    SnaxBingoKernelIdma1dCopyArgs,
    HostBingoKernelDummyArgs,
    HostBingoKernelCheckResultArgs,
)
from data_utils import format_scalar_definition, format_vector_definition  # noqa: E402

cur_chiplet_id = 0
DMA_CORE_ID = 1
HOST_CORE_ID = 2
# Every host kernel sits on the host core (cluster 0, core 2)
HOST_CLUSTER_ID = 0


def get_args():
    parser = argparse.ArgumentParser(description="Bingo HW Manager")
    parser.add_argument("--output_dir", type=str, default=".")
    parser.add_argument("--output_offload_file_name", type=str, default="offload_bingo_hw.h")
    parser.add_argument("-c", "--cfg", type=pathlib.Path, required=True)
    parser.add_argument("--hwcfg", type=pathlib.Path, required=True)
    parser.add_argument("--platformcfg", type=pathlib.Path, required=True)
    parser.add_argument("--data_h", type=pathlib.Path, default=None)
    return parser.parse_args()


def define_workload_params(cfg_path):
    params = {}
    with open(cfg_path) as f:
        for line in f:
            line = line.split("//", 1)[0].split("#", 1)[0].strip()
            if not line or line in ("{", "}"):
                continue
            key, sep, value = line.partition(":")
            if sep:
                params[key.strip()] = int(value.strip().rstrip(","), 0)
    for name in ("num_clusters", "chain_len", "chunk_size", "retune_count"):
        if name not in params:
            raise KeyError(f"params.hjson must define {name}")
    if params["chain_len"] % 2:
        raise ValueError("chain_len must be even (the copies alternate between two clusters)")
    params["A_size"] = params["chain_len"] * params["chunk_size"]
    return params


def emit_header_file(params):
    a_size = params["A_size"]
    return "\n\n".join([
        "#include <stdint.h>",
        "uint64_t __host_bingo_kernel_clk_retune(void *arg);",
        "uint64_t __host_bingo_kernel_clk_report(void *arg);",
        format_scalar_definition("uint32_t", "A_size", a_size),
        format_vector_definition("uint8_t", "A_l3", [idx & 0xFF for idx in range(a_size)]),
    ]) + "\n"


def create_dfg(params, platform):
    dfg = BingoDFG(
        num_chiplets=platform["num_chiplets"],
        num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
        num_cores_per_cluster=platform["num_cores_per_cluster"],
        is_host_as_acc=True,
        chiplet_ids=platform["chiplet_ids"],
    )
    chunk, half = params["chunk_size"], params["A_size"] // 2
    # Cluster c holds half c of A, filled by the copies i with i % 2 == c
    buffers = [BingoMemAlloc(f"A_L1_buf_{c}", size=half, mem_level="L1",
                             chip_id=cur_chiplet_id, cluster_id=c) for c in range(2)]
    prev = None
    for i in range(params["chain_len"]):
        copy = BingoNode(
            assigned_chiplet_id=cur_chiplet_id, assigned_cluster_id=i % 2, assigned_core_id=DMA_CORE_ID,
            kernel_name="__snax_bingo_kernel_idma_1d_copy",
            kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                src_addr=BingoMemSymbol("A_l3", offset=(i % 2) * half + (i // 2) * chunk),
                dst_addr=buffers[i % 2].view((i // 2) * chunk),
                size=chunk,
            ),
        )
        dfg.bingo_add_node(copy)
        if prev is not None:
            dfg.bingo_add_edge(prev, copy)
        prev = copy
    retune = BingoNode(
        assigned_chiplet_id=cur_chiplet_id, assigned_cluster_id=HOST_CLUSTER_ID, assigned_core_id=HOST_CORE_ID,
        kernel_name="__host_bingo_kernel_clk_retune",
        kernel_args=HostBingoKernelDummyArgs(dummy_input=params["retune_count"]),
    )
    dfg.bingo_add_node(retune)
    report = BingoNode(
        assigned_chiplet_id=cur_chiplet_id, assigned_cluster_id=HOST_CLUSTER_ID, assigned_core_id=HOST_CORE_ID,
        kernel_name="__host_bingo_kernel_clk_report",
        kernel_args=HostBingoKernelDummyArgs(dummy_input=0),
    )
    dfg.bingo_add_node(report)
    dfg.bingo_add_edge(prev, report)
    dfg.bingo_add_edge(retune, report)
    for c in range(2):
        check = BingoNode(
            assigned_chiplet_id=cur_chiplet_id, assigned_cluster_id=HOST_CLUSTER_ID, assigned_core_id=HOST_CORE_ID,
            kernel_name="__host_bingo_kernel_check_result",
            kernel_args=HostBingoKernelCheckResultArgs(
                name=f"A_cluster{c}",
                golden_data_addr=BingoMemSymbol("A_l3", offset=c * half),
                output_data_addr=buffers[c],
                data_size=half,
            ),
        )
        dfg.bingo_add_node(check)
        dfg.bingo_add_edge(report, check)
    return dfg


def main():
    args = get_args()
    os.makedirs(args.output_dir, exist_ok=True)
    params = define_workload_params(args.cfg)
    if args.data_h is not None:
        args.data_h.write_text(emit_header_file(params))
    platform = parse_platform_cfg(args.platformcfg)
    if not guard_cluster_count(params, platform, args.output_dir, args.output_offload_file_name):
        return
    dfg = create_dfg(params, platform)
    data_header = os.path.basename(args.data_h) if args.data_h is not None else "clk_retune_data.h"
    dfg.bingo_compile_dfg(APP_NAME, args.output_dir, args.output_offload_file_name,
                          extra_include_header_list=[data_header])


if __name__ == "__main__":
    main()
