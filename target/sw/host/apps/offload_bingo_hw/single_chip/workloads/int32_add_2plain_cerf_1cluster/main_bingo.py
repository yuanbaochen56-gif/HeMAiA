#!/usr/bin/env python3
"""One int32 add, with a compiler-generated fallback on plain core 2.

host gating (g0 only) -> DM loads -> core 1 add (g0) -> core 2 add (g1)
                                                       -> host join -> exits

With L3 only and home-slot imports, a rejected export triggers the fallback.
The same-type backup warning is intentional: L1 substitution is disabled.
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
    SnaxBingoKernelIdma1dCopyArgs,
    SnaxBingoKernelInt32AddArgs,
)
from data_utils import format_scalar_definition, format_vector_definition


def emit_data(num_elements):
    a = list(range(num_elements))
    b = [3 * i + 7 for i in range(num_elements)]
    golden = [x + y for x, y in zip(a, b)]
    return "\n\n".join([
        "#include <stdint.h>",
        format_scalar_definition("uint32_t", "num_elements", num_elements),
        format_vector_definition("int32_t", "A_l3", a),
        format_vector_definition("int32_t", "B_l3", b),
        format_vector_definition("int32_t", "C_golden_l3", golden),
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
    if platform["num_chiplets"] != 1 or platform["num_cores_per_cluster"] != 4:
        raise ValueError("expects one chiplet with snax_versacore_2plain_to_cluster")
    count = params["num_elements"]
    if count <= 0:
        raise ValueError("num_elements must be positive")
    size = count * 4
    if args.data_h:
        args.data_h.write_text(emit_data(count))

    dfg = BingoDFG(
        num_chiplets=1, num_clusters_per_chiplet=1, num_cores_per_cluster=4,
        is_host_as_acc=True, chiplet_ids=platform["chiplet_ids"],
        core_type_ids=parse_bingo_core_type_ids(args.platformcfg))
    a_l1 = BingoMemAlloc("A_L1", size, "L1", chip_id=0, cluster_id=0)
    b_l1 = BingoMemAlloc("B_L1", size, "L1", chip_id=0, cluster_id=0)
    outputs = [BingoMemAlloc(f"C_L1_{branch}", size, "L1", chip_id=0, cluster_id=0)
               for branch in range(2)]

    gating = BingoNode(0, 0, 4, node_name="gating",
                       kernel_name="__host_bingo_kernel_cerf_gating",
                       kernel_args=HostBingoKernelCerfGatingArgs(
                           mode=BINGO_GATING_MODE_STATIC, cerf_controlled_mask=3,
                           top_k_or_threshold=1))
    gating.node_type = "gating"
    gating.cerf_write_groups = [0, 1]
    dfg.bingo_add_node(gating)
    loads = []
    for name, destination in (("A", a_l1), ("B", b_l1)):
        load = BingoNode(0, 0, 3, node_name=f"load_{name}",
                         kernel_name="__snax_bingo_kernel_idma_1d_copy",
                         kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                             src_addr=BingoMemSymbol(f"{name}_l3", offset=0),
                             dst_addr=destination, size=size))
        dfg.bingo_add_node(load)
        dfg.bingo_add_edge(gating, load)
        loads.append(load)

    adds = []
    for branch, core in enumerate((1, 2)):
        add = BingoNode(0, 0, core, node_name=f"core{core}_add",
                        kernel_args=SnaxBingoKernelInt32AddArgs(
                            a_addr=a_l1, b_addr=b_l1, c_addr=outputs[branch],
                            num_elements=count))
        add.cond_exec_en = True
        add.cond_exec_group_id = branch
        dfg.bingo_add_node(add)
        for load in loads:
            dfg.bingo_add_edge(load, add)
        adds.append(add)
    dfg.bingo_add_cerf_fallback(adds[0], adds[1])

    join = BingoNode(0, 0, 4, node_name="join",
                     kernel_name="__host_bingo_kernel_dummy",
                     kernel_args=HostBingoKernelDummyArgs(dummy_input=0))
    dfg.bingo_add_node(join)
    for add in adds:
        dfg.bingo_add_edge(add, join)

    post_check = [
        "{",
        "    uint32_t __evt = bingo_cerf_fb_evt();",
        "    uint32_t __type = BINGO_CORE_TYPE_ID(0, 1);",
        "    uint32_t __branch = (__evt & (1u << __type)) ? 1 : 0;",
        "    uint32_t __err = 0;",
        "#ifdef BINGO_ADD_EXPECT_BRANCH",
        "    if (__branch != BINGO_ADD_EXPECT_BRANCH) __err++;",
        "#endif",
        "    int32_t* __golden = (int32_t*)(uintptr_t)chiplet_addr_transform(",
        "        (uint64_t)(uintptr_t)C_golden_l3);",
        "    int32_t* __output[] = {(int32_t*)ptr_C_L1_0, (int32_t*)ptr_C_L1_1};",
        f"    for (uint32_t __i = 0; __i < {count}; __i++)",
        "        if (__output[__branch][__i] != __golden[__i]) __err++;",
        '    printf_safe("[Int32AddCERF] gating selected g0; join complete; output branch %d\\r\\n", __branch);',
        '    printf_safe("[Host] Check [C_branch_%d]: %s\\r\\n", __branch, __err ? "FAIL" : "PASS");',
        "    if (__err) return 1;",
        "}",
    ]
    dfg.bingo_compile_dfg(
        app_name="int32 Add (L3 reject CERF fallback)", output_dir=str(args.output_dir),
        output_file_name=args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name if args.data_h else "int32_add_data.h"],
        post_execute_code=post_check)


if __name__ == "__main__":
    main()
