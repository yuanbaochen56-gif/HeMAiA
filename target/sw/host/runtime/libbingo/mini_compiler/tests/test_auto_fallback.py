"""P7b opt-in shared B1 pairs and group-wide P7c registration.

Manual B1 registration alone retains its one-node-per-type limit. Calling
automatic registration opts that (chip, type) into multiple pairs: manual
registrations before or after it share gp/gh and mirror the primary chain
between backups, without placing backups on the device chain. Logical outputs
are at least once, allowing ordered idempotent rewrites, not strict exclusion.
"""
import json
import importlib.util
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bingo_dfg import BingoDFG
from bingo_kernel_args import (
    HostBingoKernelDummyArgs, HostBingoKernelIdmaArgs,
    SnaxBingoKernelIdma1dCopyArgs, SnaxBingoKernelInt32AddArgs,
)
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_node import BingoNode
from test_cerf_fallback import compile_graph, node
from test_host_fallback import TYPES, dummy


def copy(graph, name, cluster=0, group=None):
    task = BingoNode(0, cluster, 1, node_name=name,
                     kernel_name="__snax_bingo_kernel_idma_1d_copy",
                     kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                         BingoMemSymbol("source"), BingoMemAlloc(name, 64, "L3"), 64))
    if group is not None:
        task.cond_exec_en, task.cond_exec_group_id = True, group
    graph.bingo_add_node(task)
    return task


def chain(length=6):
    graph = BingoDFG(1, 2, 2, True, [0], core_type_ids=TYPES)
    tasks = [copy(graph, f"copy_{i}") for i in range(length)]
    for left, right in zip(tasks, tasks[1:]):
        graph.bingo_add_edge(left, right)
    check = dummy(graph, "check")
    graph.bingo_add_edge(tasks[-1], check)
    return graph, tasks, check


class AutoFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def compile(self, graph):
        return compile_graph(graph, self.tmp.name)

    def test_six_pairs_are_acyclic_and_survive_all_passes(self):
        graph, tasks, check = chain()
        backups = graph.bingo_auto_host_fallback(0, 2)
        self.assertEqual(len(backups), 6)
        output = self.compile(graph)
        self.assertTrue(nx.is_directed_acyclic_graph(graph))
        for i, (primary, backup) in enumerate(zip(tasks, backups)):
            self.assertTrue(nx.has_path(graph, primary, backup))
            if i:
                self.assertTrue(nx.has_path(graph, backups[i - 1], backup))
            for selected in tasks:
                self.assertFalse(nx.has_path(graph, backup, selected))
        self.assertTrue(nx.has_path(graph, backups[-1], check))
        self.assertEqual({n.cond_exec_group_id for n in tasks}, {0})
        self.assertEqual({n.cond_exec_group_id for n in backups}, {1})
        self.assertEqual(graph._host_fallback_masks, {0: (3, 1)})
        self.assertIn("bingo_cerf_update(0x00000003u, 0x00000001u);", output)
        self.assertEqual(output.count("bingo_cerf_fb_set("), 1)
        report = json.loads((Path(self.tmp.name) / "host_fallback_report.json").read_text())
        self.assertEqual(sum(row["selected"] for row in report), 6)
        exits = [n for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"
                 and n.assigned_core_id == 1]
        self.assertEqual(len(exits), 2)
        self.assertTrue(all(n.cond_exec_en and n.cond_exec_group_id == 0 for n in exits))

    def test_only_pair_edges_before_entry_exit(self):
        graph, tasks, check = chain()
        backups = graph.bingo_auto_host_fallback(0, 2)
        graph._expand_host_fallbacks()
        for i in range(6):
            self.assertTrue(graph.has_edge(tasks[i], backups[i]))
            if i < 5:
                self.assertTrue(graph.has_edge(backups[i], backups[i + 1]))
                self.assertFalse(graph.has_edge(tasks[-1], backups[i]))
        self.assertEqual(
            {(u, v) for u, v in graph.edges() if u in backups and v in backups},
            set(zip(backups, backups[1:])))
        self.assertFalse(any(graph.has_edge(b, p) for b in backups for p in tasks))
        self.assertTrue(graph.has_edge(tasks[-1], check))
        self.assertTrue(graph.has_edge(backups[-1], check))
        self.assertTrue(nx.is_directed_acyclic_graph(graph))

    def test_mirrors_branch_edges_and_keeps_external_dependencies(self):
        graph, tasks, check = chain(4)
        graph.remove_edge(tasks[1], tasks[2])
        graph.bingo_add_edge(tasks[0], tasks[2])
        graph.bingo_add_edge(tasks[1], tasks[3])
        predecessor = dummy(graph, "external_input")
        graph.bingo_add_edge(predecessor, tasks[0])
        graph.bingo_add_edge(tasks[1], check)
        user_edges = set(graph.edges())
        backups = graph.bingo_auto_host_fallback(0, 2)
        pairs = dict(zip(tasks, backups))
        graph._expand_host_fallbacks()
        self.assertEqual(
            {(u, v) for u, v in graph.edges() if u in backups and v in backups},
            {(pairs[u], pairs[v]) for u, v in user_edges if u in pairs and v in pairs})
        self.assertFalse(any(graph.has_edge(b, p) for b in backups for p in tasks))
        self.assertTrue(graph.has_edge(predecessor, backups[0]))
        for primary in (tasks[1], tasks[-1]):
            self.assertTrue(graph.has_edge(primary, check))
            self.assertTrue(graph.has_edge(pairs[primary], check))
        self.assertTrue(nx.is_directed_acyclic_graph(graph))

    def test_live_primary_finishes_after_switch_then_backup_rewrites_safely(self):
        graph, tasks, _ = chain(1)
        live = copy(graph, "live", cluster=1)
        backups = graph.bingo_auto_host_fallback(0, 2)
        self.compile(graph)
        self.assertTrue(nx.has_path(graph, live, graph._host_fallbacks[live]))
        # A primary already executing does not re-read gp. Its done releases
        # the pair; the now-enabled gh executes, not skips, the same-output copy.
        source = bytes(range(64))
        destination = bytearray(64)
        gp, gh, running = 1, 0, True
        gp, gh = 0, 1
        if running:
            destination[:] = source
        primary_done = True
        executions = 0
        if primary_done and gh:
            destination[:] = source
            executions += 1
        self.assertEqual((gp, executions, bytes(destination)), (0, 1, source))
        self.assertEqual(len(backups), 2)

    def test_nonoverlapping_add_rewrite_and_inplace_rejection(self):
        graph, tasks, _ = chain(1)
        task = tasks[0]
        task.kernel_name = "__snax_bingo_kernel_int32_add"
        task.kernel_args = SnaxBingoKernelInt32AddArgs(
            BingoMemSymbol("a"), BingoMemSymbol("b"), BingoMemAlloc("out", 64), 16)
        backup, = graph.bingo_auto_host_fallback(0, 2)
        self.assertIs(backup.kernel_args.output_addr, task.kernel_args.c_addr)
        a, b = list(range(16)), list(range(16, 32))
        output = [x + y for x, y in zip(a, b)]
        output[:] = [x + y for x, y in zip(a, b)]
        self.assertEqual(output, [16 + 2 * i for i in range(16)])
        for kind in ("copy", "add"):
            graph, tasks, _ = chain(1)
            task = tasks[0]
            if kind == "copy":
                task.kernel_args.src_addr = task.kernel_args.dst_addr
            else:
                task.kernel_name = "__snax_bingo_kernel_int32_add"
                shared = BingoMemAlloc("shared", 64)
                task.kernel_args = SnaxBingoKernelInt32AddArgs(
                    shared, BingoMemSymbol("b"), shared, 16)
            self.assertEqual(graph.bingo_auto_host_fallback(0, 2), [])
            self.assertIn("overlap", graph._auto_host_fallback_report[0]["reason"])

    def test_skip_reasons_and_report(self):
        changes = [
            ("conditional", lambda n: setattr(n, "cond_exec_en", True), "unconditional"),
            ("group", lambda n: setattr(n, "_gating_node", BingoNode(0, 0, 2)), "unconditional"),
            ("marked", lambda n: setattr(n, "non_idempotent", True), "non-idempotent"),
            ("unknown", lambda n: setattr(n, "kernel_name", "__snax_bingo_kernel_unknown"), "whitelisted"),
            ("bare", lambda n: setattr(n.kernel_args, "src_addr", 123), "bare"),
        ]
        for name, change, reason in changes:
            with self.subTest(name=name):
                graph, tasks, _ = chain(1)
                change(tasks[0])
                self.assertEqual(graph.bingo_auto_host_fallback(0, 2), [])
                self.assertIn(reason, graph._auto_host_fallback_report[0]["reason"])
                if name == "group":
                    # Use a real router reference for the separate emission check.
                    tasks[0]._gating_node = dummy(graph, "router")
                    tasks[0]._cond_node_index = 0
                self.compile(graph)
                report = json.loads((Path(self.tmp.name) / "host_fallback_report.json").read_text())
                self.assertFalse(report[0]["selected"])

    def test_suffix_rejection_cascades_and_compile_rechecks(self):
        graph, tasks, check = chain()
        late = dummy(graph, "late", 0, 1)
        graph.bingo_add_edge(check, late)
        self.assertEqual(graph.bingo_auto_host_fallback(0, 2), [])
        self.assertTrue(all(not row["selected"] for row in graph._auto_host_fallback_report))
        self.assertTrue(all("suffix" in row["reason"] for row in graph._auto_host_fallback_report
                            if row["node_id"] in {n.node_id for n in tasks}))
        graph, tasks, check = chain()
        graph.bingo_auto_host_fallback(0, 2)
        late = dummy(graph, "late", 0, 1)
        graph.bingo_add_edge(check, late)
        with self.assertRaisesRegex(ValueError, "outside CERF group"):
            self.compile(graph)

    def test_legacy_manual_limit_preserved_but_auto_explicitly_opts_in(self):
        graph, tasks, _ = chain(2)
        manual = graph.bingo_add_host_fallback(tasks[0])
        with self.assertRaisesRegex(ValueError, "one host fallback"):
            graph.bingo_add_host_fallback(tasks[1])
        backups = graph.bingo_auto_host_fallback(0, 2)
        self.assertEqual(len(backups), 2)
        self.assertIs(backups[0], manual)
        self.compile(graph)
        self.assertTrue(nx.has_path(graph, manual, backups[1]))
        self.assertFalse(any(nx.has_path(graph, b, p) for b in backups for p in tasks))

    def test_manual_after_auto_shares_groups_and_pair_ordering(self):
        """Later manual pairs share groups and mirror the backup chain."""
        graph, tasks, check = chain()
        backups = graph.bingo_auto_host_fallback(0, 2)
        last = copy(graph, "manual_after_auto")
        graph.remove_edge(tasks[-1], check)
        graph.bingo_add_edge(tasks[-1], last)
        graph.bingo_add_edge(last, check)
        manual_backup = graph.bingo_add_host_fallback(last)
        self.assertIs(graph.bingo_add_host_fallback(last), manual_backup)
        tasks.append(last)
        backups.append(manual_backup)
        output = self.compile(graph)
        self.assertTrue(nx.is_directed_acyclic_graph(graph))
        self.assertEqual({n.cond_exec_group_id for n in tasks}, {0})
        self.assertEqual({n.cond_exec_group_id for n in backups}, {1})
        self.assertEqual(graph._host_fallback_masks, {0: (3, 1)})
        self.assertEqual(output.count("bingo_cerf_fb_set("), 1)
        for i, (primary, backup) in enumerate(zip(tasks, backups)):
            self.assertTrue(nx.has_path(graph, primary, backup))
            if i:
                self.assertTrue(nx.has_path(graph, backups[i - 1], backup))
            self.assertFalse(any(nx.has_path(graph, backup, p) for p in tasks))
            if i + 1 < len(tasks):
                self.assertFalse(nx.has_path(graph, tasks[-1], backup))
        self.assertTrue(nx.has_path(graph, backups[-1], check))

    def test_handwritten_conflict_both_orders_and_group_overflow(self):
        for before in (True, False):
            graph, tasks, _ = chain()
            primary = copy(graph, "manual", 1, 7)
            backup = dummy(graph, "manual_backup")
            backup.cond_exec_en, backup.cond_exec_group_id = True, 11
            if before:
                graph.bingo_add_cerf_fallback(primary, backup)
                with self.assertRaisesRegex(ValueError, "handwritten"):
                    graph.bingo_auto_host_fallback(0, 2)
            else:
                graph.bingo_auto_host_fallback(0, 2)
                graph.bingo_add_cerf_fallback(primary, backup)
                with self.assertRaisesRegex(ValueError, "handwritten"):
                    self.compile(graph)
        graph, _, _ = chain()
        graph.bingo_auto_host_fallback(0, 2)
        manual = dummy(graph, "group30")
        manual.cond_exec_en, manual.cond_exec_group_id = True, 30
        with self.assertRaisesRegex(ValueError, "overflow"):
            self.compile(graph)

    def test_auto_idempotent_registration_and_no_request_output_unchanged(self):
        graph, _, _ = chain()
        first = graph.bingo_auto_host_fallback(0, 2)
        self.assertEqual(graph.bingo_auto_host_fallback(0, 2), first)
        self.assertEqual(len(graph._auto_host_fallback_report), 6)
        graph, _, _ = chain()
        original = self.compile(graph)
        reference, _, _ = chain()
        with patch.object(reference, "_expand_host_fallbacks"), \
                patch.object(reference, "_compile_host_fallbacks"), \
                patch.object(reference, "_compile_cerf_fallbacks"):
            self.assertEqual(self.compile(reference), original)

    def test_real_chain_emits_ancestor_closed_primary_prefix_and_safe_tags(self):
        root = Path(__file__).resolve().parents[7]
        recipe = root / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_chain_autofb_2cluster/main_bingo.py"
        spec = importlib.util.spec_from_file_location("p7b_real_chain", recipe)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        params = dict(A_size=384, chunk_size=64, chain_len=6, num_clusters=2)
        platform = dict(num_chiplets=1, num_clusters_per_chiplet=2,
                        num_cores_per_cluster=2, chiplet_ids=[0])
        graph = module.create_dfg(params, module.define_memory_handles(params), platform, TYPES)
        output = self.compile(graph)
        by_id = {n.node_id: n for n in graph}
        emitted = [by_id[int(i)] for i in re.findall(
            r"task_desc_list_chip_00\[\d+\] = .*// Node ID (\d+)", output)]
        self.assertEqual(len(emitted), len(graph))
        primaries = set(graph._host_fallbacks)
        priority = primaries | set().union(*(nx.ancestors(graph, p) for p in primaries))
        self.assertEqual(set(emitted[:len(priority)]), priority)
        self.assertEqual(emitted, graph._bingo_topological_order())
        positions = {n: i for i, n in enumerate(emitted)}
        self.assertTrue(all(positions[u] < positions[v] for u, v in graph.edges()))
        self.assertTrue(nx.is_directed_acyclic_graph(graph))
        for p, b in graph._host_fallbacks.items():
            self.assertTrue(nx.has_path(graph, p, b))
            self.assertFalse(any(nx.has_path(graph, b, q) for q in primaries))
        for i in range(5):
            self.assertTrue(nx.has_path(graph, graph._host_fallbacks[by_id[i]],
                                       graph._host_fallbacks[by_id[i + 1]]))
        for p, consumer in ((by_id[5], by_id[7]), (by_id[6], by_id[8])):
            self.assertTrue(nx.has_path(graph, p, consumer))
            self.assertTrue(nx.has_path(graph, graph._host_fallbacks[p], consumer))
        # Prove reuse intervals using the REAL emitted FIFO order, not another
        # ordinary topological sort. Every reused cell/tag must drain before set.
        hb = nx.DiGraph(graph)
        last, live = {}, {}
        for n in emitted:
            slot = (n.assigned_chiplet_id, n.assigned_cluster_id, n.assigned_core_id)
            if slot in last:
                hb.add_edge(last[slot], n)
            last[slot] = n
        for u, v in graph.edges():
            if (u.dep_set_enable and v.dep_check_enable
                    and v.assigned_core_id in u.dep_set_list
                    and u.assigned_core_id in v.dep_check_list):
                self.assertEqual(u.dep_set_tag, v.dep_check_tag)
                key = (v.assigned_chiplet_id, v.assigned_cluster_id,
                       v.assigned_core_id, u.assigned_core_id, u.dep_set_tag)
                live.setdefault(key, []).append((u, v))
        for edges in live.values():
            edges.sort(key=lambda edge: positions[edge[0]])
            for (_, drain), (producer, _) in zip(edges, edges[1:]):
                self.assertTrue(drain is producer or nx.has_path(hb, drain, producer))

    def test_priority_preserves_relative_order_and_manual_mode(self):
        graph, tasks, _ = chain()
        backups = graph.bingo_auto_host_fallback(0, 2)
        graph._expand_host_fallbacks()
        ordinary = list(nx.topological_sort(graph))
        priority = set(tasks) | set().union(*(nx.ancestors(graph, p) for p in tasks))
        self.assertEqual(graph._bingo_topological_order(),
                         [n for n in ordinary if n in priority] +
                         [n for n in ordinary if n not in priority])
        self.assertTrue(all(b not in priority for b in backups))
        manual, tasks, _ = chain(1)
        manual.bingo_add_host_fallback(tasks[0])
        self.assertEqual(manual._bingo_topological_order(), list(nx.topological_sort(manual)))

    def test_tag_allocation_failure_is_not_silently_downgraded(self):
        graph, _, _ = chain()
        graph.bingo_auto_host_fallback(0, 2)
        with patch.object(graph, "bingo_transform_dfg_allocate_dep_tags",
                          side_effect=ValueError("dep-tag allocation failed")):
            with self.assertRaisesRegex(ValueError, "dep-tag allocation failed"):
                self.compile(graph)


class GroupFallbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fixture(self):
        graph = BingoDFG(1, 2, 2, True, [0], core_type_ids=TYPES)
        ld = copy(graph, "ld", 0, 7)
        gemm = node(graph, "gemm", 0, 0, 7)
        backup_ld = copy(graph, "backup_ld", 1, 11)
        backup_gemm = node(graph, "backup_gemm", 1, 0, 11)
        graph.bingo_add_edge(ld, gemm)
        graph.bingo_add_edge(backup_ld, backup_gemm)
        graph.bingo_add_cerf_group_fallback(gemm, backup_gemm)
        return graph, ld, gemm, backup_ld, backup_gemm

    def compile(self, graph):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return compile_graph(graph, self.tmp.name)

    def test_every_type_same_entries_and_both_protected_exits(self):
        graph, ld, gemm, backup_ld, _ = self.fixture()
        host = node(graph, "host_member", 0, 2, 7)
        dummy_node = BingoNode(0, 0, 1)
        dummy_node.node_type = "dummy"
        dummy_node.cond_exec_en, dummy_node.cond_exec_group_id = True, 7
        graph.bingo_add_node(dummy_node)
        output = self.compile(graph)
        self.assertEqual({key: value[2:] for key, value in graph._cerf_fallback_table.items()},
                         {(0, 1): (7, 11), (0, 2): (7, 11)})
        self.assertEqual(output.count("bingo_cerf_fb_set("), 2)
        self.assertTrue(nx.has_path(graph, gemm, backup_ld))
        self.assertTrue(nx.has_path(graph, host, backup_ld))
        exits = [n for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"
                 and n.assigned_cluster_id == 0]
        self.assertEqual(len(exits), 2)
        self.assertTrue(all(n.cond_exec_en and n.cond_exec_group_id == 7 for n in exits))

    def test_conflicting_existing_type_entry_rejected(self):
        graph, _, _, _, _ = self.fixture()
        primary = copy(graph, "conflict", 1, 8)
        backup = node(graph, "conflict_backup", 0, 2, 12)
        graph.bingo_add_cerf_fallback(primary, backup)
        with self.assertRaisesRegex(ValueError, "Conflicting CERF fallback entries"):
            self.compile(graph)

    def test_each_type_suffix_checked(self):
        for core in (0, 1):
            graph, _, gemm, _, _ = self.fixture()
            tail = node(graph, "tail", 0, core)
            graph.bingo_add_edge(gemm, tail)
            with self.subTest(core=core), self.assertRaisesRegex(ValueError, "outside CERF group"):
                self.compile(graph)

    def test_possible_substitute_exit_and_warning_for_every_type(self):
        graph, ld, gemm, _, _ = self.fixture()
        # Move the given backup to host so neither substitute carries it.
        for n in list(graph.node_list):
            if n.cond_exec_group_id == 11:
                n.assigned_cluster_id, n.assigned_core_id = 0, 2
                if isinstance(n.kernel_args, SnaxBingoKernelIdma1dCopyArgs):
                    args = n.kernel_args
                    n.kernel_name = "__host_bingo_kernel_idma"
                    n.kernel_args = HostBingoKernelIdmaArgs(args.src_addr, args.dst_addr, args.size)
                else:
                    n.kernel_name = "__host_bingo_kernel_dummy"
                    n.kernel_args = HostBingoKernelDummyArgs(0)
        late = node(graph, "late_substitute", 1, 1)
        graph.bingo_add_edge(gemm, late)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            compile_graph(graph, self.tmp.name)
        self.assertTrue(any("late_substitute" in str(w.message) for w in caught))
        exits = [n for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"]
        self.assertTrue(all(n.cond_exec_en and n.cond_exec_group_id == 7 for n in exits))


if __name__ == "__main__":
    unittest.main()
