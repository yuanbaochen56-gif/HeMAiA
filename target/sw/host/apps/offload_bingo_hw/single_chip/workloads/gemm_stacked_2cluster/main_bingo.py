# BEGIN WORKLOAD DESCRIPTION AND TASK GRAPH
# The stacked GEMM of gemm_stacked_1cluster on each of the two clusters, with
# every data move on the cluster's DM core (iDMA) and both GEMMs on core 0.
# Per cluster c:
#
# Load_A1 + Load_B1 -> Gemm_D1
# Gemm_D1 -> Load_B2          (left out with --overlap 1)
# Gemm_D1 + Load_B2 -> Gemm_D2
# Gemm_D2 -> Store_D2
#
# The host checks the whole D2 of both clusters after the offload. The software
# runtime runs the same kernels on the same graph
# (offload_bingo_sw/single_chip/workloads/gemm_stacked_2cluster, which takes its
# data and sizes from this file). Without the Gemm_D1 -> Load_B2 edge, Load_B2
# overlaps Gemm_D1 on the other core; the software runtime runs one task of a
# cluster at a time and cannot do that.
# END WORKLOAD DESCRIPTION AND TASK GRAPH

import os
import sys
import argparse
import pathlib
import hjson

current_dir = os.path.dirname(os.path.abspath(__file__))
WORKLOADS_DIR = os.path.dirname(current_dir)
ROOT_DIR = os.path.normpath(os.path.join(current_dir, "../../../../../../../../"))
sys.path.append(f"{ROOT_DIR}/target/sw/host/runtime/libbingo/mini_compiler")
# The data of gemm_stacked_1cluster (same generator, same seed)
sys.path.append(os.path.join(WORKLOADS_DIR, "gemm_stacked_1cluster"))
from gemm_datagen import emit_header_file, infer_stacked_gemm2_dims  # noqa: E402

from bingo_dfg import BingoDFG  # noqa: E402
from bingo_platform import guard_cluster_count, parse_platform_cfg  # noqa: E402
from bingo_node import BingoNode  # noqa: E402
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol  # noqa: E402
from bingo_kernel_args import (  # noqa: E402
    SnaxBingoKernelIdma1dCopyArgs,
    SnaxBingoKernelGemmFullArgs,
)

GEMM_CORE_ID = 0
DMA_CORE_ID = 1
APP_NAME = "Single-Chip GEMM Stacked 2-Cluster"


def get_args():
    parser = argparse.ArgumentParser(description="Bingo HW Manager")
    parser.add_argument("--output_dir", type=str, default=".")
    parser.add_argument("--output_offload_file_name", type=str, default="offload_bingo_hw.h")
    parser.add_argument("-c", "--cfg", type=pathlib.Path, required=True)
    parser.add_argument("--hwcfg", type=pathlib.Path, required=True)
    parser.add_argument("--platformcfg", type=pathlib.Path, required=True)
    parser.add_argument("--data_h", type=pathlib.Path, default=None)
    parser.add_argument("--overlap", type=int, choices=(0, 1), default=0,
                        help="1: no Gemm_D1 -> Load_B2 edge (Load_B2 may overlap Gemm_D1)")
    return parser.parse_args()


def define_workload_params(cfg_path, hwcfg_path):
    """The parameters and derived sizes (gemm_stacked_1cluster's rules)."""
    with open(cfg_path) as f:
        param = hjson.loads(f.read())
    with open(hwcfg_path) as f:
        hw = hjson.loads(f.read())
    merged = {**param, **hw}
    array_shape = merged["array_shape"]
    unrolling = merged["snax_versacore_core_template"]["snax_acc_cfg"][0][
        "snax_versacore_spatial_unrolling"][0][array_shape]
    meshRow, tileSize, meshCol = unrolling
    M1, K1, N1, N2 = merged["M1"], merged["K1"], merged["N1"], merged["N2"]
    M2, K2 = infer_stacked_gemm2_dims(M1, N1, tileSize, meshCol)
    params = dict(
        M1=M1, K1=K1, N1=N1, M2=M2, K2=K2, N2=N2, meshRow=meshRow, tileSize=tileSize,
        meshCol=meshCol, arrayShapeIdx=array_shape, transposeA=merged.get("transposed_A", 0),
        transposeB=merged.get("transposed_B", 0), accumPrevC=merged.get("accumPrevC", 0),
        num_clusters=merged["num_clusters"])
    params["A1_size"] = M1 * K1 * meshRow * tileSize  # int8
    params["B1_size"] = K1 * N1 * tileSize * meshCol  # int8
    params["D1_size"] = M1 * N1 * meshRow * meshCol * 4  # int32
    params["B2_size"] = K2 * N2 * tileSize * meshCol  # int8
    params["D2_size"] = M2 * N2 * meshRow * meshCol * 4  # int32
    return params, merged


def define_memory_handles(params):
    handles = {name: BingoMemSymbol(name, offset=0) for name in ("A1", "B1", "B2")}
    for c in range(params["num_clusters"]):
        for name in ("A1", "B1", "D1", "B2", "D2"):
            handles[f"{name}_l1_{c}"] = BingoMemAlloc(
                f"{name}_l1_{c}", size=params[f"{name}_size"], mem_level="L1", chip_id=0x00,
                cluster_id=c)
        handles[f"D2_l3_{c}"] = BingoMemAlloc(f"D2_l3_{c}", size=params["D2_size"],
                                              mem_level="L3", chip_id=0x00)
    return handles


