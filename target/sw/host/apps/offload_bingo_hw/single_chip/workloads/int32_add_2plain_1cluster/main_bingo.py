# Copyright 2025 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
#
# int32 add split over the two plain compute cores of one
# snax_versacore_2plain_to_cluster (target/rtl/cfg/cluster):
#
#   DM core     : iDMA A and B from L3 into L1
#   plain core 1: C[0 : n/2] = A + B
#   plain core 2: C[n/2 : n] = A + B
#   host        : check C against the golden data
#
# The two plain cores have the same type, so when one of them dies the bingo HW
# manager replays its task on the other and the check still passes.
import os
import sys
import argparse
import pathlib

current_dir = os.path.dirname(os.path.abspath(__file__))
WORKLOADS_DIR = os.path.dirname(current_dir)
sys.path.append(WORKLOADS_DIR)
ROOT_DIR = os.path.abspath(os.path.join(current_dir, "../../../../../../../../"))
ROOT_DIR = os.path.normpath(ROOT_DIR)
APP_NAME = "Single-Chip int32 Add on 2 Plain Cores"

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
    SnaxBingoKernelInt32AddArgs,
    HostBingoKernelCheckResultArgs,
)
from data_utils import format_scalar_definition, format_vector_definition  # noqa: E402

cur_chiplet_id = 0
cur_cluster_id = 0

# Core layout of snax_versacore_2plain_to_cluster
PLAIN_CORE_IDS = (1, 2)
NUM_SNITCH_CORES = 4
DMA_CORE_ID = NUM_SNITCH_CORES - 1
HOST_CORE_ID = NUM_SNITCH_CORES  # the host is the extra bingo slot of cluster 0


def get_args():
    parser = argparse.ArgumentParser(description="Bingo HW Manager")
    parser.add_argument("--output_dir", type=str, default=".",
                        help="Output directory for generated files")
    parser.add_argument("--output_offload_file_name", type=str, default="offload_bingo_hw.h",
                        help="Output filename for the offload header file")
    parser.add_argument("-c", "--cfg", type=pathlib.Path, required=True,
                        help="Select param config file (params.hjson)")
    parser.add_argument("--hwcfg", type=pathlib.Path, required=True,
                        help="Select hardware config file")
    parser.add_argument("--platformcfg", type=pathlib.Path, required=True,
                        help="Path to generated occamy.h with HW platform defines")
    parser.add_argument("--data_h", type=pathlib.Path, default=None,
                        help="Output path for the generated data header.")
    return parser.parse_args()


def define_workload_params(cfg_path):
    params = {}
    with open(cfg_path) as f:
        for line in f:
            line = line.split("//", 1)[0].split("#", 1)[0].strip()
            if not line or line in ("{", "}"):
                continue
            key, sep, value = line.partition(":")
            if not sep:
                continue
            params[key.strip()] = int(value.strip().rstrip(","), 0)

    for name in ("num_clusters", "num_elements"):
        if name not in params:
            raise KeyError(f"params.hjson must define {name}")
    if params["num_elements"] % 2:
        raise ValueError("num_elements must be even (split over two cores)")
    return params


def golden_data(num_elements):
    a = [i for i in range(num_elements)]
    b = [3 * i + 7 for i in range(num_elements)]
    c = [x + y for x, y in zip(a, b)]
    return a, b, c


def emit_header_file(num_elements):
    a, b, c = golden_data(num_elements)
    data_str = [
        "#include <stdint.h>",
        format_scalar_definition("uint32_t", "num_elements", num_elements),
        format_vector_definition("int32_t", "A_l3", a),
        format_vector_definition("int32_t", "B_l3", b),
        format_vector_definition("int32_t", "C_golden_l3", c),
    ]
    return "\n\n".join(data_str) + "\n"


