#!/usr/bin/env python3
# Copyright 2026 KU Leuven.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0
"""
attn_spare — the reference attention workload with a spare cluster.

The same graph as attn_reference_4cluster (same stages, same K-split of every
GEMM into four chunks, same host reduction, same data and checks), but K-chunk
k of every GEMM runs on cluster chunk_cluster[k] (params.hjson: [0, 1, 2, 0]):
clusters 0-2 do all the work and cluster 3 gets no task, so it can stand by as
a cold spare for the bingo HW manager (sub_cfg_i.spare). A cluster with two
chunks has a buffer set per chunk; the partial sums are still added in chunk
order, so the results are those of the reference.

All pattern helpers except the K-split GEMM come from attn_reference_4cluster.
"""

import importlib.util
import os
import pathlib
import sys

import hjson

current_dir = os.path.dirname(os.path.abspath(__file__))
REFERENCE_DIR = os.path.join(os.path.dirname(current_dir), "attn_reference_4cluster")
_spec = importlib.util.spec_from_file_location("attn_reference", os.path.join(REFERENCE_DIR, "main_bingo.py"))
ref = importlib.util.module_from_spec(_spec)
sys.modules["attn_reference"] = ref   # its dataclasses look their module up
_spec.loader.exec_module(ref)

BingoNode = ref.BingoNode
DMA_CORE, GEMM_CORE, HOST_CORE = ref.DMA_CORE, ref.GEMM_CORE, ref.HOST_CORE