def copy(c, name, src, dst, size):
    return BingoNode(assigned_chiplet_id=0x00, assigned_cluster_id=c, assigned_core_id=DMA_CORE_ID,
                     node_name=f"{name}_{c}", kernel_name="__snax_bingo_kernel_idma_1d_copy",
                     kernel_args=SnaxBingoKernelIdma1dCopyArgs(src_addr=src, dst_addr=dst, size=size))


def gemm(c, name, params, a, b, d, m, k, n):
    return BingoNode(
        assigned_chiplet_id=0x00, assigned_cluster_id=c, assigned_core_id=GEMM_CORE_ID,
        node_name=f"{name}_{c}", kernel_name="__snax_bingo_kernel_gemm_full",
        kernel_args=SnaxBingoKernelGemmFullArgs(
            input_A_addr=a, input_B_addr=b, input_C_addr=0, output_D_addr=d, M=m, K=k, N=n,
            array_shape_idx=params["arrayShapeIdx"], transpose_A=params["transposeA"],
            transpose_B=params["transposeB"], accumPrevC=params["accumPrevC"]))


def create_dfg(params, h, platform, overlap):
    dfg = BingoDFG(num_chiplets=platform["num_chiplets"],
                   num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
                   num_cores_per_cluster=platform["num_cores_per_cluster"],
                   is_host_as_acc=True, chiplet_ids=platform["chiplet_ids"])
    for c in range(params["num_clusters"]):
        load_a1 = copy(c, "Load_A1", h["A1"], h[f"A1_l1_{c}"], params["A1_size"])
        load_b1 = copy(c, "Load_B1", h["B1"], h[f"B1_l1_{c}"], params["B1_size"])
        load_b2 = copy(c, "Load_B2", h["B2"], h[f"B2_l1_{c}"], params["B2_size"])
        gemm_d1 = gemm(c, "Gemm_D1", params, h[f"A1_l1_{c}"], h[f"B1_l1_{c}"], h[f"D1_l1_{c}"],
                       params["M1"], params["K1"], params["N1"])
        # D1 is the A input of the second GEMM (gemm_stacked_1cluster)
        gemm_d2 = gemm(c, "Gemm_D2", params, h[f"D1_l1_{c}"], h[f"B2_l1_{c}"], h[f"D2_l1_{c}"],
                       params["M2"], params["K2"], params["N2"])
        store_d2 = copy(c, "Store_D2", h[f"D2_l1_{c}"], h[f"D2_l3_{c}"], params["D2_size"])
        for node in (load_a1, load_b1, load_b2, gemm_d1, gemm_d2, store_d2):
            dfg.bingo_add_node(node)
        dfg.add_edge(load_a1, gemm_d1)
        dfg.add_edge(load_b1, gemm_d1)
        if not overlap:
            dfg.add_edge(gemm_d1, load_b2)
        dfg.add_edge(gemm_d1, gemm_d2)
        dfg.add_edge(load_b2, gemm_d2)
        dfg.add_edge(gemm_d2, store_d2)
    return dfg


def post_check(params):
    """The host checks the whole D2 of every cluster against the golden D2."""
    code = ["uint32_t __gemm_errors = 0;",
            "const volatile uint8_t *__golden = (const volatile uint8_t *)(uintptr_t)"
            "chiplet_addr_transform((uint64_t)(uintptr_t)D2);",
            'asm volatile("fence" ::: "memory");']
    for c in range(params["num_clusters"]):
        code += ["{",
                 "    uint32_t errors = 0;",
                 f"    const volatile uint8_t *output = (const volatile uint8_t *)(uintptr_t)ptr_D2_l3_{c};",
                 f"    for (uint32_t i = 0; i < {params['D2_size']}u; ++i) errors += output[i] != __golden[i];",
                 f'    printf_safe("[Host] Check [D2_cluster{c}]: %s (%u / {params["D2_size"]} bytes)\\r\\n", '
                 'errors ? "FAIL" : "PASS", errors);',
                 "    __gemm_errors += errors;",
                 "}"]
    code += ["if (__gemm_errors) return 1;"]
    return code


def main():
    args = get_args()
    os.makedirs(args.output_dir, exist_ok=True)
    params, merged = define_workload_params(args.cfg, args.hwcfg)
    if args.data_h is not None:
        args.data_h.write_text(emit_header_file(**merged))
    platform = parse_platform_cfg(args.platformcfg)
    if not guard_cluster_count(merged, platform, args.output_dir, args.output_offload_file_name):
        return
    dfg = create_dfg(params, define_memory_handles(params), platform, args.overlap)
    dfg.bingo_compile_dfg(APP_NAME + (" (overlap)" if args.overlap else ""), args.output_dir,
                          args.output_offload_file_name,
                          extra_include_header_list=[args.data_h.name if args.data_h else "gemm_data.h"],
                          post_execute_code=post_check(params))


if __name__ == "__main__":
    main()
