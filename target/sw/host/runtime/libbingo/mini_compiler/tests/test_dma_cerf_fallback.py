"""Generate the DMA-core double-death workload (host fallback) without an RTL build."""

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


REPO_ROOT = Path(__file__).resolve().parents[7]
WORKLOAD = REPO_ROOT / (
    "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_cerf_2cluster")


class DmaCerfFallbackTests(unittest.TestCase):
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
        original_compile = BingoDFG.bingo_compile_dfg

        def compile_workload(graph, *args, **kwargs):
            self.graph = graph
            self.original_edges = {(source.node_name, target.node_name)
                                   for source, target in graph.edges()}
            return original_compile(graph, *args, **kwargs)

        argv = [str(WORKLOAD / "main_bingo.py"), "--output_dir", str(root),
                "--data_h", str(root / "dma_cerf_data.h"),
                "-c", str(WORKLOAD / "params.hjson"),
                "--hwcfg", "/dev/null", "--platformcfg", str(platform)]
        # sys.modules too: a cached _usg_paths would skip the path setup of later workload tests
        with patch.object(sys, "argv", argv), \
                patch.object(sys, "path", [str(REPO_ROOT / "util/sim/common"), *sys.path]), \
                patch.dict(sys.modules), \
                patch.object(BingoDFG, "bingo_compile_dfg", compile_workload), \
                patch.object(BingoDFG, "bingo_visualize_dfg"), \
                patch.object(BingoDFG, "bingo_export_dfg_to_csv"), \
                contextlib.redirect_stdout(io.StringIO()), \
                warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runpy.run_path(str(WORKLOAD / "main_bingo.py"), run_name="__main__")
        self.warnings = [str(item.message) for item in caught]
        self.output = (root / "offload_bingo_hw.h").read_text()
        self.data = (root / "dma_cerf_data.h").read_text()
        self.nodes = {node.node_name: node for node in self.graph.node_list}

    def test_driver_task_ids_and_branches(self):
        expected = ["gating", "copy_0", "copy_1", "host_copy", "join"]
        for task_id, name in enumerate(expected):
            self.assertEqual(self.nodes[name].node_id, task_id)
        for name in ("copy_0", "copy_1"):
            node = self.nodes[name]
            self.assertEqual((node.assigned_cluster_id, node.assigned_core_id), (0, 1))
            self.assertEqual((node.cond_exec_en, node.cond_exec_group_id), (True, 0))
        host_copy = self.nodes["host_copy"]
        self.assertEqual((host_copy.assigned_cluster_id, host_copy.assigned_core_id), (0, 2))
        self.assertEqual((host_copy.cond_exec_en, host_copy.cond_exec_group_id), (True, 1))
        self.assertEqual(host_copy.kernel_args.size, 512)

    def test_table_ordering_and_both_dm_exits_in_the_primary_group(self):
        graph = self.graph
        self.assertEqual(graph._cerf_fallbacks, [(self.nodes["copy_0"], self.nodes["host_copy"])])
        self.assertNotIn(("copy_1", "host_copy"), self.original_edges)
        # The host copy only depends on the gating: this path is the ordering edge
        self.assertTrue(nx.has_path(graph, self.nodes["copy_1"], self.nodes["host_copy"]))
        self.assertTrue(nx.has_path(graph, self.nodes["copy_0"], self.nodes["host_copy"]))
        self.assertEqual(self.output.count("bingo_cerf_fb_set("), 1)
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 1), 0, 1);", self.output)
        self.assertIn("bingo_cerf_fb_enable((1u << BINGO_CORE_TYPE_ID(0, 1)));", self.output)
        exits = {(n.assigned_cluster_id, n.assigned_core_id):
                 n.cond_exec_group_id if n.cond_exec_en else None
                 for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"}
        self.assertEqual(exits, {(0, 0): None, (0, 1): 0, (1, 0): None, (1, 1): 0})
        # The s28/s29 checker expects the two DM exits as tasks 7 and 9
        exit_ids = {(n.assigned_cluster_id, n.assigned_core_id): n.node_id
                    for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"}
        self.assertEqual((exit_ids[(0, 1)], exit_ids[(1, 1)]), (7, 9))
        # The host backup is type 0, and the substitute runs only its exit
        self.assertEqual(self.warnings, [])

    def test_host_checks_output_selected_by_event(self):
        self.assertIn("uint32_t __evt = bingo_cerf_fb_evt();", self.output)
        self.assertIn("BINGO_CORE_TYPE_ID(0, 1);", self.output)
        self.assertIn("BINGO_DMA_EXPECT_BRANCH", self.output)
        self.assertIn("__output[__branch][__i] != __golden[__i]", self.output)
        self.assertIn("A_l3", self.data)


if __name__ == "__main__":
    unittest.main()
