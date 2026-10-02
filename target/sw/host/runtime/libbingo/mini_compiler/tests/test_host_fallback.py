"""Compiler-only coverage of automatic, same-output host iDMA fallback."""

import contextlib
import io
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bingo_dfg import BingoDFG
from bingo_kernel_args import (
    HostBingoKernelCerfGatingArgs, HostBingoKernelDummyArgs, HostBingoKernelIdmaArgs,
    SnaxBingoKernelDummyArgs, SnaxBingoKernelIdma1dCopyArgs,
)
from bingo_mem_handle import BingoMemAlloc, BingoMemFixedAddr, BingoMemSymbol
from bingo_node import BingoNode
from test_cerf_fallback import compile_graph

TYPES = {(0, 0): 1, (0, 1): 2, (0, 2): 0,
         (1, 0): 1, (1, 1): 2, (1, 2): 0}
REPO_ROOT = Path(__file__).resolve().parents[7]
WORKLOAD = REPO_ROOT / (
    "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_host_fallback_2cluster")


def dummy(graph, name, cluster=0, core=2):
    host = core == 2
    n = BingoNode(0, cluster, core, node_name=name,
                  kernel_name="__host_bingo_kernel_dummy" if host else "__snax_bingo_kernel_dummy",
                  kernel_args=HostBingoKernelDummyArgs(0) if host else SnaxBingoKernelDummyArgs(0))
    graph.bingo_add_node(n)
    return n


def fixture(protect=True):
    graph = BingoDFG(1, 2, 2, True, [0], core_type_ids=TYPES)
    copy = BingoNode(0, 0, 1, node_name="copy",
                     kernel_name="__snax_bingo_kernel_idma_1d_copy",
                     kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                         BingoMemSymbol("source"), BingoMemAlloc("output", 64, "L1"), 64))
    graph.bingo_add_node(copy)
    check = dummy(graph, "check")
    graph.bingo_add_edge(copy, check)
    backup = graph.bingo_add_host_fallback(copy) if protect else None
    return graph, copy, backup, check


class HostFallbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def compile(self, graph):
        return compile_graph(graph, self.directory.name)

    def test_clone_fields_identity_host_and_idempotent_registration(self):
        graph, copy, backup, _ = fixture()
        self.assertIs(graph.bingo_add_host_fallback(copy), backup)
        self.assertEqual(len(graph._host_fallbacks), 1)
        self.assertEqual((backup.assigned_chiplet_id, backup.assigned_cluster_id,
                          backup.assigned_core_id), (0, 0, 2))
        self.assertEqual(graph.core_type_ids[(0, 2)], 0)
        self.assertEqual(backup.kernel_name, "__host_bingo_kernel_idma")
        self.assertIs(type(backup.kernel_args), HostBingoKernelIdmaArgs)
        self.assertIsNot(backup.kernel_args, copy.kernel_args)
        for field in ("src_addr", "dst_addr"):
            self.assertIs(getattr(copy.kernel_args, field), getattr(backup.kernel_args, field))
        self.assertEqual(backup.kernel_args.size, 64)
        fields = backup.kernel_args.get_c_field_assignments({copy.kernel_args.dst_addr: "ptr_output"})
        self.assertEqual(fields["dst_addr"], "(uint64_t)ptr_output")
        self.assertIn("uint64_t", fields["src_addr"])
        self.assertIsNone(backup.kernel_args._scratchpad_c_expr)

    def test_final_edges_survive_dummy_and_tag_passes(self):
        graph, copy, backup, check = fixture()
        # Edges added after registration must be read by the expansion pass.
        pred = dummy(graph, "pred", 1, 0)
        succ = dummy(graph, "succ")
        graph.bingo_add_edge(pred, copy)
        graph.bingo_add_edge(copy, succ)
        self.compile(graph)
        for u, v in ((pred, backup), (copy, backup), (backup, check), (backup, succ)):
            self.assertTrue(nx.has_path(graph, u, v), (u.node_name, v.node_name))
        for n in (copy, backup):
            packed = graph.bingo_unpack_node(graph.bingo_pack_node(n))
            self.assertTrue(packed["cond_exec_en"])
            self.assertEqual(packed["cond_exec_group_id"], n.cond_exec_group_id)
        self.assertTrue(any(n.dep_set_tag is not None for n in graph.node_list))

    def test_private_groups_initial_values_launch_order_and_exits(self):
        graph, copy, backup, _ = fixture()
        output = self.compile(graph)
        self.assertEqual((copy.cond_exec_group_id, backup.cond_exec_group_id), (0, 1))
        self.assertEqual(graph._host_fallback_masks, {0: (3, 1)})
        self.assertEqual(output.count("bingo_cerf_update("), 1)
        self.assertIn("bingo_cerf_update(0x00000003u, 0x00000001u);", output)
        markers = ["bingo_cerf_update(", "bingo_cerf_fb_set(", "bingo_cerf_fb_enable(",
                   "bingo_hw_scheduler_init("]
        self.assertEqual([output.index(m) for m in markers],
                         sorted(output.index(m) for m in markers))
        self.assertNotIn("bingo_cerf_fb_enable(0)", output)
        self.assertFalse(any(n.node_type == "gating" for n in graph.node_list))
        exits = {(n.assigned_cluster_id, n.assigned_core_id): n
                 for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"}
        for key in ((0, 1), (1, 1)):
            self.assertTrue(exits[key].cond_exec_en)
            self.assertEqual(exits[key].cond_exec_group_id, copy.cond_exec_group_id)
        self.assertFalse(exits[(1, 0)].cond_exec_en)

    def test_leaf_backup_waited_on_by_exits(self):
        graph, copy, backup, check = fixture()
        graph.remove_node(check)
        self.compile(graph)
        exit_node = next(n for n in graph.node_list if n.kernel_name == "__host_bingo_kernel_exit")
        self.assertTrue(nx.has_path(graph, copy, backup))
        self.assertTrue(nx.has_path(graph, backup, exit_node))

    def test_groups_follow_manual_and_router_groups(self):
        graph, copy, backup, check = fixture()
        manual = dummy(graph, "manual")
        manual.cond_exec_en, manual.cond_exec_group_id = True, 5
        router = dummy(graph, "router")
        expert = dummy(graph, "expert", 0, 0)
        graph.bingo_add_edge(manual, router)
        graph.bingo_add_edge(router, expert, cond_dic={"mode": "static", "write_mask": 1})
        graph.bingo_add_edge(router, copy)
        self.compile(graph)
        self.assertEqual((expert.cond_exec_group_id, copy.cond_exec_group_id,
                          backup.cond_exec_group_id), (6, 7, 8))

    def test_no_request_output_matches_passes_disabled(self):
        graph, _, _, _ = fixture(False)
        original = self.compile(graph)
        reference, _, _, _ = fixture(False)
        with patch.object(reference, "_expand_host_fallbacks"), \
                patch.object(reference, "_compile_host_fallbacks"):
            self.assertEqual(self.compile(reference), original)
        self.assertNotIn("bingo_cerf_update(", original)
        self.assertNotIn("bingo_cerf_fb_set(", original)

    def test_different_type_handwritten_table_is_preserved(self):
        graph, copy, backup, _ = fixture()
        primary = dummy(graph, "manual_primary", 0, 0)
        manual_backup = dummy(graph, "manual_backup")
        for n, group in ((primary, 7), (manual_backup, 11)):
            n.cond_exec_en, n.cond_exec_group_id = True, group
        graph.bingo_add_cerf_fallback(primary, manual_backup)
        output = self.compile(graph)
        self.assertEqual((copy.cond_exec_group_id, backup.cond_exec_group_id), (12, 13))
        self.assertIn("bingo_cerf_update(0x00003000u, 0x00001000u);", output)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 7, 11);", output)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 1), 12, 13);", output)
        self.assertEqual(output.count("bingo_cerf_fb_enable("), 1)
        self.assertIn("(1u << BINGO_CORE_TYPE_ID(0, 0)) | (1u << BINGO_CORE_TYPE_ID(0, 1))", output)
        self.assertLess(output.index("bingo_cerf_update("), output.index("bingo_cerf_fb_set("))

    def test_scope_and_core_types_rejected(self):
        changes = [
            ("num_chiplets", 2, "single chiplet"),
            ("is_host_as_acc", False, "is_host_as_acc"),
        ]
        for field, value, message in changes:
            graph, copy, _, _ = fixture(False)
            setattr(graph, field, value)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
                graph.bingo_add_host_fallback(copy)
        for types, message in (({}, "core_type_ids"),
                               ({**TYPES, (0, 1): 0}, "nonzero"),
                               ({**TYPES, (0, 2): 1}, "host slot")):
            graph, copy, _, _ = fixture(False)
            graph.core_type_ids = types
            with self.subTest(types=types), self.assertRaisesRegex(ValueError, message):
                graph.bingo_add_host_fallback(copy)
        graph, copy, _, _ = fixture(False)
        graph.remove_node(copy)
        with self.assertRaisesRegex(ValueError, "belong"):
            graph.bingo_add_host_fallback(copy)

    def test_nonordinary_and_unwhitelisted_nodes_rejected(self):
        changes = [("node_type", "dummy"), ("node_type", "gating"),
                   ("node_type", "entry"), ("node_type", "exit"), ("cond_exec_en", True),
                   ("kernel_name", "__snax_bingo_kernel_exit"),
                   ("kernel_name", "__snax_bingo_kernel_gemm"),
                   ("kernel_name", "__snax_bingo_kernel_int32_add"),
                   ("kernel_args", SnaxBingoKernelDummyArgs(0)),
                   ("assigned_core_id", 2), ("assigned_cluster_id", -1)]
        for field, value in changes:
            graph, copy, _, _ = fixture(False)
            setattr(copy, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                graph.bingo_add_host_fallback(copy)

    def test_conditional_changes_after_registration_rejected(self):
        graph, copy, _, check = fixture()
        graph[copy][check]["cond"] = True
        with self.assertRaisesRegex(ValueError, "unconditional"):
            self.compile(graph)
        graph, copy, _, _ = fixture()
        copy.cond_exec_en = True
        with self.assertRaisesRegex(ValueError, "unconditional"):
            self.compile(graph)

    def test_overlap_and_bare_addresses_rejected(self):
        alloc = BingoMemAlloc("shared", 128)
        for src, dst in ((alloc, alloc.view(63)), (alloc.view(10), alloc.view(20)),
                         (BingoMemSymbol("x", 1), BingoMemSymbol("x", 2)),
                         (BingoMemFixedAddr(100), BingoMemFixedAddr(120)),
                         (1234, alloc), (alloc, 1234)):
            graph, copy, _, _ = fixture(False)
            copy.kernel_args.src_addr, copy.kernel_args.dst_addr = src, dst
            with self.subTest(src=src, dst=dst), self.assertRaisesRegex(ValueError, "overlap|bare"):
                graph.bingo_add_host_fallback(copy)

    def test_adjacent_views_and_distinct_allocations_allowed(self):
        alloc = BingoMemAlloc("shared", 128)
        graph, copy, _, _ = fixture(False)
        copy.kernel_args.src_addr, copy.kernel_args.dst_addr = alloc, alloc.view(64)
        backup = graph.bingo_add_host_fallback(copy)
        self.assertIs(backup.kernel_args.dst_addr, copy.kernel_args.dst_addr)
        self.compile(graph)

    def test_size_rejected(self):
        for size in (0, -1, "64"):
            graph, copy, _, _ = fixture(False)
            copy.kernel_args.size = size
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "size"):
                graph.bingo_add_host_fallback(copy)

    def test_second_node_of_same_type_rejected(self):
        graph, _, _, _ = fixture()
        other = BingoNode(0, 1, 1, kernel_name="__snax_bingo_kernel_idma_1d_copy",
                          kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                              BingoMemSymbol("x"), BingoMemAlloc("y", 64), 64))
        graph.bingo_add_node(other)
        with self.assertRaisesRegex(ValueError, "one host fallback"):
            graph.bingo_add_host_fallback(other)

    def test_handwritten_same_type_conflicts_before_or_after_registration(self):
        for before in (True, False):
            graph, copy, _, _ = fixture(False)
            other, manual_backup = dummy(graph, "other", 1, 1), dummy(graph, "manual_backup")
            if before:
                graph.bingo_add_cerf_fallback(other, manual_backup)
                with self.assertRaisesRegex(ValueError, "handwritten"):
                    graph.bingo_add_host_fallback(copy)
            else:
                graph.bingo_add_host_fallback(copy)
                graph.bingo_add_cerf_fallback(other, manual_backup)
                with self.assertRaisesRegex(ValueError, "handwritten"):
                    self.compile(graph)

    def test_protected_slot_suffix_rejected(self):
        graph, copy, _, check = fixture()
        tail = dummy(graph, "tail", 0, 1)
        graph.bingo_add_edge(check, tail)
        with self.assertRaisesRegex(ValueError, "outside CERF group"):
            self.compile(graph)

    def test_substitute_late_task_only_warns(self):
        graph, copy, _, check = fixture()
        tail = dummy(graph, "late", 1, 1)
        graph.bingo_add_edge(check, tail)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.compile(graph)
        self.assertEqual(len(caught), 1)
        self.assertIn("both cores", str(caught[0].message))
        self.assertIn("fallback stalls", str(caught[0].message))

    def test_private_groups_cannot_be_controlled_by_gating(self):
        for use_mask in (True, False):
            graph, _, _, _ = fixture()
            gating = dummy(graph, "gating")
            if use_mask:
                gating.kernel_args = HostBingoKernelCerfGatingArgs(cerf_controlled_mask=3)
            else:
                gating.cerf_write_groups = [1]
            with self.subTest(mask=use_mask), self.assertRaisesRegex(ValueError, "private"):
                self.compile(graph)

    def test_group_overflow(self):
        graph, _, _, _ = fixture()
        manual = dummy(graph, "manual")
        manual.cond_exec_en, manual.cond_exec_group_id = True, 30
        with self.assertRaisesRegex(ValueError, "overflow"):
            self.compile(graph)

    def test_cyclic_user_graph_rejected(self):
        graph, copy, _, check = fixture()
        graph.bingo_add_edge(check, copy)
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.compile(graph)


class HostFallbackWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        platform = root / "occamy.h"
        platform.write_text(
            "#define N_CHIPLETS 1\n#define CHIPLET_ID_0 0x00\n"
            "#define N_CLUSTERS_PER_CHIPLET 2\n#define N_CORES_PER_CLUSTER 2\n"
            "#define BINGO_CORE_TYPE_ID(cluster, core) "
            "(((cluster) == 0 && (core) == 0) ? 1 : "
            "((cluster) == 0 && (core) == 1) ? 2 : "
            "((cluster) == 1 && (core) == 0) ? 1 : "
            "((cluster) == 1 && (core) == 1) ? 2 : 0)\n")
        original = BingoDFG.bingo_compile_dfg

        def capture(graph, *args, **kwargs):
            self.graph = graph
            return original(graph, *args, **kwargs)

        argv = [str(WORKLOAD / "main_bingo.py"), "--output_dir", str(root),
                "--data_h", str(root / "dma_host_fallback_data.h"),
                "-c", str(WORKLOAD / "params.hjson"), "--hwcfg", "/dev/null",
                "--platformcfg", str(platform)]
        with patch.object(sys, "argv", argv), \
                patch.object(sys, "path", [str(REPO_ROOT / "util/sim/common"), *sys.path]), \
                patch.dict(sys.modules), patch.object(BingoDFG, "bingo_compile_dfg", capture), \
                patch.object(BingoDFG, "bingo_visualize_dfg"), \
                contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(WORKLOAD / "main_bingo.py"), run_name="__main__")
        self.output = (root / "offload_bingo_hw.h").read_text()

    def test_single_copy_same_output_and_consumer(self):
        graph = self.graph
        copy, backup = next(iter(graph._host_fallbacks.items()))
        check = next(n for n in graph.node_list if n.node_name == "check")
        self.assertIs(check.kernel_args.output_data_addr, copy.kernel_args.dst_addr)
        self.assertIs(backup.kernel_args.dst_addr, copy.kernel_args.dst_addr)
        self.assertTrue(nx.has_path(graph, backup, check))
        self.assertEqual(self.output.count("uint64_t ptr_A_L1 = bingo_l1_alloc("), 1)
        self.assertFalse(any(n.node_type == "gating" for n in graph.node_list))
        self.assertEqual((copy.node_id, check.node_id, backup.node_id), (0, 1, 2))
        exits = {(n.assigned_cluster_id, n.assigned_core_id): n.node_id
                 for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"}
        self.assertEqual((exits[(0, 1)], exits[(1, 1)]), (5, 7))

    def test_exported_task_ids_for_checker(self):
        import importlib.util
        path = REPO_ROOT / "target/sim/automation/test/3_start_bingo_watchdog_sim.py"
        spec = importlib.util.spec_from_file_location("host_fallback_driver", path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        ids = driver.host_fallback_task_ids(Path(self.directory.name) / "final_dfg.csv")
        self.assertEqual(ids, {"copy": 0, "check": 1, "backup": 2, "primary_exit": 5,
                               "substitute_exit": 7})
        self.assertEqual(driver.SCENARIOS["s34"]["fault_gid"], ids["copy"])


if __name__ == "__main__":
    unittest.main()
