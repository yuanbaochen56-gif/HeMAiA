"""Generate the real MoE2 workload without platform dependencies or an RTL build."""

import contextlib
import io
import json
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


REPO_ROOT = Path(__file__).resolve().parents[7]
WORKLOAD = REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/moe2_2cluster"


class MoE2FallbackTests(unittest.TestCase):
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
        hwcfg = root / "hw.json"
        hwcfg.write_text(json.dumps({
            "snax_versacore_core_template": {"snax_acc_cfg": [{
                "snax_versacore_spatial_unrolling": [[[1, 1, 1], [4, 4, 8]]]
            }]}
        }))
        original_compile = BingoDFG.bingo_compile_dfg

        def compile_workload(graph, *args, **kwargs):
            self.graph = graph
            self.original_edges = {(source.node_name, target.node_name)
                                   for source, target in graph.edges()}
            return original_compile(graph, *args, **kwargs)

        argv = [str(WORKLOAD / "main_bingo.py"), "--output_dir", str(root),
                "--data_h", str(root / "moe_data.h"), "-c", str(WORKLOAD / "params.hjson"),
                "--hwcfg", str(hwcfg), "--platformcfg", str(platform)]
        with patch.object(sys, "argv", argv), \
                patch.object(sys, "path", [str(WORKLOAD), *sys.path]), \
                patch.object(BingoDFG, "bingo_compile_dfg", compile_workload), \
                patch.object(BingoDFG, "bingo_visualize_dfg"), \
                patch.object(BingoDFG, "bingo_export_dfg_to_csv"), \
                contextlib.redirect_stdout(io.StringIO()), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runpy.run_path(str(WORKLOAD / "main_bingo.py"), run_name="__main__")
        self.warnings = [str(item.message) for item in caught]
        self.output = (root / "offload_bingo_hw.h").read_text()
        self.nodes = {node.node_name: node for node in self.graph.node_list if node.node_name}

    def test_compiler_owns_table_ordering_and_protected_exit(self):
        graph = self.graph
        primary, backup = self.nodes["e0_gemm"], self.nodes["e1_gemm"]
        self.assertIs(type(graph), BingoDFG)
        self.assertEqual(graph._cerf_fallbacks, [(primary, backup)])
        for name in ("e1_ldA", "e1_ldB"):
            self.assertNotIn(("e0_stD", name), self.original_edges)
            self.assertTrue(nx.has_path(graph, self.nodes["e0_stD"], self.nodes[name]))
        self.assertEqual(self.output.count("bingo_cerf_fb_set("), 1)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 0, 1);", self.output)
        self.assertIn("bingo_cerf_fb_enable((1u << BINGO_CORE_TYPE_ID(0, 0)));", self.output)
        self.assertLess(self.output.index("bingo_cerf_fb_set("),
                        self.output.index("bingo_cerf_fb_enable("))
        self.assertLess(self.output.index("bingo_cerf_fb_enable("),
                        self.output.index("bingo_hw_scheduler_init("))
        exits = [node for node in graph.node_list
                 if node.kernel_name == "__snax_bingo_kernel_exit"]
        for node in exits:
            protected = (node.assigned_cluster_id, node.assigned_core_id) == (0, 0)
            self.assertEqual(node.cond_exec_en, protected)
            if protected:
                self.assertEqual(node.cond_exec_group_id, 0)
                self.assertFalse(node.cond_exec_invert)

    def test_task_ids_groups_and_shared_waiting_queue_contract_are_preserved(self):
        expected = ["router", "gating", "e0_ldA", "e0_ldB", "e0_gemm", "e0_stD",
                    "e1_ldA", "e1_ldB", "e1_gemm", "e1_stD", "join"]
        for task_id, name in enumerate(expected):
            self.assertEqual(self.nodes[name].node_id, task_id)
        for expert in range(2):
            for suffix in ("ldA", "ldB", "gemm", "stD"):
                node = self.nodes[f"e{expert}_{suffix}"]
                self.assertTrue(node.cond_exec_en)
                self.assertEqual(node.cond_exec_group_id, expert)
                self.assertIsNone(node._gating_node)
        protected_tasks = [node for node in self.graph.node_list
                           if (node.assigned_cluster_id, node.assigned_core_id) == (0, 0)
                           and node.kernel_name]
        self.assertTrue(all(node.cond_exec_en and node.cond_exec_group_id == 0
                            for node in protected_tasks))
        self.assertEqual(len(self.warnings), 1)
        self.assertIn("same-type backup", self.warnings[0])


if __name__ == "__main__":
    unittest.main()
