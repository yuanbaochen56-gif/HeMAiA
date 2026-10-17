# BEGIN WORKLOAD DESCRIPTION AND TASK GRAPH
# Copies of many sizes on the DM cores, both directions, for the calibration of the
# compiler's task-time estimates (bingo_cost.py). Per cluster c, one chain:
#
# Load_<s>_c (L3 -> L1, s = every size in "sizes") -> ... -> Store_<s>_c (L1 -> L3, every size)
#
# Phase A ("own times"): the chains of the clusters run one after another (cluster 1
# starts after cluster 0's last copy), so no copy shares the L3 port with another
# cluster. Phase B ("shared"): after phase A every cluster runs the chain of the
# "shared_sizes" at the same time. The host checks the head and the tail of every
# cluster's L3 buffer after the offload.
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
CHECK = 1024
APP_NAME = "Single-Chip DMA Sizes 2-Cluster"


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
    for name in ("num_clusters", "sizes", "shared_sizes"):
        if name not in params:
            raise KeyError(f"params.hjson must define {name}")
    sizes = list(params["sizes"]) + list(params["shared_sizes"])
    if not sizes or min(sizes) < CHECK or any(s % 64 for s in sizes):
        raise ValueError(f"every size must be a multiple of 64 and at least {CHECK} bytes")
    params["largest"] = max(sizes)
    if max(params["sizes"]) != params["largest"] or max(params["shared_sizes"]) != params["largest"]:
        raise ValueError("both phases must end with the largest size (the host checks its result)")
    return params


def emit_header_file(params):
    source = [i * 13 + 5 & 0xFF for i in range(params["largest"])]
    return "\n\n".join([
        "#include <stdint.h>",
        format_scalar_definition("uint32_t", "SRC_size", params["largest"]),
        format_vector_definition("uint8_t", "SRC", source, alignment=64),
    ]) + "\n"


def define_memory_handles(params):
    handles = {"SRC": BingoMemSymbol("SRC", offset=0)}
    for c in range(params["num_clusters"]):
        handles[f"buf_l1_{c}"] = BingoMemAlloc(f"buf_l1_{c}", size=params["largest"], mem_level="L1",
                                               chip_id=0x00, cluster_id=c)
        handles[f"out_l3_{c}"] = BingoMemAlloc(f"out_l3_{c}", size=params["largest"], mem_level="L3",
                                               chip_id=0x00)
    return handles


def copy(c, name, src, dst, size):
    return BingoNode(assigned_chiplet_id=0x00, assigned_cluster_id=c, assigned_core_id=DMA_CORE_ID,
                     node_name=f"{name}_{c}", kernel_name="__snax_bingo_kernel_idma_1d_copy",
                     kernel_args=SnaxBingoKernelIdma1dCopyArgs(src_addr=src, dst_addr=dst, size=size))


def chain(c, phase, sizes, h):
    nodes = [copy(c, f"{phase}_Load_{s}", h["SRC"], h[f"buf_l1_{c}"], s) for s in sizes]
    nodes += [copy(c, f"{phase}_Store_{s}", h[f"buf_l1_{c}"], h[f"out_l3_{c}"], s) for s in sizes]
    return nodes


def create_dfg(params, h, platform):
    dfg = BingoDFG(num_chiplets=platform["num_chiplets"],
                   num_clusters_per_chiplet=platform["num_clusters_per_chiplet"],
                   num_cores_per_cluster=platform["num_cores_per_cluster"],
                   is_host_as_acc=True, chiplet_ids=platform["chiplet_ids"])
    clusters = range(params["num_clusters"])
    own = [chain(c, "A", params["sizes"], h) for c in clusters]
    shared = [chain(c, "B", params["shared_sizes"], h) for c in clusters]
    for nodes in own + shared:
        for node in nodes:
            dfg.bingo_add_node(node)
        for a, b in zip(nodes, nodes[1:]):
            dfg.add_edge(a, b)
    # Phase A one cluster after another; phase B starts on every cluster after it
    for a, b in zip(own, own[1:]):
        dfg.add_edge(a[-1], b[0])
    for nodes in shared:
        dfg.add_edge(own[-1][-1], nodes[0])
    return dfg


def post_check(params):
    """The host checks the first and the last CHECK bytes of every cluster's L3 buffer
    (written by the largest store of phase B) against the source."""
    largest = params["largest"]
    code = ["uint32_t __out_errors = 0;",
            "const volatile uint8_t *__src = (const volatile uint8_t *)(uintptr_t)"
            "chiplet_addr_transform((uint64_t)(uintptr_t)SRC);",
            'asm volatile("fence" ::: "memory");']
    for c in range(params["num_clusters"]):
        code += ["{",
                 "    uint32_t errors = 0;",
                 f"    const volatile uint8_t *out = (const volatile uint8_t *)(uintptr_t)ptr_out_l3_{c};",
                 f"    for (uint32_t i = 0; i < {CHECK}u; ++i) errors += out[i] != __src[i];",
                 f"    for (uint32_t i = {largest - CHECK}u; i < {largest}u; ++i) errors += out[i] != __src[i];",
                 f'    printf_safe("[Host] Check [out_cluster{c}]: %s (%u / {2 * CHECK} bytes)\\r\\n", '
                 'errors ? "FAIL" : "PASS", errors);',
                 "    __out_errors += errors;",
                 "}"]
    code += ["if (__out_errors) return 1;"]
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
                          extra_include_header_list=[args.data_h.name if args.data_h else "dma_sizes_data.h"],
                          post_execute_code=post_check(params))


if __name__ == "__main__":
    main()
