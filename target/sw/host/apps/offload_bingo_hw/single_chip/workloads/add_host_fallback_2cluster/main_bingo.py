#!/usr/bin/env python3
"""Out-of-place int32 add, with an automatic same-output host fallback.

Inputs and output are in L3. Golden values avoid signed overflow; pre-kernel
faults only, not late writes or partial writes after fencing.
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
from bingo_kernel_args import HostBingoKernelCheckResultArgs, SnaxBingoKernelInt32AddArgs
from data_utils import format_vector_definition


def vectors(count):
    a = [(-2147480000, 2147480000, -700, 700)[i % 4] + i % 97 for i in range(count)]
    b = [(i * 17) % 501 - 250 for i in range(count)]
    golden = [x + y for x, y in zip(a, b)]
    if not all(-2147483648 <= value <= 2147483647 for value in a + b + golden):
        raise ValueError("int32 test data must not overflow")
    return a, b, golden


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
    if not guard_cluster_count(params, platform, args.output_dir, args.output_offload_file_name):
        return
    if platform["num_chiplets"] != 1 or platform["num_cores_per_cluster"] != 2:
        raise ValueError("expects one hemaia_ci chiplet (accelerator, DM core, host slot)")
    count = params["num_elements"]
    if type(count) is not int or count <= 0:
        raise ValueError("num_elements must be a positive integer")
    data = vectors(count)
    if args.data_h:
        args.data_h.write_text("#include <stdint.h>\n\n" + "\n\n".join(
            format_vector_definition("int32_t", name, values)
            for name, values in zip(("A_l3", "B_l3", "C_golden"), data)) + "\n")
    dfg = BingoDFG(1, 2, 2, True, platform["chiplet_ids"],
                   core_type_ids=parse_bingo_core_type_ids(args.platformcfg))
    a, b = BingoMemSymbol("A_l3"), BingoMemSymbol("B_l3")
    c = BingoMemAlloc("C_l3", 4 * count, "L3", chip_id=0)
    add = BingoNode(0, 0, 1, node_name="add",
                    kernel_name="__snax_bingo_kernel_int32_add",
                    kernel_args=SnaxBingoKernelInt32AddArgs(a, b, c, count))
    check = BingoNode(0, 0, 2, node_name="check",
                      kernel_name="__host_bingo_kernel_check_result",
                      kernel_args=HostBingoKernelCheckResultArgs(
                          golden_data_addr=BingoMemSymbol("C_golden"), output_data_addr=c,
                          data_size=4 * count, name="C_l3"))
    dfg.bingo_add_node(add)
    dfg.bingo_add_node(check)
    dfg.bingo_add_edge(add, check)
    dfg.bingo_add_host_fallback(add)
    dfg.bingo_compile_dfg(
        app_name="Int32 add (automatic host fallback)", output_dir=str(args.output_dir),
        output_file_name=args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name if args.data_h else "add_host_fallback_data.h"],
        post_execute_code=[
            "{",
            "    uint32_t __branch = !!(bingo_cerf_fb_evt() & (1u << BINGO_CORE_TYPE_ID(0, 1)));",
            '    printf_safe("[AddHostFallback] check complete; host fallback %d\\r\\n", __branch);',
            "}",
        ])


if __name__ == "__main__":
    main()
