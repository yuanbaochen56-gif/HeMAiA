"""Compiler-only CERF fallback checks, without an RTL build or simulator."""

import contextlib
import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bingo_dfg import BingoDFG
from bingo_kernel_args import HostBingoKernelDummyArgs, SnaxBingoKernelDummyArgs
from bingo_node import BingoNode
from bingo_platform import parse_bingo_core_type_ids, parse_platform_cfg


TYPES = {(0, 0): 1, (0, 1): 2, (0, 2): 0,
         (1, 0): 3, (1, 1): 2, (1, 2): 0}


def node(graph, name, cluster, core, group=None, chip=0):
    host = core == graph.num_cores_per_cluster - 1
    kernel = "__host_bingo_kernel_dummy" if host else "__snax_bingo_kernel_dummy"
    args = HostBingoKernelDummyArgs(0) if host else SnaxBingoKernelDummyArgs(0)
    result = BingoNode(chip, cluster, core, node_name=name, kernel_name=kernel,
                       kernel_args=args)
    if group is not None:
        result.cond_exec_en = True
        result.cond_exec_group_id = group
    graph.bingo_add_node(result)
    return result


def fixture(automatic=False, types=None, mode="static"):
    graph = BingoDFG(1, 2, 2, True, [0], core_type_ids=TYPES if types is None else types)
    router = node(graph, "router", 0, 2)
    primary = node(graph, "primary", 0, 0, None if automatic else 7)
    backup = node(graph, "backup", 1, 0, None if automatic else 11)
    join = node(graph, "join", 0, 2)
    policy = {"mode": "top_k", "k": 1} if mode == "top_k" else {"mode": "static", "write_mask": 1}
    for target in (primary, backup):
        graph.bingo_add_edge(router, target, cond=automatic,
                             cond_dic=policy if automatic else None)
        graph.bingo_add_edge(target, join)
    graph.bingo_add_cerf_fallback(primary, backup)
    return graph, primary, backup, join


def compile_graph(graph, output_dir):
    with contextlib.redirect_stdout(io.StringIO()), \
            patch.object(graph, "bingo_visualize_dfg"), \
            patch.object(graph, "bingo_export_dfg_to_csv"):
        graph.bingo_compile_dfg("fallback", str(output_dir), "offload.h", [])
    return (Path(output_dir) / "offload.h").read_text()


class CerfFallbackCompilerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def compile(self, graph):
        return compile_graph(graph, self.directory.name)

    def test_manual_groups_table_and_exit(self):
        graph, primary, backup, _ = fixture()
        output = self.compile(graph)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 7, 11);", output)
        self.assertIn("bingo_cerf_fb_enable((1u << BINGO_CORE_TYPE_ID(0, 0)));", output)
        self.assertIn('_Static_assert(BINGO_CORE_TYPE_ID(0, 0) == 1', output)
        self.assertLess(output.index("bingo_cerf_fb_set("), output.index("bingo_cerf_fb_enable("))
        self.assertLess(output.index("bingo_cerf_fb_enable("), output.index("bingo_hw_scheduler_init("))
        self.assertIn("#error", output)  # Legacy init macros must not overwrite the table.
        exits = [n for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"]
        protected = [n for n in exits if (n.assigned_cluster_id, n.assigned_core_id) == (0, 0)]
        self.assertEqual(len(protected), 1)
        self.assertTrue(protected[0].cond_exec_en)
        self.assertEqual(protected[0].cond_exec_group_id, 7)
        self.assertFalse(protected[0].cond_exec_invert)
        self.assertTrue(all(not n.cond_exec_en for n in exits if n not in protected))

    def test_automatic_groups_and_backup_software_guard(self):
        graph, primary, backup, _ = fixture(automatic=True, mode="top_k")
        output = self.compile(graph)
        self.assertEqual((primary.cond_exec_group_id, backup.cond_exec_group_id), (0, 1))
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 0, 1);", output)
        self.assertIsNotNone(primary._gating_node)
        self.assertIsNone(backup._gating_node)
        self.assertIn(f"args_dev_chip00_{backup.node_id}->gating_sp_addr = 0;", output)
        self.assertNotIn(f"args_dev_chip00_{primary.node_id}->gating_sp_addr = 0;", output)

    def test_automatic_multinode_groups_disable_every_backup_guard(self):
        graph, primary, backup, join = fixture(automatic=True, mode="top_k")
        router = graph.node_list[0]
        primary_tail = node(graph, "primary_tail", 0, 1)
        backup_tail = node(graph, "backup_tail", 1, 1)
        for head, tail in ((primary, primary_tail), (backup, backup_tail)):
            graph.bingo_add_edge(head, tail)
            graph.bingo_add_edge(router, tail, cond_dic={"mode": "top_k", "k": 1})
            graph.bingo_add_edge(tail, join)
        output = self.compile(graph)
        self.assertEqual(primary_tail.cond_exec_group_id, primary.cond_exec_group_id)
        self.assertEqual(backup_tail.cond_exec_group_id, backup.cond_exec_group_id)
        for n in (backup, backup_tail):
            self.assertIsNone(n._gating_node)
            self.assertIn(f"args_dev_chip00_{n.node_id}->gating_sp_addr = 0;", output)
        self.assertIsNotNone(primary_tail._gating_node)

    def test_all_primary_sinks_order_all_backup_sources(self):
        graph, primary, backup, _ = fixture()
        sink2 = node(graph, "sink2", 0, 1, 7)
        source2 = node(graph, "source2", 1, 1, 11)
        graph.bingo_add_edge(primary, sink2)
        # primary is not itself a sink; a second independent primary is.
        sink3 = node(graph, "sink3", 0, 1, 7)
        graph.bingo_transform_dfg_add_exit_nodes()
        graph._compile_cerf_fallbacks()
        for source in (backup, source2):
            self.assertTrue(graph.has_edge(sink2, source))
            self.assertTrue(graph.has_edge(sink3, source))
            self.assertFalse(graph.has_edge(primary, source))

    def test_order_edges_survive_dummy_and_tag_passes(self):
        graph, primary, backup, join = fixture()
        self.compile(graph)
        import networkx as nx
        self.assertTrue(nx.has_path(graph, primary, backup))
        self.assertTrue(nx.has_path(graph, backup, join))
        for n in (primary, backup):
            unpacked = graph.bingo_unpack_node(graph.bingo_pack_node(n))
            self.assertTrue(unpacked["cond_exec_en"])
            self.assertEqual(unpacked["cond_exec_group_id"], n.cond_exec_group_id)

    def test_missing_core_type_map_is_an_error(self):
        graph, _, _, _ = fixture(types={})
        with self.assertRaisesRegex(ValueError, "Missing core_type_ids"):
            self.compile(graph)

    def test_same_type_backup_warns(self):
        types = dict(TYPES)
        types[(1, 0)] = 1
        graph, _, _, _ = fixture(types=types)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.compile(graph)
        self.assertEqual(len(caught), 1)
        self.assertIn("same-type backup", str(caught[0].message))
        self.assertIn("substitute", str(caught[0].message))

    def test_protected_slot_cannot_run_the_backup(self):
        graph, _, backup, _ = fixture()
        extra = node(graph, "same_type", 0, 0, 11)
        graph.bingo_add_edge(backup, extra)
        # It is on the protected slot too, so reject mandatory suffix work.
        with self.assertRaisesRegex(ValueError, "outside CERF group"):
            self.compile(graph)

    def test_same_type_on_a_nonrepresentative_backup_slot_warns(self):
        graph, _, backup, _ = fixture()
        graph.core_type_ids[(1, 1)] = 1
        extra = node(graph, "same_type", 1, 1, 11)
        graph.bingo_add_edge(backup, extra)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            self.compile(graph)
        self.assertEqual(len(caught), 1)
        self.assertIn("same-type backup", str(caught[0].message))

    def test_conflicting_type_entries_are_rejected(self):
        graph, _, _, _ = fixture()
        graph.core_type_ids[(1, 1)] = 1
        primary2 = node(graph, "primary2", 1, 1, 8)
        backup2 = node(graph, "backup2", 0, 1, 12)
        graph.bingo_add_cerf_fallback(primary2, backup2)
        with self.assertRaisesRegex(ValueError, "Conflicting CERF fallback entries"):
            self.compile(graph)

    def test_identical_type_entries_are_deduplicated_and_both_exits_guarded(self):
        graph, _, _, _ = fixture()
        graph.core_type_ids[(0, 1)] = 1
        primary2 = node(graph, "primary2", 0, 1, 7)
        backup2 = node(graph, "backup2", 1, 1, 11)
        graph.bingo_add_cerf_fallback(primary2, backup2)
        output = self.compile(graph)
        self.assertEqual(output.count("bingo_cerf_fb_set("), 1)
        self.assertEqual(output.count("bingo_cerf_fb_enable("), 1)
        exits = [n for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"
                 and n.assigned_cluster_id == 0]
        self.assertTrue(all(n.cond_exec_en and n.cond_exec_group_id == 7 for n in exits))

    def test_two_types_emit_one_enable_after_all_entries(self):
        graph, _, _, _ = fixture()
        primary2 = node(graph, "primary2", 0, 1, 8)
        backup2 = node(graph, "backup2", 1, 1, 12)
        graph.bingo_add_cerf_fallback(primary2, backup2)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            output = self.compile(graph)
        self.assertEqual(output.count("bingo_cerf_fb_set("), 2)
        self.assertEqual(output.count("bingo_cerf_fb_enable("), 1)
        self.assertLess(output.rindex("bingo_cerf_fb_set("), output.index("bingo_cerf_fb_enable("))
        self.assertIn("(1u << BINGO_CORE_TYPE_ID(0, 0)) | (1u << BINGO_CORE_TYPE_ID(0, 1))", output)

    def test_protected_slot_mandatory_suffix_is_rejected(self):
        graph, primary, _, _ = fixture()
        tail = node(graph, "mandatory_tail", 0, 0)
        graph.bingo_add_edge(primary, tail)
        with self.assertRaisesRegex(ValueError, "mandatory_tail.*outside CERF group 7"):
            self.compile(graph)

    def test_later_other_group_on_protected_slot_is_rejected(self):
        graph, primary, _, _ = fixture()
        tail = node(graph, "other_group_tail", 0, 0, 12)
        graph.bingo_add_edge(primary, tail)
        with self.assertRaisesRegex(ValueError, "outside CERF group 7"):
            self.compile(graph)

    def test_earlier_unconditional_work_on_protected_slot_is_allowed(self):
        graph, primary, _, _ = fixture()
        prefix = node(graph, "prefix", 0, 0)
        graph.bingo_add_edge(prefix, primary)
        self.compile(graph)

    def test_later_primary_group_work_is_allowed(self):
        graph, primary, _, _ = fixture()
        tail = node(graph, "primary_tail", 0, 0, 7)
        graph.bingo_add_edge(primary, tail)
        self.compile(graph)

    def test_cycle_is_rejected_without_inserting_edges(self):
        graph, primary, backup, _ = fixture()
        graph.bingo_add_edge(backup, primary)
        with self.assertRaisesRegex(ValueError, "ordering would create a cycle"):
            graph._compile_cerf_fallbacks()
        self.assertFalse(graph.has_edge(primary, backup))

    def test_invalid_group_assignments_are_rejected(self):
        for mutation in ("disabled", "inverted", "same", "overflow"):
            with self.subTest(mutation=mutation):
                graph, primary, backup, _ = fixture()
                if mutation == "disabled":
                    primary.cond_exec_en = False
                elif mutation == "inverted":
                    backup.cond_exec_invert = True
                elif mutation == "same":
                    backup.cond_exec_group_id = primary.cond_exec_group_id
                else:
                    primary.cond_exec_group_id = 32
                with self.assertRaisesRegex(ValueError, "CERF"):
                    graph._compile_cerf_fallbacks()

    def test_inverted_sibling_is_rejected(self):
        graph, _, _, _ = fixture()
        sibling = node(graph, "inverted_sibling", 1, 1, 11)
        sibling.cond_exec_invert = True
        with self.assertRaisesRegex(ValueError, "inverted tasks"):
            self.compile(graph)

    def test_shared_expert_groups_are_rejected(self):
        graph, _, backup, _ = fixture()
        sibling = node(graph, "shared_expert", 1, 1, 11)
        gate = node(graph, "gate", 0, 2)
        backup._gating_node = sibling._gating_node = gate
        backup._cond_node_index, sibling._cond_node_index = 0, 1
        with self.assertRaisesRegex(ValueError, "shared expert groups"):
            graph._compile_cerf_fallbacks()

    def test_manual_groups_are_reserved_before_router_assignment(self):
        graph, primary, backup, _ = fixture(automatic=True)
        # Leave the backup auto-allocated, but make the primary hand-assigned.
        for _, _, data in graph.in_edges(primary, data=True):
            data["cond"] = False
        primary.cond_exec_en, primary.cond_exec_group_id = True, 7
        output = self.compile(graph)
        self.assertEqual(backup.cond_exec_group_id, 8)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 7, 8);", output)

    def test_registration_rejects_foreign_nodes_and_cross_chip(self):
        graph, primary, backup, _ = fixture()
        with self.assertRaisesRegex(ValueError, "belong"):
            graph.bingo_add_cerf_fallback(primary, BingoNode(0, 0, 0))
        with self.assertRaisesRegex(ValueError, "distinct"):
            graph.bingo_add_cerf_fallback(primary, primary)
        backup.assigned_chiplet_id = 1
        with self.assertRaisesRegex(ValueError, "same chiplet"):
            graph.bingo_add_cerf_fallback(primary, backup)

    def test_duplicate_registration_is_idempotent(self):
        graph, primary, backup, _ = fixture()
        graph.bingo_add_cerf_fallback(primary, backup)
        self.assertEqual(len(graph._cerf_fallbacks), 1)
        self.assertEqual(self.compile(graph).count("bingo_cerf_fb_set("), 1)

    def test_cross_chip_assignment_after_registration_is_rejected(self):
        graph, _, backup, _ = fixture()
        backup.assigned_chiplet_id = 1
        with self.assertRaisesRegex(ValueError, "same chiplet"):
            graph._compile_cerf_fallbacks()

    def test_tables_are_scoped_per_chip(self):
        graph = BingoDFG(2, 2, 2, True, [0, 1], core_type_ids=TYPES)
        p0, b0 = node(graph, "p0", 0, 0, 7), node(graph, "b0", 1, 0, 11)
        p1, b1 = node(graph, "p1", 0, 0, 8, chip=1), node(graph, "b1", 1, 0, 12, chip=1)
        graph.bingo_add_cerf_fallback(p0, b0)
        graph.bingo_add_cerf_fallback(p1, b1)
        output = self.compile(graph)
        self.assertEqual(output.count("bingo_cerf_fb_enable("), 2)
        chip0 = output[output.index("if (current_chip_id == 0x00)"):output.index("if (current_chip_id == 0x01)")]
        chip1 = output[output.index("if (current_chip_id == 0x01)"):]
        self.assertIn("BINGO_CORE_TYPE_ID(0, 0), 7, 11", chip0)
        self.assertNotIn("BINGO_CORE_TYPE_ID(0, 0), 8, 12", chip0)
        self.assertIn("BINGO_CORE_TYPE_ID(0, 0), 8, 12", chip1)
        self.assertNotIn("BINGO_CORE_TYPE_ID(0, 0), 7, 11", chip1)

    def test_core_type_mask_width(self):
        for core_type in (0, 31):
            with self.subTest(core_type=core_type):
                types = dict(TYPES)
                types[(0, 0)] = core_type
                graph, _, _, _ = fixture(types=types)
                self.assertIn(f"== {core_type}", self.compile(graph))
        for core_type in (-1, 32):
            types = dict(TYPES)
            types[(0, 0)] = core_type
            graph, _, _, _ = fixture(types=types)
            with self.assertRaisesRegex(ValueError, "32-bit mask"):
                self.compile(graph)

    def test_host_backup_uses_type_zero_slot(self):
        graph, primary, _, join = fixture()
        graph._cerf_fallbacks.clear()
        host_backup = node(graph, "host_backup", 0, 2, 12)
        graph.bingo_add_edge(host_backup, join)
        graph.bingo_add_cerf_fallback(primary, host_backup)
        output = self.compile(graph)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 7, 12);", output)

    @unittest.skipUnless(shutil.which("cc"), "a host C compiler is required")
    def test_emitted_c_programs_table_before_start_and_rejects_stale_types(self):
        graph, _, _, _ = fixture()
        self.compile(graph)
        emitted = io.StringIO()
        graph._emit_scheduler_launch(emitted, 0)
        source = """
#include <assert.h>
#include <stdint.h>
#define OFFLOAD_BINGO_HW_DEBUG_PRINT_SAFE(...) ((void)0)
#define BINGO_CORE_TYPE_ID(cluster, core) CORE_TYPE
static unsigned step;
static uint32_t list[1];
#define device_arg_list_chip_00 list
#define device_kernel_list_chip_00 list
#define global_task_id_to_dev_task_id_chip_00 list
#define bingo_hw_scheduler_task_desc_list_chip_00 list
#define host_arg_list_chip_00 list
#define host_kernel_list_chip_00 list
#define global_task_id_to_host_task_id_chip_00 list
#define num_dev_tasks_chip_00 0
#define num_total_tasks 0
#define bingo_hw_scheduler_num_task_desc_chip_00 0
void bingo_cerf_fb_set(uint32_t type, uint32_t clear, uint32_t set) {
    assert(step++ == 0 && type == 1 && clear == 7 && set == 11);
}
void bingo_cerf_fb_enable(uint32_t mask) {
    assert(step++ == 1 && mask == 2);
}
void bingo_hw_scheduler_init(uint64_t a, uint64_t b, uint32_t c, uint64_t d,
                             uint32_t e, uint64_t f, uint32_t g) {
    assert(step++ == 2);
}
uint32_t bingo_hw_scheduler(uint32_t *a, uint32_t *b, uint32_t *c) {
    assert(step++ == 3);
    return 0;
}
int launch(void) {
""" + emitted.getvalue() + """
    return 0;
}
int main(void) {
    assert(launch() == 0 && step == 4);
    return 0;
}
"""
        path = Path(self.directory.name) / "launch.c"
        path.write_text(source)
        binary = path.with_suffix("")
        result = subprocess.run(["cc", "-std=c11", "-Wall", "-Werror", "-DCORE_TYPE=1",
                                 str(path), "-o", str(binary)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        subprocess.run([str(binary)], check=True, timeout=5)
        for flags in (["-DCORE_TYPE=3"], ["-DCORE_TYPE=1", "-DBINGO_CERF_FB_CLUSTER=0"]):
            with self.subTest(flags=flags):
                result = subprocess.run(["cc", "-std=c11", "-fsyntax-only", *flags, str(path)],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)

    def test_no_registration_leaves_output_unchanged(self):
        graph, primary, backup, _ = fixture()
        graph._cerf_fallbacks.clear()
        output = self.compile(graph)
        self.assertNotIn("bingo_cerf_fb_", output)
        self.assertIsNone(primary._gating_node)
        self.assertEqual((primary.cond_exec_group_id, backup.cond_exec_group_id), (7, 11))
        exits = [n for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"]
        self.assertTrue(all(not n.cond_exec_en for n in exits))

    def test_unregistered_auto_cross_group_edges_still_rejected(self):
        graph, primary, backup, _ = fixture(automatic=True)
        graph._cerf_fallbacks.clear()
        graph.bingo_add_edge(primary, backup)
        # This joins the two conditional components, so the unguarded join
        # remains the forbidden boundary, not a registered fallback exception.
        with self.assertRaisesRegex(ValueError, "CERF boundary"):
            self.compile(graph)


class CoreTypeParserTests(unittest.TestCase):
    def test_generated_macro_and_existing_platform_parser(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "occamy.h"
            path.write_text(
                "#define N_CHIPLETS 1\n#define CHIPLET_ID_0 0x00\n"
                "#define N_CLUSTERS_PER_CHIPLET 2\n#define N_CORES_PER_CLUSTER 2\n"
                "#define BINGO_CORE_TYPE_ID(cluster, core) "
                "(((cluster) == 0 && (core) == 0) ? 1 : "
                "((cluster) == 1 && (core) == 0) ? 3 : 0)\n")
            self.assertEqual(parse_bingo_core_type_ids(path),
                             {(0, 0): 1, (0, 1): 0, (0, 2): 0,
                              (1, 0): 3, (1, 1): 0, (1, 2): 0})
            self.assertEqual(parse_platform_cfg(path),
                             {"num_chiplets": 1, "chiplet_ids": [0],
                              "num_clusters_per_chiplet": 2, "num_cores_per_cluster": 2})

    def test_missing_or_malformed_macro_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "occamy.h"
            for text in ("", "#define BINGO_CORE_TYPE_ID(cluster, core) 0\n"):
                path.write_text(text)
                with self.assertRaisesRegex(ValueError, "BINGO_CORE_TYPE_ID"):
                    parse_bingo_core_type_ids(path)


if __name__ == "__main__":
    unittest.main()
