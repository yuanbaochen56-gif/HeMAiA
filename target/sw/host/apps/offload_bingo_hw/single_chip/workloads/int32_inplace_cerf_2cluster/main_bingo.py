#!/usr/bin/env python3
"""An in-place DM add, protected by a separate-output host CERF branch."""

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
from bingo_kernel_args import (
    BINGO_GATING_MODE_STATIC, HostBingoKernelCerfGatingArgs,
    HostBingoKernelAraAddI32Args, HostBingoKernelCheckResultArgs,
    SnaxBingoKernelIdma1dCopyArgs, SnaxBingoKernelInt32AddArgs,
)
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_node import BingoNode
from bingo_platform import guard_cluster_count, parse_bingo_core_type_ids, parse_platform_cfg
from data_utils import format_scalar_definition, format_vector_definition


def emit_data(count, unsafe):
    initial = [5 * i - 23 for i in range(count)]
    delta = [3 + (i % 7) for i in range(count)]
    once = [a + b for a, b in zip(initial, delta)]
    expected = [a + (2 if unsafe else 1) * b for a, b in zip(initial, delta)]
    return "\n\n".join([
        "#include <stdint.h>",
        format_scalar_definition("uint32_t", "num_elements", count),
        format_vector_definition("int32_t", "acc_init", initial),
        format_vector_definition("int32_t", "delta", delta),
        format_vector_definition("int32_t", "golden_once", once),
        format_vector_definition("int32_t", "golden_acc", expected),
    ]) + "\n"


def build_graph(platform, core_types, count, unsafe=False):
    dfg = BingoDFG(1, 2, 2, True, platform["chiplet_ids"],
                   core_type_ids=core_types, allow_unsafe_replay=unsafe)
    size = count * 4
    acc = BingoMemAlloc("acc", size, "L3", chip_id=0, cluster_id=0)
    bk = BingoMemAlloc("bk", size, "L3", chip_id=0, cluster_id=0)
    gating = BingoNode(0, 0, 2, node_name="gating",
        kernel_name="__host_bingo_kernel_cerf_gating",
        kernel_args=HostBingoKernelCerfGatingArgs(
            mode=BINGO_GATING_MODE_STATIC, cerf_controlled_mask=3, top_k_or_threshold=1))
    gating.node_type = "gating"
    gating.cerf_write_groups = [0, 1]
    copy = BingoNode(0, 0, 1, node_name="copy",
        kernel_name="__snax_bingo_kernel_idma_1d_copy",
        kernel_args=SnaxBingoKernelIdma1dCopyArgs(BingoMemSymbol("acc_init"), acc, size))
    add = BingoNode(0, 0, 1, node_name="inplace_add",
        kernel_args=SnaxBingoKernelInt32AddArgs(acc, BingoMemSymbol("delta"), acc, count))
    if unsafe:
        add.non_idempotent = False
    add.cond_exec_en = True
    add.cond_exec_group_id = 0
    # The existing int32 host kernel is named __host_bingo_kernel_add_i32.
    # Its arguments infer that existing name; no new host interface is needed.
    backup = BingoNode(0, 0, 2, node_name="backup",
        kernel_args=HostBingoKernelAraAddI32Args(
            BingoMemSymbol("acc_init"), BingoMemSymbol("delta"), bk, count))
    backup.cond_exec_en = True
    backup.cond_exec_group_id = 1
    check = BingoNode(0, 0, 2, node_name="check",
        kernel_name="__host_bingo_kernel_check_result",
        kernel_args=HostBingoKernelCheckResultArgs(
            golden_data_addr=BingoMemSymbol("golden_acc"),
            output_data_addr=acc, data_size=size, name="acc"))
    for node in (gating, copy, add, backup, check):
        dfg.bingo_add_node(node)
    for before, after in ((gating, copy), (copy, add), (copy, backup),
                          (add, check), (backup, check)):
        dfg.bingo_add_edge(before, after)
    dfg.bingo_add_cerf_fallback(add, backup)
    return dfg, {"gating": gating, "copy": copy, "add": add,
                 "backup": backup, "check": check}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output_dir", type=pathlib.Path, default=CURRENT_DIR)
    parser.add_argument("--output_offload_file_name", default="offload_bingo_hw.h")
    parser.add_argument("-c", "--cfg", type=pathlib.Path, required=True)
    parser.add_argument("--hwcfg", type=pathlib.Path, required=True)
    parser.add_argument("--platformcfg", type=pathlib.Path, required=True)
    parser.add_argument("--data_h", type=pathlib.Path)
    parser.add_argument("--allow-unsafe-replay", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    params = hjson.loads(args.cfg.read_text())
    platform = parse_platform_cfg(args.platformcfg)
    if not guard_cluster_count(params, platform, args.output_dir, args.output_offload_file_name):
        return
    if platform["num_chiplets"] != 1 or platform["num_cores_per_cluster"] != 2:
        raise ValueError("expects one hemaia_ci chiplet")
    count = params["num_elements"]
    if not isinstance(count, int) or count <= 0:
        raise ValueError("num_elements must be a positive integer")
    if args.data_h:
        args.data_h.write_text(emit_data(count, args.allow_unsafe_replay))
    dfg, _ = build_graph(platform, parse_bingo_core_type_ids(args.platformcfg),
                         count, args.allow_unsafe_replay)
    post = [
        "{",
        "    uint32_t __branch = !!(bingo_cerf_fb_evt() & (1u << BINGO_CORE_TYPE_ID(0, 1)));",
        "    uint32_t __blocked = bingo_replay_blocked(), __err = 0;",
        "    uint32_t __want_blocked = __branch ? (1u << 1) : 0;",
        "    if (__blocked != __want_blocked) __err++;",
        "#ifdef BINGO_INPLACE_EXPECT_BRANCH",
        "    if (__branch != BINGO_INPLACE_EXPECT_BRANCH) __err++;",
        "#endif",
        "    if (__branch) {",
        "        int32_t* __gold = (int32_t*)(uintptr_t)chiplet_addr_transform((uint64_t)(uintptr_t)golden_once);",
        "        int32_t* __bk = (int32_t*)ptr_bk;",
        f"        for (uint32_t __i = 0; __i < {count}; __i++)",
        "            if (__bk[__i] != __gold[__i]) __err++;",
        '        printf_safe("[Host] Check [bk]: %s\\r\\n", __err ? "FAIL" : "PASS");',
        "    }",
        '    printf_safe("[Int32Inplace] check complete; branch=%d unsafe=%d replay_blocked=0x%x\\r\\n",',
        f"                __branch, {int(args.allow_unsafe_replay)}, __blocked);",
        "    if (__err) return 1;",
        "}",
    ]
    dfg.bingo_compile_dfg(
        app_name="in-place int32 add (no replay, separate-output CERF backup)",
        output_dir=str(args.output_dir), output_file_name=args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name if args.data_h else "int32_inplace_data.h"],
        post_execute_code=post)


if __name__ == "__main__":
    main()
