#!/usr/bin/env python3
"""Choose deep g0 or shallow g1 from an integer margin after the shared prefix."""
import argparse
import json
from pathlib import Path
import runpy
import sys

import hjson

CURRENT_DIR = Path(__file__).resolve().parent
ROOT = CURRENT_DIR.parents[7]
B3 = CURRENT_DIR.parent / "early_exit_2cluster"
sys.path.append(str(ROOT / "target/sw/host/runtime/libbingo/mini_compiler"))
sys.path.append(str(B3))
from bingo_dfg import BingoDFG
from bingo_kernel_args import (
    BingoKernelArgs, HostBingoKernelDummyArgs, HostBingoKernelIdmaArgs,
    SnaxBingoKernelGemmFullArgs, SnaxBingoKernelIdma1dCopyArgs,
)
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_node import BingoNode
from bingo_platform import guard_cluster_count, parse_bingo_core_type_ids, parse_platform_cfg
from early_exit_conf_datagen import generate_data, emit_header

RequantArgs = runpy.run_path(str(B3 / "main_bingo.py"))["RequantArgs"]


class SelectArgs(BingoKernelArgs):
    KERNEL_NAME = "__host_bingo_kernel_ee_select"

    def __init__(self, samples_addr, dst_addr, sample_size, sample_count):
        self.samples_addr, self.dst_addr = samples_addr, dst_addr
        self.sample_size, self.sample_count = sample_size, sample_count

    def get_struct_name(self):
        return "__host_bingo_kernel_ee_select_args_t"

    def get_c_field_assignments(self, handles):
        fields = {}
        for name in ("samples_addr", "dst_addr"):
            self._process_addr(getattr(self, name), name, fields, handles,
                               split_64bit=False, as_64bit=True)
        return {**fields, "sample_size": str(self.sample_size), "sample_count": str(self.sample_count)}


