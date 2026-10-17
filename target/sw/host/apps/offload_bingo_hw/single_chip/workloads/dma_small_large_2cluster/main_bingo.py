# BEGIN WORKLOAD DESCRIPTION AND TASK GRAPH
# Copies of growing size on each cluster's DM core, one after another, for the
# learned watchdog thresholds: with one class per kernel (the A7 classes) the small
# copies teach the bound that the large one then has to meet. Per cluster c:
#
# Copy_S (small, L3 -> L1) -> Copy_M (medium, L3 -> L1) -> Copy_L (large, L3 -> L1)
#                          -> Store_T (the last 1 KiB of the large buffer, L1 -> L3)
#
# Both clusters run the same chain at the same time. The host checks both stored
# tails against the source after the offload.
# END WORKLOAD DESCRIPTION AND TASK GRAPH

import os
import sys
import argparse
import pathlib
import hjson

current_dir = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(current_dir, "../../../../../../../../"))
sys.path.append(f"{ROOT_DIR}/target/sw/host/runtime/libbingo/mini_compiler")
sys.path.append(f"{ROOT_DIR}/util/sim")
import _usg_paths  # noqa: F401,E402  (registers util/sim/{common,gemm,xdma,ara} on sys.path)

from bingo_dfg import BingoDFG  # noqa: E402
from bingo_platform import guard_cluster_count, parse_platform_cfg  # noqa: E402
from bingo_node import BingoNode  # noqa: E402
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol  # noqa: E402
from bingo_kernel_args import SnaxBingoKernelIdma1dCopyArgs  # noqa: E402
from data_utils import format_scalar_definition, format_vector_definition  # noqa: E402

DMA_CORE_ID = 1
TAIL = 1024
APP_NAME = "Single-Chip DMA Small-Large 2-Cluster"


def get_args():
    parser = argparse.ArgumentParser(description="Bingo HW Manager")
    parser.add_argument("--output_dir", type=str, default=".")
    parser.add_argument("--output_offload_file_name", type=str, default="offload_bingo_hw.h")
    parser.add_argument("-c", "--cfg", type=pathlib.Path, required=True)
    parser.add_argument("--platformcfg", type=pathlib.Path, required=True)
    parser.add_argument("--data_h", type=pathlib.Path, default=None)
    return parser.parse_args()


def define_workload_params(cfg_path):
    with open(cfg_path) as f:
        params = hjson.loads(f.read())
    for name in ("num_clusters", "small", "medium", "large"):
        if name not in params:
            raise KeyError(f"params.hjson must define {name}")
    if not params["small"] <= params["medium"] <= params["large"] or params["large"] < TAIL:
        raise ValueError("expected small <= medium <= large and large >= 1 KiB")
    return params


def emit_header_file(params):
    source = [i * 7 & 0xFF for i in range(params["large"])]
    return "\n\n".join([
        "#include <stdint.h>",
        format_scalar_definition("uint32_t", "SRC_size", params["large"]),
        format_vector_definition("uint8_t", "SRC", source, alignment=64),
    ]) + "\n"


def define_memory_handles(params):
    handles = {"SRC": BingoMemSymbol("SRC", offset=0)}
    for c in range(params["num_clusters"]):
        for name in ("small", "medium", "large"):
            handles[f"{name}_l1_{c}"] = BingoMemAlloc(f"{name}_l1_{c}", size=params[name], mem_level="L1",
                                                      chip_id=0x00, cluster_id=c)
        handles[f"tail_l3_{c}"] = BingoMemAlloc(f"tail_l3_{c}", size=TAIL, mem_level="L3", chip_id=0x00)
    return handles


def copy(c, name, src, dst, size):
    return BingoNode(assigned_chiplet_id=0x00, assigned_cluster_id=c, assigned_core_id=DMA_CORE_ID,
                     node_name=f"{name}_{c}", kernel_name="__snax_bingo_kernel_idma_1d_copy",
                     kernel_args=SnaxBingoKernelIdma1dCopyArgs(src_addr=src, dst_addr=dst, size=size))


def create_dfg(params, h, platform):
    dfg = BingoDFG(num_chiplets=platform["num_chiplets"],
                   num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
                   num_cores_per_cluster=platform["num_cores_per_cluster"],
                   is_host_as_acc=True, chiplet_ids=platform["chiplet_ids"])
    for c in range(params["num_clusters"]):
        chain = [copy(c, "Copy_S", h["SRC"], h[f"small_l1_{c}"], params["small"]),
                 copy(c, "Copy_M", h["SRC"], h[f"medium_l1_{c}"], params["medium"]),
                 copy(c, "Copy_L", h["SRC"], h[f"large_l1_{c}"], params["large"]),
                 copy(c, "Store_T", h[f"large_l1_{c}"].view(params["large"] - TAIL), h[f"tail_l3_{c}"], TAIL)]
        for node in chain:
            dfg.bingo_add_node(node)
        for a, b in zip(chain, chain[1:]):
            dfg.add_edge(a, b)
    return dfg


def post_check(params):
    """The host checks the stored tail of every cluster against the source."""
    code = ["uint32_t __tail_errors = 0;",
            "const volatile uint8_t *__src = (const volatile uint8_t *)(uintptr_t)"
            "chiplet_addr_transform((uint64_t)(uintptr_t)SRC);",
            'asm volatile("fence" ::: "memory");']
    for c in range(params["num_clusters"]):
        code += ["{",
                 "    uint32_t errors = 0;",
                 f"    const volatile uint8_t *tail = (const volatile uint8_t *)(uintptr_t)ptr_tail_l3_{c};",
                 f"    for (uint32_t i = 0; i < {TAIL}u; ++i) errors += tail[i] != __src[{params['large'] - TAIL}u + i];",
                 f'    printf_safe("[Host] Check [tail_cluster{c}]: %s (%u / {TAIL} bytes)\\r\\n", '
                 'errors ? "FAIL" : "PASS", errors);',
                 "    __tail_errors += errors;",
                 "}"]
    code += ["if (__tail_errors) return 1;"]
    return code


def main():
    args = get_args()
    os.makedirs(args.output_dir, exist_ok=True)
    params = define_workload_params(args.cfg)
    if args.data_h is not None:
        args.data_h.write_text(emit_header_file(params))
    platform = parse_platform_cfg(args.platformcfg)
    if not guard_cluster_count(params, platform, args.output_dir, args.output_offload_file_name):
        return
    dfg = create_dfg(params, define_memory_handles(params), platform)
    dfg.bingo_compile_dfg(APP_NAME, args.output_dir, args.output_offload_file_name,
                          extra_include_header_list=[args.data_h.name if args.data_h else "dma_small_large_data.h"],
                          post_execute_code=post_check(params))


if __name__ == "__main__":
    main()
