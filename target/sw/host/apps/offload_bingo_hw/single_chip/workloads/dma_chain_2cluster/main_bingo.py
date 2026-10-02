# Copyright 2025 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# A chain of iDMA copies on the cluster-0 DM core, for the bingo fault-precursor
# scenarios: copy i (global task i) moves chunk i of an L3 vector into the
# cluster-0 L1 buffer and depends on copy i - 1, so each one is a separate
# dispatch of that core. The host checks the whole buffer. Cluster 1 copies the
# first chunk into its own L1 and the host checks it, as in dummy_2cluster.
import os
import sys
import argparse
import pathlib

current_dir = os.path.dirname(os.path.abspath(__file__))
WORKLOADS_DIR = os.path.dirname(current_dir)
sys.path.append(WORKLOADS_DIR)
ROOT_DIR = os.path.abspath(os.path.join(current_dir, "../../../../../../../../"))
ROOT_DIR = os.path.normpath(ROOT_DIR)
APP_NAME = "Single-Chip DMA Chain 2-Cluster"

print(f"ROOT_DIR: {ROOT_DIR}")
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

# Chip 0; the clusters are enumerated from params["num_clusters"].
cur_chiplet_id = 0

# id abstraction aligned with the cmd processor hw
DMA_CORE_ID = 1
HOST_CORE_ID = 2
# There is one host core per chiplet, and the bingo mini-compiler requires every
# host kernel to sit on it (cluster 0, core 2) -- even when the buffer it checks
# lives in another cluster's L1.
HOST_CLUSTER_ID = 0


def get_args():
    parser = argparse.ArgumentParser(description="Bingo HW Manager")
    parser.add_argument(
        "--output_dir",
        type=str,
        default=".",
        help="Output directory for generated files",
    )
    parser.add_argument(
        "--output_offload_file_name",
        type=str,
        default="offload_bingo_hw.h",
        help="Output filename for the offload header file",
    )
    parser.add_argument(
        "-c",
        "--cfg",
        type=pathlib.Path,
        required=True,
        help="Select param config file (params.hjson)",
    )
    parser.add_argument(
        "--hwcfg",
        type=pathlib.Path,
        required=True,
        help="Select hardware config file",
    )
    parser.add_argument(
        "--platformcfg",
        type=pathlib.Path,
        required=True,
        help="Path to generated occamy.h with HW platform defines",
    )
    parser.add_argument(
        "--data_h",
        type=pathlib.Path,
        default=None,
        help="Output path for the generated data header (e.g. dma_chain_data.h).",
    )
    parser.add_argument(
        "--clear-after", type=int, default=None,
        help="Insert a host risk-CLEAR dependency between this copy and the next.",
    )
    return parser.parse_args()


def define_workload_params(cfg_path, _hwcfg_path):
    params = {}
    with open(cfg_path) as f:
        for line in f:
            line = line.split("//", 1)[0].split("#", 1)[0].strip()
            if not line or line in ("{", "}"):
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
            product = 1
            value = value.strip().rstrip(",")
            for factor in value.split("*"):
                product *= int(factor.strip(), 0)
            params[key.strip()] = product

    for name in ("num_clusters", "chain_len", "chunk_size"):
        if name not in params:
            raise KeyError(f"params.hjson must define {name}")

    params["A_size"] = params["chain_len"] * params["chunk_size"]
    return params, dict(params)


def emit_header_file(**kwargs):
    a_size = kwargs["A_size"]
    a_data = [idx & 0xFF for idx in range(a_size)]

    data_str = [
        "#include <stdint.h>",
        "uint64_t __host_bingo_kernel_risk_clear(void *arg);",
        format_scalar_definition("uint32_t", "A_size", a_size),
        format_vector_definition("uint8_t", "A_l3", a_data),
    ]
    return "\n\n".join(data_str) + "\n"


def define_memory_handles(params):
    """L3 source symbol, the cluster-0 chain buffer and the cluster-1 buffer (L1)."""
    mem_handles = {"A_data_L3_symbol": BingoMemSymbol("A_l3", offset=0)}
    sizes = {0: params["A_size"], 1: params["chunk_size"]}
    for cluster_id in range(params["num_clusters"]):
        mem_handles[f"A_L1_buf_{cluster_id}"] = BingoMemAlloc(
            f"A_L1_buf_{cluster_id}",
            size=sizes[cluster_id],
            mem_level="L1",
            chip_id=cur_chiplet_id,
            cluster_id=cluster_id,
        )
    return mem_handles


