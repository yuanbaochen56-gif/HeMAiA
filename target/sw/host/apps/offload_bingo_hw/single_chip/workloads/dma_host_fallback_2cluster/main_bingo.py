#!/usr/bin/env python3
"""One device iDMA copy, automatically protected by a host copy of the same output.

The cluster-1 DM core has only an exit. Faults are injected before the kernel;
partial DMA writes and late writes after fencing are outside this demonstration.
"""

import argparse
import pathlib
import sys

import hjson

CURRENT_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parents[7]
sys.path.append(str(ROOT_DIR / "target/sw/host/runtime/libbingo/mini_compiler"))
sys.path.append(str(ROOT_DIR / "util/sim"))
import _usg_paths  # noqa: F401,E402

from bingo_dfg import BingoDFG
from bingo_platform import guard_cluster_count, parse_bingo_core_type_ids, parse_platform_cfg
from bingo_node import BingoNode
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_kernel_args import HostBingoKernelCheckResultArgs, SnaxBingoKernelIdma1dCopyArgs
from data_utils import format_scalar_definition, format_vector_definition


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output_dir", type=pathlib.Path, default=CURRENT_DIR)
    parser.add_argument("--output_offload_file_name", default="offload_bingo_hw.h")
    parser.add_argument("-c", "--cfg", type=pathlib.Path, required=True)
    parser.add_argument("--hwcfg", type=pathlib.Path, required=True)
    parser.add_argument("--platformcfg", type=pathlib.Path, required=True)
    parser.add_argument("--data_h", type=pathlib.Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    params = hjson.loads(args.cfg.read_text())
    platform = parse_platform_cfg(args.platformcfg)
    if not guard_cluster_count(params, platform, args.output_dir,
                               args.output_offload_file_name):
        return
    if platform["num_chiplets"] != 1 or platform["num_cores_per_cluster"] != 2:
        raise ValueError("expects one hemaia_ci chiplet (accelerator, DM core, host slot)")
    size = params["size"]
    if not isinstance(size, int) or size <= 0:
        raise ValueError("size must be a positive integer")
    if args.data_h:
        args.data_h.write_text("\n\n".join([
            "#include <stdint.h>",
            format_scalar_definition("uint32_t", "A_size", size),
            format_vector_definition("uint8_t", "A_l3", [(7 * i + 3) & 0xFF for i in range(size)]),
        ]) + "\n")

    dfg = BingoDFG(
        num_chiplets=1, num_clusters_per_chiplet=2, num_cores_per_cluster=2,
        is_host_as_acc=True, chiplet_ids=platform["chiplet_ids"],
        core_type_ids=parse_bingo_core_type_ids(args.platformcfg))
    source = BingoMemSymbol("A_l3")
    output = BingoMemAlloc("A_L1", size, "L1", chip_id=0, cluster_id=0)
    copy = BingoNode(0, 0, 1, node_name="copy",
                     kernel_name="__snax_bingo_kernel_idma_1d_copy",
                     kernel_args=SnaxBingoKernelIdma1dCopyArgs(source, output, size))
    check = BingoNode(0, 0, 2, node_name="check",
                      kernel_name="__host_bingo_kernel_check_result",
                      kernel_args=HostBingoKernelCheckResultArgs(
                          golden_data_addr=source, output_data_addr=output,
                          data_size=size, name="A_L1"))
    dfg.bingo_add_node(copy)
    dfg.bingo_add_node(check)
    dfg.bingo_add_edge(copy, check)
    dfg.bingo_add_host_fallback(copy)
    dfg.bingo_compile_dfg(
        app_name="DMA copy (automatic host fallback)", output_dir=str(args.output_dir),
        output_file_name=args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name if args.data_h else "dma_host_fallback_data.h"],
        post_execute_code=[
            "{",
            "    uint32_t __branch = !!(bingo_cerf_fb_evt() & (1u << BINGO_CORE_TYPE_ID(0, 1)));",
            '    printf_safe("[DmaHostFallback] check complete; host fallback %d\\r\\n", __branch);',
            "#ifdef BINGO_DMA_EXPECT_BRANCH",
            "    if (__branch != BINGO_DMA_EXPECT_BRANCH) return 1;",
            "#endif",
            "}",
        ])


if __name__ == "__main__":
    main()