def emit_ksplit_gemm_by_chunk(dfg, mem, hw, spec, chunk_cluster, *, debug=True, input_dep=None,
                              final_check=False):
    """ref.emit_ksplit_gemm_pattern with K-chunk k on cluster chunk_cluster[k]:
    per chunk, load A and B into that cluster's L1, GEMM, store the partial;
    then the host adds the partials in chunk order, dequantizes, and the DM
    core of cluster 0 converts the result to row-major. The names of the
    golden symbols are those of the reference (chunk k is its cluster k)."""
    name = spec.name
    M_T, N_T = spec.M_T, spec.N_T
    K_T_tile = spec.K_T // spec.k_split
    A_tile_bytes = M_T * K_T_tile * hw.meshRow * hw.tileSize
    B_tile_bytes = K_T_tile * N_T * hw.tileSize * hw.meshCol
    D_bytes = M_T * N_T * hw.meshRow * hw.meshCol * 4
    D_num_elements = M_T * N_T * hw.meshRow * hw.meshCol
    chunks = list(range(spec.k_split))
    assert len(chunk_cluster) == spec.k_split

    for k in chunks:
        cid = chunk_cluster[k]
        ref._l1(mem, f"l1_A_{name}_k{k}", A_tile_bytes, cid)
        ref._l1(mem, f"l1_B_{name}_k{k}", B_tile_bytes, cid)
        ref._l1(mem, f"l1_D_{name}_k{k}", D_bytes, cid)
        ref._l3(mem, f"{name}_D_partial_c{k}_l3", D_bytes)
    for i in range(1, len(chunks)):
        label = "_".join(f"c{c}" for c in chunks[:i + 1])
        ref._l3(mem, f"{name}_sum_{label}_l3", D_bytes)
    ref._l3(mem, f"{name}_fp32_l3", D_bytes)
    ref._l3(mem, f"{name}_fp32_rm_l3", D_bytes)

    store_nodes = {}
    for k in chunks:
        cid = chunk_cluster[k]
        l1_A, l1_B, l1_D = (mem[f"l1_{x}_{name}_k{k}"] for x in "ABD")
        load_A = BingoNode(
            assigned_chiplet_id=0, assigned_cluster_id=cid, assigned_core_id=DMA_CORE,
            node_name=f"Load_{name}_c{cid}_k{k}_A",
            kernel_name="__snax_bingo_kernel_idma_1d_copy",
            kernel_args=ref.SnaxBingoKernelIdma1dCopyArgs(
                src_addr=ref._sym(mem, f"golden_{name}_A_k{k}"), dst_addr=l1_A, size=A_tile_bytes),
        )
        load_B = BingoNode(
            assigned_chiplet_id=0, assigned_cluster_id=cid, assigned_core_id=DMA_CORE,
            node_name=f"Load_{name}_c{cid}_k{k}_B",
            kernel_name="__snax_bingo_kernel_idma_1d_copy",
            kernel_args=ref.SnaxBingoKernelIdma1dCopyArgs(
                src_addr=ref._sym(mem, f"golden_{name}_B_k{k}"), dst_addr=l1_B, size=B_tile_bytes),
        )
        gemm = BingoNode(
            assigned_chiplet_id=0, assigned_cluster_id=cid, assigned_core_id=GEMM_CORE,
            node_name=f"Gemm_{name}_c{cid}_k{k}",
            kernel_name="__snax_bingo_kernel_gemm_full",
            kernel_args=ref.SnaxBingoKernelGemmFullArgs(
                input_A_addr=l1_A, input_B_addr=l1_B, input_C_addr=0, output_D_addr=l1_D,
                M=M_T, K=K_T_tile, N=N_T, array_shape_idx=hw.array_shape_idx,
                transpose_A=0, transpose_B=0, accumPrevC=0),
        )
        store = BingoNode(
            assigned_chiplet_id=0, assigned_cluster_id=cid, assigned_core_id=DMA_CORE,
            node_name=f"Store_{name}_k{k}",
            kernel_name="__snax_bingo_kernel_idma_1d_copy",
            kernel_args=ref.SnaxBingoKernelIdma1dCopyArgs(
                src_addr=l1_D, dst_addr=mem[f"{name}_D_partial_c{k}_l3"], size=D_bytes),
        )
        for n in (load_A, load_B, gemm, store):
            dfg.bingo_add_node(n)
        dfg.bingo_add_edge(load_A, load_B)
        dfg.bingo_add_edge(load_B, gemm)
        dfg.bingo_add_edge(gemm, store)
        ref._add_edge_list(dfg, input_dep, load_A)
        store_nodes[k] = store
        if debug:
            check_p = BingoNode(
                assigned_chiplet_id=0, assigned_cluster_id=0, assigned_core_id=HOST_CORE,
                node_name=f"Check_{name}_D_partial_k{k}",
                kernel_name="__host_bingo_kernel_check_result",
                kernel_args=ref.HostBingoKernelCheckResultArgs(
                    golden_data_addr=ref._sym(mem, f"golden_{name}_D_partial_c{k}"),
                    output_data_addr=mem[f"{name}_D_partial_c{k}_l3"],
                    data_size=ref._capped_bytes(D_bytes), name=f"{name}_D_partial_c{k}",
                    check_type=ref.BINGO_CHECK_TYPE_BYTE_EXACT),
            )
            dfg.bingo_add_node(check_p)
            dfg.bingo_add_edge(store, check_p)

    running_buf = mem[f"{name}_D_partial_c{chunks[0]}_l3"]
    last = store_nodes[chunks[0]]
    for i in range(1, len(chunks)):
        k = chunks[i]
        label = "_".join(f"c{c}" for c in chunks[:i + 1])
        out_buf = mem[f"{name}_sum_{label}_l3"]
        add = BingoNode(
            assigned_chiplet_id=0, assigned_cluster_id=0, assigned_core_id=HOST_CORE,
            node_name=f"Add_{name}_{label}",
            kernel_name="__host_bingo_kernel_add_i32",
            kernel_args=ref.HostBingoKernelAraAddI32Args(
                input_a_addr=running_buf, input_b_addr=mem[f"{name}_D_partial_c{k}_l3"],
                output_addr=out_buf, num_elements=D_num_elements),
        )
        dfg.bingo_add_node(add)
        dfg.bingo_add_edge(last, add)
        dfg.bingo_add_edge(store_nodes[k], add)
        last = add
        if debug:
            check_s = BingoNode(
                assigned_chiplet_id=0, assigned_cluster_id=0, assigned_core_id=HOST_CORE,
                node_name=f"Check_{name}_sum_{label}",
                kernel_name="__host_bingo_kernel_check_result",
                kernel_args=ref.HostBingoKernelCheckResultArgs(
                    golden_data_addr=ref._sym(mem, f"golden_{name}_sum_{label}"), output_data_addr=out_buf,
                    data_size=ref._capped_bytes(D_bytes), name=f"{name}_sum_{label}",
                    check_type=ref.BINGO_CHECK_TYPE_BYTE_EXACT),
            )
            dfg.bingo_add_node(check_s)
            dfg.bingo_add_edge(add, check_s)
            last = check_s
        running_buf = out_buf

    dequant = BingoNode(
        assigned_chiplet_id=0, assigned_cluster_id=0, assigned_core_id=HOST_CORE,
        node_name=f"Dequant_{name}",
        kernel_name="__host_bingo_kernel_dequantize_i32f32",
        kernel_args=ref.HostBingoKernelAraDequantizeI32F32Args(
            input_addr=running_buf, output_addr=mem[f"{name}_fp32_l3"],
            scale_addr=ref._sym(mem, spec.scale_sym_name), num_elements=D_num_elements),
    )
    dfg.bingo_add_node(dequant)
    dfg.bingo_add_edge(last, dequant)
    d2rm_cls = ref.xdma_conv_args("xdma_d_to_row_major", hw.meshRow, hw.meshCol, 4)
    reshape = BingoNode(
        assigned_chiplet_id=0, assigned_cluster_id=0, assigned_core_id=DMA_CORE,
        node_name=f"Reshape_{name}_d2rm",
        kernel_name=d2rm_cls.KERNEL_NAME,
        kernel_args=d2rm_cls(src_addr=mem[f"{name}_fp32_l3"], dst_addr=mem[f"{name}_fp32_rm_l3"],
                             M_T=M_T, N_T=N_T),
    )
    dfg.bingo_add_node(reshape)
    dfg.bingo_add_edge(dequant, reshape)
    if not debug and not final_check:
        return reshape
    check = BingoNode(
        assigned_chiplet_id=0, assigned_cluster_id=0, assigned_core_id=HOST_CORE,
        node_name=f"Check_{name}_fp32",
        kernel_name="__host_bingo_kernel_check_result",
        kernel_args=ref.HostBingoKernelCheckResultArgs(
            golden_data_addr=ref._sym(mem, f"golden_{name}_fp32"), output_data_addr=mem[f"{name}_fp32_rm_l3"],
            num_elements=ref._capped_elems(D_num_elements, elem_bytes=4), name=f"{name}_fp32",
            check_type=ref.BINGO_CHECK_TYPE_FP32_TOL, tolerance=spec.check_tolerance),
    )
    dfg.bingo_add_node(check)
    dfg.bingo_add_edge(reshape, check)
    return check


