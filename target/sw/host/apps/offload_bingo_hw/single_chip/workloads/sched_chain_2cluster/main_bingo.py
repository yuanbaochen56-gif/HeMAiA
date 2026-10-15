# Copyright 2025 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# Scheduling micro-benchmark of the bingo hardware manager: N iDMA copies of
# chunk_size bytes from an L3 vector into cluster L1, either
#   chain    (--parallel 0): copy i moves chunk i into the cluster-0 L1 buffer and
#            depends on copy i - 1, all on the cluster-0 DM core; cluster 1 copies
#            chunk 0 into its own L1 at the same time (as dma_chain_2cluster);
#   parallel (--parallel 1): the copies are independent, copy i runs on the DM
#            core of cluster i % 2 and moves chunk i to slot i // 2 of that
#            cluster's buffer.
# The host checks every cluster buffer after its last copy. The software runtime
# runs the same graph (offload_bingo_sw/single_chip/workloads/sched_chain_2cluster),
# so the two schedulers can be compared on the same copies.
import os
import sys
import argparse
import pathlib

current_dir = os.path.dirname(os.path.abspath(__file__))
WORKLOADS_DIR = os.path.dirname(current_dir)
sys.path.append(WORKLOADS_DIR)
ROOT_DIR = os.path.abspath(os.path.join(current_dir, "../../../../../../../../"))
ROOT_DIR = os.path.normpath(ROOT_DIR)
APP_NAME = "Single-Chip Scheduling Benchmark 2-Cluster"

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
        help="Output path for the generated data header (e.g. sched_chain_data.h).",
    )
    parser.add_argument("--copies", type=int, default=4, help="number of copies N")
    parser.add_argument("--parallel", type=int, default=0,
                        help="1: independent copies on both clusters; 0: one chain on cluster 0")
    return parser.parse_args()


def define_workload_params(cfg_path, _hwcfg_path, copies, parallel):
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

    for name in ("num_clusters", "chunk_size"):
        if name not in params:
            raise KeyError(f"params.hjson must define {name}")
    if not 1 <= copies <= 32 or parallel not in (0, 1):
        raise ValueError("copies must be 1..32 and parallel 0 or 1")
    params.update(copies=copies, parallel=parallel)
    params["A_size"] = copies * params["chunk_size"]
    return params, dict(params)


def placement(params):
    """(cluster, slot in that cluster's buffer) of every copy."""
    if params["parallel"]:
        return [(i % 2, i // 2) for i in range(params["copies"])]
    return [(0, i) for i in range(params["copies"])]


def pattern(index):
    return (7 * index + index // 256) & 0xFF


def emit_header_file(**kwargs):
    chunk = kwargs["chunk_size"]
    a_data = [pattern(idx) for idx in range(kwargs["A_size"])]
    data_str = [
        "#include <stdint.h>",
        format_scalar_definition("uint32_t", "A_size", kwargs["A_size"]),
        format_vector_definition("uint8_t", "A_l3", a_data),
    ]
    # golden content of each cluster buffer
    for cluster in range(2):
        golden = []
        for i, (c, _) in enumerate(placement(kwargs)):
            if c == cluster:
                golden += a_data[i * chunk:(i + 1) * chunk]
        if not kwargs["parallel"] and cluster == 1:
            golden = a_data[:chunk]
        data_str.append(format_vector_definition("uint8_t", f"G{cluster}_l3", golden))
    return "\n\n".join(data_str) + "\n"


def buffer_sizes(params):
    chunk = params["chunk_size"]
    used = [sum(1 for c, _ in placement(params) if c == cluster) for cluster in range(2)]
    if not params["parallel"]:
        used[1] = 1
    return [n * chunk for n in used]


def define_memory_handles(params):
    """L3 source symbol and one L1 buffer per cluster."""
    mem_handles = {"A_data_L3_symbol": BingoMemSymbol("A_l3", offset=0)}
    for cluster_id, size in enumerate(buffer_sizes(params)):
        mem_handles[f"A_L1_buf_{cluster_id}"] = BingoMemAlloc(
            f"A_L1_buf_{cluster_id}",
            size=size,
            mem_level="L1",
            chip_id=cur_chiplet_id,
            cluster_id=cluster_id,
        )
    return mem_handles


def copy_node(cluster, src_offset, dst_handle, dst_offset, size):
    return BingoNode(
        assigned_chiplet_id=cur_chiplet_id,
        assigned_cluster_id=cluster,
        assigned_core_id=DMA_CORE_ID,
        kernel_name="__snax_bingo_kernel_idma_1d_copy",
        kernel_args=SnaxBingoKernelIdma1dCopyArgs(
            src_addr=BingoMemSymbol("A_l3", offset=src_offset),
            dst_addr=dst_handle.view(dst_offset),
            size=size,
        ),
    )


def create_dfg(params, mem_handles, platform):
    bingo_dfg = BingoDFG(
        num_chiplets=platform["num_chiplets"],
        num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
        num_cores_per_cluster=platform["num_cores_per_cluster"],
        is_host_as_acc=True,
        chiplet_ids=platform["chiplet_ids"],
    )
    chunk = params["chunk_size"]
    # Global task ids follow the order the nodes are added: the copies first
    last = {0: [], 1: []}
    prev = None
    for i, (cluster, slot) in enumerate(placement(params)):
        node = copy_node(cluster, i * chunk, mem_handles[f"A_L1_buf_{cluster}"], slot * chunk, chunk)
        bingo_dfg.bingo_add_node(node)
        if not params["parallel"]:
            if prev is not None:
                bingo_dfg.bingo_add_edge(prev, node)
            prev = node
            last[0] = [node]
        else:
            last[cluster].append(node)
    if not params["parallel"]:
        node = copy_node(1, 0, mem_handles["A_L1_buf_1"], 0, chunk)
        bingo_dfg.bingo_add_node(node)
        last[1] = [node]

    sizes = buffer_sizes(params)
    for cluster in range(2):
        if not last[cluster]:
            continue
        task_check = BingoNode(
            assigned_chiplet_id=cur_chiplet_id,
            assigned_cluster_id=HOST_CLUSTER_ID,
            assigned_core_id=HOST_CORE_ID,
            kernel_name="__host_bingo_kernel_check_result",
            kernel_args=HostBingoKernelCheckResultArgs(
                name=f"sched_cluster{cluster}",
                golden_data_addr=BingoMemSymbol(f"G{cluster}_l3", offset=0),
                output_data_addr=mem_handles[f"A_L1_buf_{cluster}"],
                data_size=sizes[cluster],
            ),
        )
        bingo_dfg.bingo_add_node(task_check)
        for pred in last[cluster]:
            bingo_dfg.bingo_add_edge(pred, task_check)

    return bingo_dfg


def main():
    args = get_args()
    output_dir = args.output_dir
    output_file_name = args.output_offload_file_name
    print(f"Output DIR: {output_dir}")

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    params, merged_config = define_workload_params(args.cfg, args.hwcfg, args.copies, args.parallel)

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
    dfg = create_dfg(params, mem_handles, platform)
    data_header = (
        os.path.basename(args.data_h) if args.data_h is not None else "sched_chain_data.h"
    )
    dfg.bingo_compile_dfg(
        APP_NAME, output_dir, output_file_name, extra_include_header_list=[data_header]
    )


if __name__ == "__main__":
    main()
