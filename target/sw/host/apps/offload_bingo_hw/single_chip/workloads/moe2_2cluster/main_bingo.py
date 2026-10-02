#!/usr/bin/env python3
"""Two experts, top-1, with a compiler-generated g0 -> g1 fallback.

router -> gating -> expert 0 (cluster 0, g0) -> expert 1 (cluster 1, g1)
                                                 -> host join -> exits

The compiler orders the backup's loads after expert 0's final store and
places the fault core's exit in g0, so the shared waiting queue can drain.
"""

import argparse
import pathlib
import sys

import hjson
import numpy as np

CURRENT_DIR = pathlib.Path(__file__).resolve().parent
ROOT_DIR = CURRENT_DIR.parents[7]
sys.path.append(str(ROOT_DIR / "target/sw/host/runtime/libbingo/mini_compiler"))

from moe_datagen import generate_moe_data, emit_header_file
from bingo_dfg import BingoDFG
from bingo_platform import guard_cluster_count, parse_bingo_core_type_ids, parse_platform_cfg
from bingo_node import BingoNode
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_kernel_args import (
    BINGO_GATING_MODE_TOP_K,
    HostBingoKernelAraSoftmaxF32Args,
    HostBingoKernelCerfGatingArgs,
    HostBingoKernelDummyArgs,
    SnaxBingoKernelGemmFullArgs,
    SnaxBingoKernelIdma1dCopyArgs,
)


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
    param = hjson.loads(args.cfg.read_text())
    if not guard_cluster_count(param, parse_platform_cfg(args.platformcfg),
                               args.output_dir, args.output_offload_file_name):
        return
    hw = hjson.loads(args.hwcfg.read_text())
    shape = param["array_shape"]
    unrolling = hw["snax_versacore_core_template"]["snax_acc_cfg"][0][
        "snax_versacore_spatial_unrolling"][0][shape]
    params = {**param, "meshRow": unrolling[0], "tileSize": unrolling[1],
              "meshCol": unrolling[2], "arrayShapeIdx": shape}
    a_size = param["M"] * param["K"] * unrolling[0] * unrolling[1]
    b_size = param["K"] * param["N"] * unrolling[1] * unrolling[2]
    d_size = param["M"] * param["N"] * unrolling[0] * unrolling[2] * 4
    # The A reader fetches at least 8 banks (64 B) per K step, but an A tile of
    # this shape is 16 B: the last step reads up to 64 B past it. Pad A with
    # zeros to cover that, otherwise those lanes read uninitialised L1 (X in
    # simulation, which reaches D). The golden is computed before the padding.
    a_alloc = -(-(a_size + 64) // 64) * 64
    data = generate_moe_data(params)
    data["input_A"] = np.concatenate([data["input_A"].flatten(),
                                      np.zeros(a_alloc - a_size, dtype=np.int8)])
    if args.data_h:
        emit_header_file(str(args.data_h), params, data)

    dfg = BingoDFG(num_chiplets=1, num_clusters_per_chiplet=2,
                   num_cores_per_cluster=2, is_host_as_acc=True, chiplet_ids=[0],
                   core_type_ids=parse_bingo_core_type_ids(args.platformcfg))
    logits = BingoMemAlloc("logits", 8, "L3")
    activation = BingoMemAlloc("router_activation", 2, "L3")
    router = BingoNode(0, 0, 2, node_name="router",
                       kernel_name="__host_bingo_kernel_softmax",
                       kernel_args=HostBingoKernelAraSoftmaxF32Args(
                           input_addr=BingoMemSymbol("router_logits", offset=0),
                           output_addr=logits, num_rows=1, row_length=2))
    dfg.bingo_add_node(router)
    gating = BingoNode(0, 0, 2, node_name="gating",
                       kernel_name="__host_bingo_kernel_cerf_gating",
                       kernel_args=HostBingoKernelCerfGatingArgs(
                           mode=BINGO_GATING_MODE_TOP_K, cerf_controlled_mask=3,
                           top_k_or_threshold=1,
                           cerf_group_ids_addr=BingoMemSymbol("cerf_group_ids", offset=0),
                           cond_activation_addr=activation))
    gating._pred_source_node = router
    gating.node_type = "gating"
    gating.cerf_write_groups = [0, 1]
    dfg.bingo_add_node(gating)
    dfg.bingo_add_edge(router, gating)

    branches = []
    for expert in range(2):
        l1_a = BingoMemAlloc(f"l1_A_{expert}", a_alloc, "L1", chip_id=0, cluster_id=expert)
        l1_b = BingoMemAlloc(f"l1_B_{expert}", b_size, "L1", chip_id=0, cluster_id=expert)
        l1_d = BingoMemAlloc(f"l1_D_{expert}", d_size, "L1", chip_id=0, cluster_id=expert)
        output = BingoMemAlloc(f"D_L3_{expert}", d_size, "L3")
        ld_a = BingoNode(0, expert, 1, node_name=f"e{expert}_ldA",
                         kernel_name="__snax_bingo_kernel_idma_1d_copy",
                         kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                             src_addr=BingoMemSymbol("input_A", offset=0),
                             dst_addr=l1_a, size=a_alloc))
        ld_b = BingoNode(0, expert, 1, node_name=f"e{expert}_ldB",
                         kernel_name="__snax_bingo_kernel_idma_1d_copy",
                         kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                             src_addr=BingoMemSymbol(f"expert_{expert}_B", offset=0),
                             dst_addr=l1_b, size=b_size))
        gemm = BingoNode(0, expert, 0, node_name=f"e{expert}_gemm",
                         kernel_name="__snax_bingo_kernel_gemm_full",
                         kernel_args=SnaxBingoKernelGemmFullArgs(
                             input_A_addr=l1_a, input_B_addr=l1_b,
                             input_C_addr=0, output_D_addr=l1_d,
                             M=param["M"], K=param["K"], N=param["N"],
                             array_shape_idx=shape, transpose_A=0, transpose_B=0,
                             accumPrevC=0))
        store = BingoNode(0, expert, 1, node_name=f"e{expert}_stD",
                          kernel_name="__snax_bingo_kernel_idma_1d_copy",
                          kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                              src_addr=l1_d, dst_addr=output, size=d_size))
        for node in (ld_a, ld_b, gemm, store):
            node.cond_exec_en = True
            node.cond_exec_group_id = expert
            dfg.bingo_add_node(node)
        dfg.bingo_add_edge(gating, ld_a)
        dfg.bingo_add_edge(gating, ld_b)
        dfg.bingo_add_edge(ld_a, gemm)
        dfg.bingo_add_edge(ld_b, gemm)
        dfg.bingo_add_edge(gemm, store)
        branches.append((ld_a, ld_b, gemm, store))

    dfg.bingo_add_cerf_fallback(branches[0][2], branches[1][2])

    join = BingoNode(0, 0, 2, node_name="join",
                     kernel_name="__host_bingo_kernel_dummy",
                     kernel_args=HostBingoKernelDummyArgs(dummy_input=0))
    dfg.bingo_add_node(join)
    for branch in branches:
        dfg.bingo_add_edge(branch[-1], join)

    post_check = [
        "{",
        "    uint32_t __evt = bingo_cerf_fb_evt();",
        "    uint32_t __type = BINGO_CORE_TYPE_ID(0, 0);",
        "    uint32_t __expert = (__evt & (1u << __type)) ? 1 : 0;",
        "    uint32_t __err = 0;",
        "#ifdef BINGO_MOE_EXPECT_EXPERT",
        "    if (__expert != BINGO_MOE_EXPECT_EXPERT) __err++;",
        "#endif",
        "    float* __logits = (float*)ptr_logits;",
        "    float* __sm = (float*)(uintptr_t)chiplet_addr_transform((uint64_t)(uintptr_t)softmax_golden);",
        "    for (int __i = 0; __i < 2; __i++) {",
        "        float __diff = __logits[__i] - __sm[__i];",
        "        if (__diff < 0) __diff = -__diff;",
        "        if (!(__diff < 1e-5f)) __err++;",
        "    }",
        "    uint8_t* __activation = (uint8_t*)ptr_router_activation;",
        "    if (__activation[0] != 1 || __activation[1] != 0) __err++;",
        '    printf_safe("[MoE2] router selected expert 0; join complete; output expert %d\\r\\n", __expert);',
        "    uint8_t* __golden[] = {",
        "        (uint8_t*)(uintptr_t)chiplet_addr_transform((uint64_t)(uintptr_t)expert_0_D_golden),",
        "        (uint8_t*)(uintptr_t)chiplet_addr_transform((uint64_t)(uintptr_t)expert_1_D_golden)};",
        "    uint8_t* __output[] = {(uint8_t*)ptr_D_L3_0, (uint8_t*)ptr_D_L3_1};",
        f"    for (uint32_t __i = 0; __i < {d_size}; __i++)",
        "        if (__output[__expert][__i] != __golden[__expert][__i]) __err++;",
        '    printf_safe("[Host] Check [expert_%d_D]: %s\\r\\n", __expert, __err ? "FAIL" : "PASS");',
        "    if (__err) return 1;",
        "}",
    ]
    dfg.bingo_compile_dfg(
        app_name="MoE2 (top-1, ordered fallback)", output_dir=str(args.output_dir),
        output_file_name=args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name] if args.data_h else None,
        post_execute_code=post_check)


if __name__ == "__main__":
    main()