def main():
    args = ref.get_args()
    os.makedirs(args.output_dir, exist_ok=True)
    with open(args.cfg) as f:
        param = hjson.loads(f.read())
    with open(args.hwcfg) as f:
        hwcfg = hjson.loads(f.read())
    chunk_cluster = [int(c) for c in param.pop("chunk_cluster")]
    merged = {**param, **hwcfg}
    if args.data_h is not None:
        with open(args.data_h, "w") as f:
            f.write(ref.emit_header_file(**merged))
    array_shape = merged["array_shape"]
    unrolling = merged["snax_versacore_core_template"]["snax_acc_cfg"][0]["snax_versacore_spatial_unrolling"][0][array_shape]
    platform = ref.parse_platform_cfg(args.platformcfg)
    if not ref.guard_cluster_count(param, platform, args.output_dir, args.output_offload_file_name):
        return
    if any(not 0 <= c < platform["num_clusters_per_chiplet"] for c in chunk_cluster):
        raise ValueError(f"chunk_cluster {chunk_cluster} names a cluster the platform does not have")
    hw = ref.HwParams(
        meshRow=unrolling[0], tileSize=unrolling[1], meshCol=unrolling[2], array_shape_idx=array_shape,
        seq_len=merged["seq_len"], d_model=merged["d_model"], d_head=merged["d_head"],
        num_chiplets=platform["num_chiplets"], num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
        num_cores_per_cluster=platform["num_cores_per_cluster"], chiplet_ids=platform["chiplet_ids"],
    )
    debug = (args.debug_mode == "debug")
    dfg = ref.BingoDFG(
        num_chiplets=hw.num_chiplets, num_clusters_per_chiplet=hw.num_clusters_per_chiplet,
        num_cores_per_cluster=hw.num_cores_per_cluster, is_host_as_acc=True, chiplet_ids=hw.chiplet_ids,
    )
    mem = {}
    specs = ref.build_attention_specs(hw)

    def gemm(stage, dep, final_check=False):
        return emit_ksplit_gemm_by_chunk(dfg, mem, hw, specs[stage], chunk_cluster, debug=debug,
                                         input_dep=dep, final_check=final_check)

    # The composition of attn_reference_4cluster main()
    tail_quant = ref.emit_quantize_pattern(dfg, mem, hw, debug=debug)
    tail_proj_q = gemm("proj_q", tail_quant)
    tail_proj_k = gemm("proj_k", tail_quant)
    tail_proj_v = gemm("proj_v", tail_quant)
    tail_qk = gemm("qk_matmul", [tail_proj_q, tail_proj_k])
    tail_sm = ref.emit_softmax_pattern(dfg, mem, hw, input_buf_name="qk_matmul_fp32_rm_l3", debug=debug,
                                       input_dep=tail_qk)
    tail_av = gemm("av_matmul", [tail_sm, tail_proj_v])
    gemm("proj_o", tail_av, final_check=True)
    print(f"Built attention DFG with a spare cluster ({'debug' if debug else 'run'} mode), "
          f"K-chunks on clusters {chunk_cluster}")
    dfg.bingo_compile_dfg("attn_spare", args.output_dir, args.output_offload_file_name,
                          extra_include_header_list=["attention_datagen_data.h"])


if __name__ == "__main__":
    main()
