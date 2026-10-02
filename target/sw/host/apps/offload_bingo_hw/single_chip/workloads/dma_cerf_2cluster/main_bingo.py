#!/usr/bin/env python3
"""iDMA copies on the cluster-0 DM core, with a compiler-generated host fallback.

host gating (g0 only) -> copy 0 -> copy 1 (cluster-0 DM core, g0)
                      -> host iDMA of the whole vector (g1) -> host join -> exits

The cluster-1 DM core, the only possible substitute, runs nothing but its exit,
which the compiler puts into g0 as well. When both DM cores die on copy 0, the
DM core type is stuck and the CERF switches to the host copy.
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
from bingo_kernel_args import (
    BINGO_GATING_MODE_STATIC,
    HostBingoKernelCerfGatingArgs,
    HostBingoKernelDummyArgs,
    HostBingoKernelIdmaArgs,
    SnaxBingoKernelIdma1dCopyArgs,
)
from data_utils import format_scalar_definition, format_vector_definition

DMA_CORE_ID = 1
HOST_CORE_ID = 2


def emit_data(size):
    return "\n\n".join([
        "#include <stdint.h>",
        format_scalar_definition("uint32_t", "A_size", size),
        format_vector_definition("uint8_t", "A_l3", [(7 * i + 3) & 0xFF for i in range(size)]),
    ]) + "\n"


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
    chain_len, chunk = params["chain_len"], params["chunk_size"]
    if chain_len <= 0 or chunk <= 0:
        raise ValueError("chain_len and chunk_size must be positive")
    size = chain_len * chunk
    if args.data_h:
        args.data_h.write_text(emit_data(size))

    dfg = BingoDFG(
        num_chiplets=1, num_clusters_per_chiplet=2, num_cores_per_cluster=2,
        is_host_as_acc=True, chiplet_ids=platform["chiplet_ids"],
        core_type_ids=parse_bingo_core_type_ids(args.platformcfg))
    outputs = [BingoMemAlloc(f"A_L1_{branch}", size, "L1", chip_id=0, cluster_id=0)
               for branch in range(2)]

    # Global task ids follow the order the nodes are added: gating 0, copies 1..
    gating = BingoNode(0, 0, HOST_CORE_ID, node_name="gating",
                       kernel_name="__host_bingo_kernel_cerf_gating",
                       kernel_args=HostBingoKernelCerfGatingArgs(
                           mode=BINGO_GATING_MODE_STATIC, cerf_controlled_mask=3,
                           top_k_or_threshold=1))
    gating.node_type = "gating"
    gating.cerf_write_groups = [0, 1]
    dfg.bingo_add_node(gating)

    prev = gating
    for i in range(chain_len):
        copy = BingoNode(0, 0, DMA_CORE_ID, node_name=f"copy_{i}",
                         kernel_name="__snax_bingo_kernel_idma_1d_copy",
                         kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                             src_addr=BingoMemSymbol("A_l3", offset=i * chunk),
                             dst_addr=outputs[0].view(i * chunk), size=chunk))
        copy.cond_exec_en = True
        copy.cond_exec_group_id = 0
        dfg.bingo_add_node(copy)
        dfg.bingo_add_edge(prev, copy)
        if i == 0:
            first_copy = copy
        prev = copy
    last_copy = prev

    host_copy = BingoNode(0, 0, HOST_CORE_ID, node_name="host_copy",
                          kernel_name="__host_bingo_kernel_idma",
                          kernel_args=HostBingoKernelIdmaArgs(
                              src_addr=BingoMemSymbol("A_l3", offset=0),
                              dst_addr=outputs[1], size=size))
    host_copy.cond_exec_en = True
    host_copy.cond_exec_group_id = 1
    dfg.bingo_add_node(host_copy)
    dfg.bingo_add_edge(gating, host_copy)
    dfg.bingo_add_cerf_fallback(first_copy, host_copy)

    join = BingoNode(0, 0, HOST_CORE_ID, node_name="join",
                     kernel_name="__host_bingo_kernel_dummy",
                     kernel_args=HostBingoKernelDummyArgs(dummy_input=0))
    dfg.bingo_add_node(join)
    dfg.bingo_add_edge(last_copy, join)
    dfg.bingo_add_edge(host_copy, join)

    post_check = [
        "{",
        "    uint32_t __evt = bingo_cerf_fb_evt();",
        f"    uint32_t __type = BINGO_CORE_TYPE_ID(0, {DMA_CORE_ID});",
        "    uint32_t __branch = (__evt & (1u << __type)) ? 1 : 0;",
        "    uint32_t __err = 0;",
        "#ifdef BINGO_DMA_EXPECT_BRANCH",
        "    if (__branch != BINGO_DMA_EXPECT_BRANCH) __err++;",
        "#endif",
        "    uint8_t* __golden = (uint8_t*)(uintptr_t)chiplet_addr_transform(",
        "        (uint64_t)(uintptr_t)A_l3);",
        "    uint8_t* __output[] = {(uint8_t*)ptr_A_L1_0, (uint8_t*)ptr_A_L1_1};",
        f"    for (uint32_t __i = 0; __i < {size}; __i++)",
        "        if (__output[__branch][__i] != __golden[__i]) __err++;",
        '    printf_safe("[DmaCERF] gating selected g0; join complete; output branch %d\\r\\n", __branch);',
        '    printf_safe("[Host] Check [A_branch_%d]: %s\\r\\n", __branch, __err ? "FAIL" : "PASS");',
        "    if (__err) return 1;",
        "}",
    ]
    dfg.bingo_compile_dfg(
        app_name="DMA copies (host CERF fallback)", output_dir=str(args.output_dir),
        output_file_name=args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name if args.data_h else "dma_cerf_data.h"],
        post_execute_code=post_check)


if __name__ == "__main__":
    main()
