"""Int32 host mapping, per-kernel validation and the actual same-output graph."""
import runpy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bingo_kernel_args import HostBingoKernelAraAddI32Args, SnaxBingoKernelInt32AddArgs
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_node import BingoNode
import test_host_fallback as host_tests
from test_host_fallback import fixture, REPO_ROOT

WORKLOAD = REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/add_host_fallback_2cluster"


def add_fixture():
    graph, node, _, check = fixture(False)
    node.kernel_name = "__snax_bingo_kernel_int32_add"
    node.kernel_args = SnaxBingoKernelInt32AddArgs(
        BingoMemSymbol("a"), BingoMemSymbol("b"), BingoMemAlloc("c", 64, "L3"), 16)
    return graph, node, check


class AddHostFallbackTests(unittest.TestCase):
    def test_mapping_preserves_all_handles_and_host_width(self):
        graph, node, _ = add_fixture()
        backup = graph.bingo_add_host_fallback(node)
        self.assertEqual(backup.kernel_name, "__host_bingo_kernel_add_i32")
        self.assertIs(type(backup.kernel_args), HostBingoKernelAraAddI32Args)
        for source, destination in (("a_addr", "input_a_addr"), ("b_addr", "input_b_addr"),
                                    ("c_addr", "output_addr")):
            self.assertIs(getattr(node.kernel_args, source), getattr(backup.kernel_args, destination))
        self.assertEqual(backup.kernel_args.num_elements, 16)
        fields = backup.kernel_args.get_c_field_assignments({node.kernel_args.c_addr: "ptr_c"})
        self.assertEqual(fields["output_addr"], "(uint64_t)ptr_c")
        self.assertIn("uint64_t", fields["input_a_addr"])
        self.assertIs(graph.bingo_add_host_fallback(node), backup)

    def test_add_count_and_bare_addresses_are_rejected(self):
        for count in (0, -1, "16", 1.5, True):
            graph, node, _ = add_fixture()
            node.kernel_args.num_elements = count
            with self.assertRaisesRegex(ValueError, "num_elements"):
                graph.bingo_add_host_fallback(node)
        for field in ("a_addr", "b_addr", "c_addr"):
            graph, node, _ = add_fixture()
            setattr(node.kernel_args, field, 1234)
            with self.assertRaisesRegex(ValueError, "memory handles"):
                graph.bingo_add_host_fallback(node)

    def test_each_overlapping_input_has_specific_error_and_adjacency_passes(self):
        shared = BingoMemAlloc("shared", 256)
        for field in ("a_addr", "b_addr"):
            for offset in (0, 63):
                graph, node, _ = add_fixture()
                setattr(node.kernel_args, field, shared)
                node.kernel_args.c_addr = shared.view(offset)
                with self.assertRaisesRegex(ValueError, "int32 add output overlaps an input"):
                    graph.bingo_add_host_fallback(node)
            graph, node, _ = add_fixture()
            setattr(node.kernel_args, field, shared)
            node.kernel_args.c_addr = shared.view(64)
            graph.bingo_add_host_fallback(node)

    def test_workload_graph_and_signed_golden(self):
        helper = host_tests.HostFallbackWorkloadTests()
        self.addCleanup(helper.doCleanups)
        with patch("test_host_fallback.WORKLOAD", WORKLOAD):
            helper.setUp()
        graph = helper.graph
        primary, backup = next(iter(graph._host_fallbacks.items()))
        check = next(n for n in graph.node_list if n.node_name == "check")
        self.assertEqual((primary.node_id, check.node_id, backup.node_id), (0, 1, 2))
        self.assertIs(primary.kernel_args.c_addr, backup.kernel_args.output_addr)
        self.assertIs(primary.kernel_args.c_addr, check.kernel_args.output_data_addr)
        self.assertEqual(primary.kernel_args.c_addr.mem_level, "L3")
        self.assertTrue(nx.has_path(graph, backup, check))
        self.assertEqual((primary.cond_exec_group_id, backup.cond_exec_group_id), (0, 1))
        exits = [n for n in graph.node_list if n.kernel_name == "__snax_bingo_kernel_exit"
                 and n.assigned_core_id == 1]
        self.assertEqual(len(exits), 2)
        self.assertTrue(all(n.cond_exec_en and n.cond_exec_group_id == 0 for n in exits))
        module = runpy.run_path(str(WORKLOAD / "main_bingo.py"))
        a, b, golden = module["vectors"](512)
        self.assertEqual(golden, [x + y for x, y in zip(a, b)])
        self.assertTrue(all(-(1 << 31) <= v < (1 << 31) for v in a + b + golden))
        self.assertTrue(any(v < 0 for v in golden) and any(v > 0 for v in golden))


if __name__ == "__main__":
    unittest.main()
