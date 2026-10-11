"""S1a observer graph, private gating, eligibility and conflict checks."""
from pathlib import Path
import importlib.util
import sys
import tempfile
import unittest

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bingo_kernel_args import SnaxBingoKernelInt32AddArgs
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from test_auto_fallback import chain
from test_auto_fallback_conflicts import register_group
from test_cerf_fallback import compile_graph, node
from test_host_fallback import TYPES


class DmrCompilerTests(unittest.TestCase):
    def test_graph_observers_do_not_gate_consumers(self):
        graph, tasks, check = chain(4)
        shadows = graph.bingo_auto_dmr(0, 2)
        self.assertEqual(len(shadows), 4)
        for primary, shadow in zip(tasks, shadows):
            self.assertNotEqual(primary.assigned_cluster_id, shadow.assigned_cluster_id)
            self.assertEqual(graph.core_type_ids[(primary.assigned_cluster_id, primary.assigned_core_id)],
                             graph.core_type_ids[(shadow.assigned_cluster_id, shadow.assigned_core_id)])
            compare = graph._sdc_pairs[primary][1]
            self.assertEqual((compare.assigned_cluster_id, compare.assigned_core_id),
                             (shadow.assigned_cluster_id, shadow.assigned_core_id))
            self.assertTrue(graph.has_edge(primary, compare))
            self.assertTrue(graph.has_edge(shadow, compare))
            self.assertFalse(nx.has_path(graph, compare, check))
            self.assertTrue(all(not nx.has_path(graph, compare, consumer) for consumer in tasks))
            self.assertIs(shadow.kernel_args.src_addr, primary.kernel_args.src_addr)
            self.assertIsNot(shadow.kernel_args.dst_addr, primary.kernel_args.dst_addr)
            self.assertEqual(shadow.kernel_args.dst_addr.size, primary.kernel_args.size)
        with tempfile.TemporaryDirectory() as out:
            output = compile_graph(graph, out)
        self.assertTrue(nx.is_directed_acyclic_graph(graph))
        host_exit, = [n for n in graph if n.kernel_name == "__host_bingo_kernel_exit"]
        for primary, (shadow, compare) in graph._sdc_pairs.items():
            self.assertTrue(nx.has_path(graph, primary, compare))
            self.assertTrue(nx.has_path(graph, shadow, compare))
            self.assertTrue(nx.has_path(graph, compare, host_exit))
            self.assertFalse(nx.has_path(graph, compare, check))
            self.assertFalse(any(nx.has_path(graph, compare, consumer) for consumer in tasks))
            self.assertTrue(shadow.cond_exec_en and compare.cond_exec_en)
            self.assertEqual(shadow.cond_exec_group_id, graph._sdc_group)
            self.assertEqual(compare.cond_exec_group_id, graph._sdc_group)
        self.assertEqual(output.count("bingo_cerf_update("), 1)
        self.assertIn("bingo_test_cfg.sdc_enable ? 0x00000001u : 0u", output)
        self.assertIn('#include "bingo_test_cfg.h"', output)
        self.assertIn("[Host] SDC report: %u mismatches", output)
        self.assertEqual(graph._sdc_status.mem_level, "L3")

    def test_manual_groups_are_reserved_before_private_sdc(self):
        graph, tasks, _ = chain(1)
        unrelated = node(graph, "manual", 0, 0, 7)
        graph.bingo_add_dmr(tasks[0])
        with tempfile.TemporaryDirectory() as out:
            output = compile_graph(graph, out)
        self.assertEqual(graph._sdc_group, 8)
        self.assertEqual(unrelated.cond_exec_group_id, 7)
        self.assertIn("bingo_cerf_update(0x00000100u, bingo_test_cfg.sdc_enable ? 0x00000100u : 0u);", output)

    def test_ineligible_nodes_skip_and_record_reasons(self):
        for change, reason in (
                (lambda n: setattr(n, "cond_exec_en", True), "unconditional"),
                (lambda n: setattr(n, "non_idempotent", True), "A4"),
                (lambda n: setattr(n, "kernel_name", "unknown"), "whitelisted"),
                (lambda n: setattr(n.kernel_args, "src_addr", 123), "bare"),
                (lambda n: setattr(n.kernel_args, "src_addr", n.kernel_args.dst_addr), "overlap")):
            with self.subTest(reason=reason):
                graph, tasks, _ = chain(1)
                change(tasks[0])
                self.assertEqual(graph.bingo_auto_dmr(0, 2), [])
                row, = graph._sdc_report
                self.assertFalse(row["selected"])
                self.assertIn(reason, row["reason"])
                self.assertEqual(len(graph), 2)

    def test_int32_add_requires_shared_inputs_and_distinct_output(self):
        graph, tasks, _ = chain(1)
        primary = tasks[0]
        primary.kernel_name = "__snax_bingo_kernel_int32_add"
        primary.kernel_args = SnaxBingoKernelInt32AddArgs(
            BingoMemAlloc("a", 64, "L3"), BingoMemSymbol("b"),
            BingoMemAlloc("c", 64, "L1", cluster_id=0), 16)
        shadow = graph.bingo_add_dmr(primary)
        self.assertIsNotNone(shadow)
        self.assertEqual(shadow.kernel_args.c_addr.mem_level, "L1")
        self.assertEqual(shadow.kernel_args.c_addr.cluster_id, 1)
        self.assertEqual(shadow.kernel_args.c_addr.size, 64)
        graph, tasks, _ = chain(1)
        primary = tasks[0]
        primary.kernel_name = "__snax_bingo_kernel_int32_add"
        primary.kernel_args = SnaxBingoKernelInt32AddArgs(
            BingoMemAlloc("a", 64, "L1"), BingoMemSymbol("b"), BingoMemAlloc("c", 64), 16)
        self.assertIsNone(graph.bingo_add_dmr(primary))
        self.assertIn("cluster-local L1", graph._sdc_report[0]["reason"])

    def test_group_conflicts_in_both_registration_orders(self):
        for before in (True, False):
            graph, _, _ = chain(1)
            with self.subTest(before=before):
                if before:
                    register_group(graph, 0)
                    with self.assertRaisesRegex(ValueError, "CERF group"):
                        graph.bingo_auto_dmr(0, 2)
                    self.assertEqual(graph._sdc_types, set())
                else:
                    graph.bingo_auto_dmr(0, 2)
                    register_group(graph, 0)
                    with tempfile.TemporaryDirectory() as out:
                        with self.assertRaisesRegex(ValueError, "CERF group"):
                            compile_graph(graph, out)

    def test_p7b_automatic_fallback_is_mutually_exclusive_even_with_no_candidates(self):
        for first in ("dmr", "fallback"):
            for eligible in (True, False):
                graph, tasks, _ = chain(1)
                if not eligible:
                    tasks[0].non_idempotent = True
                with self.subTest(first=first, eligible=eligible):
                    if first == "dmr":
                        graph.bingo_auto_dmr(0, 2)
                        with self.assertRaisesRegex(ValueError, "DMR"):
                            graph.bingo_auto_host_fallback(0, 2)
                    else:
                        graph.bingo_auto_host_fallback(0, 2)
                        with self.assertRaisesRegex(ValueError, "host fallback"):
                            graph.bingo_auto_dmr(0, 2)

    def test_idempotent_registration_and_snapshot_semantics(self):
        graph, tasks, _ = chain(1)
        shadow = graph.bingo_add_dmr(tasks[0])
        self.assertIs(graph.bingo_add_dmr(tasks[0]), shadow)
        self.assertEqual(graph.bingo_auto_dmr(0, 2), [shadow])
        self.assertEqual(len(graph._sdc_pairs), 1)
        late = node(graph, "late_non_whitelisted", 1, 1)
        graph._expand_dmr()
        self.assertNotIn(late, graph._sdc_pairs)

    def test_no_request_emits_no_sdc_products_or_c_code(self):
        graph, _, _ = chain(1)
        with tempfile.TemporaryDirectory() as out:
            output = compile_graph(graph, out)
            self.assertFalse((Path(out) / "sdc_report.json").exists())
        self.assertNotIn("SDC", output)
        self.assertNotIn("sdc", output)

    def test_real_chain_has_six_observers_and_post_execute_goldens(self):
        root = Path(__file__).resolve().parents[7]
        path = root / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_chain_sdc_2cluster/main_bingo.py"
        spec = importlib.util.spec_from_file_location("test_real_sdc_chain", path)
        recipe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(recipe)
        params = dict(A_size=384, chain_len=6, chunk_size=64, num_clusters=2)
        platform = dict(num_chiplets=1, num_clusters_per_chiplet=2,
                        num_cores_per_cluster=2, chiplet_ids=[0])
        graph = recipe.create_dfg(params, recipe.base.define_memory_handles(params), platform, TYPES)
        self.assertEqual(len(graph._sdc_pairs), 6)
        self.assertEqual({n.node_id for n in graph._sdc_pairs}, set(range(6)))
        self.assertEqual(len(graph), 19)
        self.assertEqual({n.assigned_cluster_id for pair in graph._sdc_pairs.values() for n in pair}, {1})
        with tempfile.TemporaryDirectory() as out:
            header = compile_graph(graph, out)
        self.assertEqual(graph._sdc_status.size, 24)
        by_name = {n.node_name: n for n in graph}
        self.assertNotIn(by_name["copy_cluster1"], graph._sdc_pairs)
        for primary, (_, compare) in graph._sdc_pairs.items():
            self.assertFalse(any(nx.has_path(graph, compare, main) for main in graph._sdc_pairs))
        checks = "\n".join(recipe.golden_checks(params))
        self.assertIn("Check [A_chain_cluster0]", checks)
        self.assertIn("Check [A_cluster1]", checks)
        self.assertIn("return 2", checks)
        self.assertNotIn("__host_bingo_kernel_check_result", header)


if __name__ == "__main__":
    unittest.main()