def define_memory_handles(params):
    size = params["num_elements"] * 4
    mem_handles = {
        "A_l3": BingoMemSymbol("A_l3", offset=0),
        "B_l3": BingoMemSymbol("B_l3", offset=0),
        "C_golden_l3": BingoMemSymbol("C_golden_l3", offset=0),
    }
    for name in ("A_L1", "B_L1", "C_L1"):
        mem_handles[name] = BingoMemAlloc(name, size=size, mem_level="L1",
                                          chip_id=cur_chiplet_id, cluster_id=cur_cluster_id)
    return mem_handles


def create_dfg(params, mem_handles, platform):
    if platform["num_cores_per_cluster"] != NUM_SNITCH_CORES:
        raise ValueError(f"expects {NUM_SNITCH_CORES} Snitch cores per cluster "
                         f"(snax_versacore_2plain_to_cluster), platform has "
                         f"{platform['num_cores_per_cluster']}")
    bingo_dfg = BingoDFG(
        num_chiplets=platform["num_chiplets"],
        num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
        num_cores_per_cluster=platform["num_cores_per_cluster"],
        is_host_as_acc=True,
        chiplet_ids=platform["chiplet_ids"],
    )
    num_elements = params["num_elements"]
    size = num_elements * 4

    loads = []
    for src, dst in (("A_l3", "A_L1"), ("B_l3", "B_L1")):
        loads.append(BingoNode(
            assigned_chiplet_id=cur_chiplet_id,
            assigned_cluster_id=cur_cluster_id,
            assigned_core_id=DMA_CORE_ID,
            kernel_name="__snax_bingo_kernel_idma_1d_copy",
            kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                src_addr=mem_handles[src], dst_addr=mem_handles[dst], size=size),
        ))

    half = num_elements // 2
    adds = []
    for part, core_id in enumerate(PLAIN_CORE_IDS):
        offset = part * half * 4
        adds.append(BingoNode(
            assigned_chiplet_id=cur_chiplet_id,
            assigned_cluster_id=cur_cluster_id,
            assigned_core_id=core_id,
            kernel_args=SnaxBingoKernelInt32AddArgs(
                a_addr=mem_handles["A_L1"].view(offset),
                b_addr=mem_handles["B_L1"].view(offset),
                c_addr=mem_handles["C_L1"].view(offset),
                num_elements=half),
        ))

    check = BingoNode(
        assigned_chiplet_id=cur_chiplet_id,
        assigned_cluster_id=cur_cluster_id,
        assigned_core_id=HOST_CORE_ID,
        kernel_name="__host_bingo_kernel_check_result",
        kernel_args=HostBingoKernelCheckResultArgs(
            name="C",
            golden_data_addr=mem_handles["C_golden_l3"],
            output_data_addr=mem_handles["C_L1"],
            data_size=size),
    )

    for node in loads + adds + [check]:
        bingo_dfg.bingo_add_node(node)
    for add in adds:
        for load in loads:
            bingo_dfg.bingo_add_edge(load, add)
        bingo_dfg.bingo_add_edge(add, check)
    return bingo_dfg


def main():
    args = get_args()
    output_dir = args.output_dir
    output_file_name = args.output_offload_file_name
    os.makedirs(output_dir, exist_ok=True)

    params = define_workload_params(args.cfg)
    if args.data_h is not None:
        with open(args.data_h, "w") as f:
            f.write(emit_header_file(params["num_elements"]))
        print(f"Written data header: {args.data_h}")

    mem_handles = define_memory_handles(params)
    platform = parse_platform_cfg(args.platformcfg)
    if not guard_cluster_count(params, platform, output_dir, output_file_name):
        return
    dfg = create_dfg(params, mem_handles, platform)
    data_header = os.path.basename(args.data_h) if args.data_h is not None else "int32_add_data.h"
    dfg.bingo_compile_dfg(APP_NAME, output_dir, output_file_name,
                          extra_include_header_list=[data_header])


if __name__ == "__main__":
    main()