def create_dfg(params, mem_handles, platform, clear_after=None):
    """Cluster 0: copy 0 -> copy 1 -> ... -> host check; cluster 1: copy -> check."""
    bingo_dfg = BingoDFG(
        num_chiplets=platform["num_chiplets"],
        num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
        num_cores_per_cluster=platform["num_cores_per_cluster"],
        is_host_as_acc=True,
        chiplet_ids=platform["chiplet_ids"],
    )
    chunk = params["chunk_size"]
    if clear_after is not None and not 0 <= clear_after < params["chain_len"] - 1:
        raise ValueError("clear-after must name a copy with a successor")

    # Global task ids follow the order the nodes are added: the copies first
    prev = None
    copies = []
    for i in range(params["chain_len"]):
        task_copy = BingoNode(
            assigned_chiplet_id=cur_chiplet_id,
            assigned_cluster_id=0,
            assigned_core_id=DMA_CORE_ID,
            kernel_name="__snax_bingo_kernel_idma_1d_copy",
            kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                src_addr=BingoMemSymbol("A_l3", offset=i * chunk),
                dst_addr=mem_handles["A_L1_buf_0"].view(i * chunk),
                size=chunk,
            ),
        )
        bingo_dfg.bingo_add_node(task_copy)
        copies.append(task_copy)
        if prev is not None:
            bingo_dfg.bingo_add_edge(prev, task_copy)
        prev = task_copy

    checks = [(prev, "A_chain_cluster0", mem_handles["A_L1_buf_0"], params["A_size"])]
    task_copy = BingoNode(
        assigned_chiplet_id=cur_chiplet_id,
        assigned_cluster_id=1,
        assigned_core_id=DMA_CORE_ID,
        kernel_name="__snax_bingo_kernel_idma_1d_copy",
        kernel_args=SnaxBingoKernelIdma1dCopyArgs(
            src_addr=mem_handles["A_data_L3_symbol"],
            dst_addr=mem_handles["A_L1_buf_1"],
            size=chunk,
        ),
    )
    bingo_dfg.bingo_add_node(task_copy)
    checks.append((task_copy, "A_cluster1", mem_handles["A_L1_buf_1"], chunk))

    if clear_after is not None:
        clear = BingoNode(
            assigned_chiplet_id=cur_chiplet_id,
            assigned_cluster_id=HOST_CLUSTER_ID,
            assigned_core_id=HOST_CORE_ID,
            kernel_name="__host_bingo_kernel_risk_clear",
            kernel_args=HostBingoKernelDummyArgs(dummy_input=1 << DMA_CORE_ID),
        )
        bingo_dfg.bingo_add_node(clear)
        bingo_dfg.remove_edge(copies[clear_after], copies[clear_after + 1])
        bingo_dfg.bingo_add_edge(copies[clear_after], clear)
        bingo_dfg.bingo_add_edge(clear, copies[clear_after + 1])

    for pred, name, buf, size in checks:
        task_check = BingoNode(
            assigned_chiplet_id=cur_chiplet_id,
            assigned_cluster_id=HOST_CLUSTER_ID,
            assigned_core_id=HOST_CORE_ID,
            kernel_name="__host_bingo_kernel_check_result",
            kernel_args=HostBingoKernelCheckResultArgs(
                name=name,
                golden_data_addr=mem_handles["A_data_L3_symbol"],
                output_data_addr=buf,
                data_size=size,
            ),
        )
        bingo_dfg.bingo_add_node(task_check)
        bingo_dfg.bingo_add_edge(pred, task_check)

    return bingo_dfg


def main():
    args = get_args()
    output_dir = args.output_dir
    output_file_name = args.output_offload_file_name
    print(f"Output DIR: {output_dir}")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    params, merged_config = define_workload_params(args.cfg, args.hwcfg)

    if args.data_h is not None:
        with open(args.data_h, "w") as f:
            f.write(emit_header_file(**merged_config))
        print(f"Written data header: {args.data_h}")

    mem_handles = define_memory_handles(params)
    platform = parse_platform_cfg(args.platformcfg)
    if not guard_cluster_count(
        merged_config, platform, args.output_dir, args.output_offload_file_name
    ):
        return
    dfg = create_dfg(params, mem_handles, platform, args.clear_after)
    data_header = (
        os.path.basename(args.data_h) if args.data_h is not None else "dma_chain_data.h"
    )
    dfg.bingo_compile_dfg(
        APP_NAME, output_dir, output_file_name, extra_include_header_list=[data_header]
    )


if __name__ == "__main__":
    main()
