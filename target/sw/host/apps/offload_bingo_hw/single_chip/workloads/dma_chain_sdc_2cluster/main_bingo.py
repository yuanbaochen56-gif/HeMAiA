"""S1a chain: six cluster-0 DMR copies; cluster-1's own copy is not enrolled."""
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "sdc_chain_base", HERE.parent / "dma_chain_2cluster/main_bingo.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
from bingo_platform import parse_bingo_core_type_ids


def create_dfg(params, handles, platform, core_type_ids):
    graph = base.BingoDFG(
        platform["num_chiplets"], platform["num_clusters_per_chiplet"],
        platform["num_cores_per_cluster"], True, platform["chiplet_ids"],
        core_type_ids=core_type_ids)
    last = None
    for index in range(params["chain_len"]):
        task = base.BingoNode(
            0, 0, base.DMA_CORE_ID, node_name=f"copy{index}",
            kernel_name="__snax_bingo_kernel_idma_1d_copy",
            kernel_args=base.SnaxBingoKernelIdma1dCopyArgs(
                base.BingoMemSymbol("A_l3", index * params["chunk_size"]),
                handles["A_L1_buf_0"].view(index * params["chunk_size"]),
                params["chunk_size"]))
        graph.bingo_add_node(task)
        if last is not None:
            graph.bingo_add_edge(last, task)
        last = task
    # Snapshot enrollment intentionally precedes cluster 1's own copy. Moving
    # that copy's shadow to cluster 0 would add avoidable observers to the main
    # chain's core. This is not a false A4 annotation or a changed whitelist.
    graph.bingo_auto_dmr(0, core_type_ids[(0, base.DMA_CORE_ID)])
    own = base.BingoNode(
        0, 1, base.DMA_CORE_ID, node_name="copy_cluster1",
        kernel_name="__snax_bingo_kernel_idma_1d_copy",
        kernel_args=base.SnaxBingoKernelIdma1dCopyArgs(
            handles["A_data_L3_symbol"], handles["A_L1_buf_1"], params["chunk_size"]))
    graph.bingo_add_node(own)
    return graph


def golden_checks(params):
    # The legacy checking kernel aborts the scheduler on FAIL. These final
    # checks therefore run in post_execute, after host exit has waited for all
    # observers. No new host kernel, or change to the legacy kernel, is needed.
    code = ["uint32_t __sdc_golden_errors = 0;"]
    for cluster, size, label in (
            (0, params["A_size"], "A_chain_cluster0"),
            (1, params["chunk_size"], "A_cluster1")):
        code += [
            "{",
            "    uint32_t errors = 0;",
            f"    const volatile uint8_t *output = (const volatile uint8_t *)(uintptr_t)ptr_A_L1_buf_{cluster};",
            f"    for (uint32_t i = 0; i < {size}u; ++i) errors += output[i] != A_l3[i];",
            f'    printf_safe("[Host] Check [{label}]: %s (%u / {size} bytes)\\r\\n", errors ? "FAIL" : "PASS", errors);',
            "    __sdc_golden_errors += errors;",
            "}",
        ]
    code += ["if (__sdc_golden_errors) return 2;"]
    return code


def main():
    args = base.get_args()
    os.makedirs(args.output_dir, exist_ok=True)
    params, merged = base.define_workload_params(args.cfg, args.hwcfg)
    if args.data_h is not None:
        args.data_h.write_text(base.emit_header_file(**merged))
    platform = base.parse_platform_cfg(args.platformcfg)
    if not base.guard_cluster_count(merged, platform, args.output_dir, args.output_offload_file_name):
        return
    graph = create_dfg(params, base.define_memory_handles(params), platform,
                       parse_bingo_core_type_ids(args.platformcfg))
    graph.bingo_compile_dfg(
        "Single-Chip DMA Chain with SDC Observers", args.output_dir,
        args.output_offload_file_name,
        extra_include_header_list=[args.data_h.name if args.data_h else "dma_chain_data.h"],
        post_execute_code=golden_checks(params))


if __name__ == "__main__":
    main()
