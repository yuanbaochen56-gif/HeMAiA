"""Generate the real L3-reject add workload without an RTL build."""

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
from bingo_kernel_args import BINGO_GATING_MODE_STATIC


REPO_ROOT = Path(__file__).resolve().parents[7]
WORKLOAD = REPO_ROOT / (
    "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/"
    "int32_add_2plain_cerf_1cluster")


class PlainCerfFallbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        platform = root / "occamy.h"
        platform.write_text(
            "#define N_CHIPLETS 1\n#define CHIPLET_ID_0 0x00\n"
            "#define N_CLUSTERS_PER_CHIPLET 1\n#define N_CORES_PER_CLUSTER 4\n"
            "#define BINGO_CORE_TYPE_ID(cluster, core) "
            "(((cluster) == 0 && (core) == 0) ? 1 : "
            "((cluster) == 0 && (core) == 1) ? 2 : "
            "((cluster) == 0 && (core) == 2) ? 2 : "
            "((cluster) == 0 && (core) == 3) ? 3 : 0)\n")
        original_compile = BingoDFG.bingo_compile_dfg

        def compile_workload(graph, *args, **kwargs):
            self.graph = graph
            self.original_edges = {(source.node_name, target.node_name)
                                   for source, target in graph.edges()}
            return original_compile(graph, *args, **kwargs)

        argv = [str(WORKLOAD / "main_bingo.py"), "--output_dir", str(root),
                "--data_h", str(root / "int32_add_data.h"),
                "-c", str(WORKLOAD / "params.hjson"),
                "--hwcfg", str(REPO_ROOT / "target/rtl/cfg/cluster/"
                               "snax_versacore_2plain_to_cluster.hjson"),
                "--platformcfg", str(platform)]
        # Other workload tests restore sys.path after caching _usg_paths, so
        # provide the flat data-utils path explicitly for this runpy invocation.
        with patch.object(sys, "argv", argv), \
                patch.object(sys, "path", [str(REPO_ROOT / "util/sim/common"), *sys.path]), \
                patch.object(BingoDFG, "bingo_compile_dfg", compile_workload), \
                patch.object(BingoDFG, "bingo_visualize_dfg"), \
                patch.object(BingoDFG, "bingo_export_dfg_to_csv"), \
                contextlib.redirect_stdout(io.StringIO()), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runpy.run_path(str(WORKLOAD / "main_bingo.py"), run_name="__main__")
        self.warnings = [str(item.message) for item in caught]
        self.output = (root / "offload_bingo_hw.h").read_text()
        self.data = (root / "int32_add_data.h").read_text()
        self.nodes = {node.node_name: node for node in self.graph.node_list}

    def test_compiler_owns_table_ordering_and_protected_exit(self):
        graph = self.graph
        primary, backup = self.nodes["core1_add"], self.nodes["core2_add"]
        self.assertIs(type(graph), BingoDFG)
        self.assertEqual(graph._cerf_fallbacks, [(primary, backup)])
        self.assertNotIn(("core1_add", "core2_add"), self.original_edges)
        self.assertTrue(nx.has_path(graph, primary, backup))
        self.assertEqual(self.output.count("bingo_cerf_fb_set("), 1)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 1), 0, 1);", self.output)
        self.assertIn("bingo_cerf_fb_enable((1u << BINGO_CORE_TYPE_ID(0, 1)));", self.output)
        self.assertLess(self.output.index("bingo_cerf_fb_set("),
                        self.output.index("bingo_cerf_fb_enable("))
        self.assertLess(self.output.index("bingo_cerf_fb_enable("),
                        self.output.index("bingo_hw_scheduler_init("))
        for node in graph.node_list:
            if node.kernel_name == "__snax_bingo_kernel_exit":
                protected = node.assigned_core_id == 1
                self.assertEqual(node.cond_exec_en, protected)
                if protected:
                    self.assertEqual(node.node_id, 8)
                    self.assertEqual(node.cond_exec_group_id, 0)
                    self.assertFalse(node.cond_exec_invert)
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("same-type backup", self.warnings[0])

    def test_static_gating_full_adds_and_driver_task_ids(self):
        expected = ["gating", "load_A", "load_B", "core1_add", "core2_add", "join"]
        for task_id, name in enumerate(expected):
            self.assertEqual(self.nodes[name].node_id, task_id)
        gating = self.nodes["gating"]
        self.assertEqual(gating.node_type, "gating")
        self.assertEqual(gating.cerf_write_groups, [0, 1])
        self.assertEqual(gating.kernel_args.mode, BINGO_GATING_MODE_STATIC)
        self.assertEqual(gating.kernel_args.cerf_controlled_mask, 3)
        self.assertEqual(gating.kernel_args.top_k_or_threshold, 1)
        primary, backup = self.nodes["core1_add"], self.nodes["core2_add"]
        for branch, node in enumerate((primary, backup)):
            self.assertTrue(node.cond_exec_en)
            self.assertEqual(node.cond_exec_group_id, branch)
            self.assertEqual(node.assigned_core_id, branch + 1)
            self.assertEqual(node.kernel_args.num_elements, 512)
            self.assertIsNone(node._gating_node)
        self.assertIs(primary.kernel_args.a_addr, backup.kernel_args.a_addr)
        self.assertIs(primary.kernel_args.b_addr, backup.kernel_args.b_addr)
        self.assertIsNot(primary.kernel_args.c_addr, backup.kernel_args.c_addr)
        self.assertTrue(all(node.cond_exec_en and node.cond_exec_group_id == 0
                            for node in self.graph.node_list
                            if node.assigned_core_id == 1 and node.kernel_name))

    def test_host_checks_output_selected_by_event_not_current_cerf(self):
        self.assertIn("uint32_t __evt = bingo_cerf_fb_evt();", self.output)
        self.assertIn("(__evt & (1u << __type)) ? 1 : 0", self.output)
        self.assertIn("BINGO_ADD_EXPECT_BRANCH", self.output)
        self.assertIn("__output[__branch][__i] != __golden[__i]", self.output)
        self.assertIn("if (__err) return 1;", self.output)
        self.assertIn("C_golden_l3", self.data)


if __name__ == "__main__":
    unittest.main()