class ConfGateArgs(BingoKernelArgs):
    KERNEL_NAME = "__host_bingo_kernel_ee_conf_gate"

    def __init__(self, src_addr, decision_addr, count, threshold, cerf_controlled_mask=3):
        self.src_addr, self.decision_addr = src_addr, decision_addr
        self.count, self.threshold = count, threshold
        self.cerf_controlled_mask = cerf_controlled_mask

    def get_struct_name(self):
        return "__host_bingo_kernel_ee_conf_gate_args_t"

    def get_c_field_assignments(self, handles):
        fields = {}
        for name in ("src_addr", "decision_addr"):
            self._process_addr(getattr(self, name), name, fields, handles,
                               split_64bit=False, as_64bit=True)
        return {**fields, "count": str(self.count), "threshold": str(self.threshold),
                "cerf_controlled_mask": str(self.cerf_controlled_mask)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output_dir", type=Path, default=CURRENT_DIR)
    parser.add_argument("--output_offload_file_name", default="offload_bingo_hw.h")
    parser.add_argument("-c", "--cfg", type=Path, required=True)
    parser.add_argument("--hwcfg", type=Path, required=True)
    parser.add_argument("--platformcfg", type=Path, required=True)
    parser.add_argument("--data_h", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    params = hjson.loads(args.cfg.read_text())
    if not guard_cluster_count(params, parse_platform_cfg(args.platformcfg),
                               args.output_dir, args.output_offload_file_name):
        return
    hw = hjson.loads(args.hwcfg.read_text())
    row, tile, col = hw["snax_versacore_core_template"]["snax_acc_cfg"][0][
        "snax_versacore_spatial_unrolling"][0][params["array_shape"]]
    params.update(meshRow=row, tileSize=tile, meshCol=col)
    data, a_size, margins = generate_data(params)
    if args.data_h:
        emit_header(args.data_h, data)
    (args.output_dir / "early_exit_conf_data.json").write_text(
        json.dumps(dict(threshold=params["threshold"], margins=margins), indent=2) + "\n")
    d_size, b_size = len(data["shallow_golden"]) // 2 * 4, len(data["weight_W1"])
    dfg = BingoDFG(1, 2, 2, True, [0],
                   core_type_ids=parse_bingo_core_type_ids(args.platformcfg))
    alloc = lambda name, size, cluster=None: BingoMemAlloc(
        name, size, "L3" if cluster is None else "L1", chip_id=0,
        cluster_id=0 if cluster is None else cluster)
    x_input, decision = alloc("X", a_size), alloc("ee_decision", 4)
    h, h8 = alloc("h", d_size), alloc("h8", a_size)
    deep_out, shallow_out = alloc("deep_out", d_size), alloc("shallow_out", d_size)
    x, w1, d1 = alloc("X_L1", a_size, 1), alloc("W1_L1", b_size, 1), alloc("D1_L1", d_size, 1)
    a2, w2, d2 = alloc("H8_L1", a_size, 0), alloc("W2_L1", b_size, 0), alloc("D2_L1", d_size, 0)

    def node(name, kernel_args, cluster, core, group=None, kernel=None):
        task = BingoNode(0, cluster, core, node_name=name,
                         kernel_name=kernel or kernel_args.KERNEL_NAME, kernel_args=kernel_args)
        if group is not None:
            task.cond_exec_en, task.cond_exec_group_id = True, group
        dfg.bingo_add_node(task)
        return task

    select = node("select_sample", SelectArgs(BingoMemSymbol("X_samples", offset=0),
                                             x_input, a_size, 2), 0, 2)
    copy = lambda name, src, dst, size, cluster, group=None: node(
        name, SnaxBingoKernelIdma1dCopyArgs(src, dst, size), cluster, 1, group,
        "__snax_bingo_kernel_idma_1d_copy")
    gemm = lambda name, a, b, d, cluster, group=None: node(
        name, SnaxBingoKernelGemmFullArgs(a, b, 0, d, params["M"], params["K"],
                                        params["N"], params["array_shape"], 0, 0, 0),
        cluster, 0, group, "__snax_bingo_kernel_gemm_full")
    ld_x = copy("load_X", x_input, x, a_size, 1)
    ld_w1 = copy("load_W1", BingoMemSymbol("weight_W1", offset=0), w1, b_size, 1)
    gemm1 = gemm("gemm1", x, w1, d1, 1)
    store_h = copy("store_h", d1, h, d_size, 1)
    rq = node("requant", RequantArgs(h, h8, d_size // 4, params["shift"], a_size), 0, 2)
    gating = node("conf_gate", ConfGateArgs(h, decision, d_size // 4, params["threshold"]), 0, 2)
    gating.node_type, gating.cerf_write_groups = "gating", [0, 1]
    ld_h8 = copy("load_h8", h8, a2, a_size, 0, 0)
    ld_w2 = copy("load_W2", BingoMemSymbol("weight_W2", offset=0), w2, b_size, 0, 0)
    gemm2 = gemm("gemm2", a2, w2, d2, 0, 0)
    store_deep = copy("store_deep", d2, deep_out, d_size, 0, 0)
    backup = node("shallow_copy", HostBingoKernelIdmaArgs(h, shallow_out, d_size),
                  0, 2, 1, "__host_bingo_kernel_idma")
    join = node("join", HostBingoKernelDummyArgs(0), 0, 2, kernel="__host_bingo_kernel_dummy")
    for source, target in (
        (select, ld_x), (select, ld_w1), (ld_x, gemm1), (ld_w1, gemm1),
        (gemm1, store_h), (store_h, rq), (rq, gating), (gating, ld_h8), (gating, ld_w2),
        (ld_h8, gemm2), (ld_w2, gemm2), (gemm2, store_deep),
        (gating, backup), (store_deep, join), (backup, join),
    ):
        dfg.bingo_add_edge(source, target)
    dfg.bingo_add_cerf_fallback(gemm2, backup)
    post = [
        "{", "    uint32_t __sample = early_exit_conf_sample();",
        "    uint32_t __decision = *(uint32_t*)ptr_ee_decision;",
        "    uint32_t __fault_evt = !!(bingo_cerf_fb_evt() & (1u << BINGO_CORE_TYPE_ID(0, 0)));",
        "#ifdef BINGO_C6_SW_CERF",
        "    // C6: the host may have degraded instead",
        "    __fault_evt |= !!(bingo_c6_sw_cerf_evt() & (1u << BINGO_CORE_TYPE_ID(0, 0)));",
        "#endif",
        "    uint32_t __shallow = __fault_evt || __decision;",
        "    uint32_t __err = (__sample >= 2 || __decision > 1);",
        "    int32_t* __out = (int32_t*)(__shallow ? ptr_shallow_out : ptr_deep_out);",
        "    int32_t* __gold = (int32_t*)(uintptr_t)chiplet_addr_transform(",
        "        (uint64_t)(uintptr_t)(__shallow ? shallow_golden : deep_golden));",
        f"    if (__sample < 2) for (uint32_t __i = 0; __i < {d_size // 4}; __i++)",
        f"        if (__out[__i] != __gold[__sample * {d_size // 4} + __i]) __err++;",
        '    printf_safe("[EarlyExitConf] sample=%d decision=%d fault_evt=%d shallow=%d\\r\\n",',
        "                __sample, __decision, __fault_evt, __shallow);",
        '    printf_safe("[Host] Check [%s]: %s\\r\\n", __shallow ? "shallow_out" : "deep_out", __err ? "FAIL" : "PASS");',
        "    if (__err) return 1;", "}",
    ]
    dfg.bingo_compile_dfg(app_name="Confidence-driven early exit", output_dir=str(args.output_dir),
        output_file_name=args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name if args.data_h else "early_exit_conf_data.h",
                                   "requant.h", "ee_conf.h"],
        post_execute_code=post)


if __name__ == "__main__":
    main()
