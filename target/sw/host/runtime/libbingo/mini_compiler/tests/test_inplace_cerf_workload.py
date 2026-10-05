"""Generation and graph safety of the A4 in-place-add demonstration."""
import contextlib
import importlib.util
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

ROOT = Path(__file__).resolve().parents[7]
WORKLOAD = ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/int32_inplace_cerf_2cluster"
IDS = {"copy": 1, "add": 2, "backup": 3, "check": 4,
       "primary_exit": 7, "substitute_exit": 9}


def generate(directory, unsafe=False, branch_aware=False):
    platform = directory / "occamy.h"
    platform.write_text(
        "#define N_CHIPLETS 1\n#define CHIPLET_ID_0 0x00\n"
        "#define N_CLUSTERS_PER_CHIPLET 2\n#define N_CORES_PER_CLUSTER 2\n"
        "#define BINGO_CORE_TYPE_ID(cluster, core) "
        "(((cluster) == 0 && (core) == 0) ? 1 : "
        "((cluster) == 0 && (core) == 1) ? 2 : "
        "((cluster) == 1 && (core) == 0) ? 1 : "
        "((cluster) == 1 && (core) == 1) ? 2 : 0)\n")
    captured = []
    original = BingoDFG.bingo_compile_dfg

    def compile(graph, *args, **kwargs):
        captured.append(graph)
        return original(graph, *args, **kwargs)

    argv = [str(WORKLOAD / "main_bingo.py"), "--output_dir", str(directory),
            "--data_h", str(directory / "int32_inplace_data.h"),
            "-c", str(WORKLOAD / "params.hjson"), "--hwcfg", "/dev/null",
            "--platformcfg", str(platform)]
    if unsafe:
        argv.append("--allow-unsafe-replay")
    if branch_aware:
        argv.append("--branch-aware-check")
    with patch.object(sys, "argv", argv), patch.dict(sys.modules), \
         patch.object(BingoDFG, "bingo_compile_dfg", compile), \
         patch.object(BingoDFG, "bingo_visualize_dfg"), \
         warnings.catch_warnings(record=True) as emitted, \
         contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("always")
        runpy.run_path(str(WORKLOAD / "main_bingo.py"), run_name="__main__")
    return captured[0], (directory / "offload_bingo_hw.h").read_text(), emitted


class InplaceCerfWorkloadTests(unittest.TestCase):
    def test_branch_aware_checks_nodes_groups_and_edges(self):
        spec = importlib.util.spec_from_file_location("inplace_branch_workload", WORKLOAD / "main_bingo.py")
        workload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(workload)
        platform = dict(chiplet_ids=[0])
        graph, nodes = workload.build_graph(
            platform, {0: 1, 1: 2, 2: 0}, 32, branch_aware_check=True)
        add, backup, check, check_bk = [nodes[k] for k in ("add", "backup", "check", "check_bk")]
        self.assertEqual(list(graph.predecessors(check)), [add])
        self.assertEqual(list(graph.predecessors(check_bk)), [backup])
        self.assertTrue(check.cond_exec_en and check_bk.cond_exec_en)
        self.assertEqual((check.cond_exec_group_id, check_bk.cond_exec_group_id), (0, 1))
        self.assertIs(check.kernel_args.output_data_addr, add.kernel_args.c_addr)
        self.assertIs(check_bk.kernel_args.output_data_addr, backup.kernel_args.output_addr)
        self.assertEqual(check_bk.kernel_args.golden_data_addr.symbol_name, "golden_once")
        self.assertEqual(check_bk.kernel_args.name, "bk")

    def test_branch_aware_compiles_and_preserves_fault_gid(self):
        driver_path = ROOT / "target/sim/automation/test/3_start_bingo_watchdog_sim.py"
        driver = runpy.run_path(str(driver_path))
        with tempfile.TemporaryDirectory() as temporary:
            graph, _, _ = generate(Path(temporary), branch_aware=True)
            ids = driver["replay_safety_task_ids"](
                Path(temporary) / "final_dfg.csv", branch_aware=True)
            self.assertEqual(ids["add"], driver["INPLACE_FAULT_GID"])
            self.assertEqual(ids["check_bk"], ids["check"] + 1)
            self.assertEqual(sum(node.node_name in ("check", "check_bk")
                                 for node in graph.node_list), 2)

    def test_protected_graph_outputs_groups_and_exits(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph, header, emitted = generate(Path(temporary))
            nodes = {node.node_name: node for node in graph.node_list}
            copy, add, backup, check = [nodes[name] for name in ("copy", "inplace_add", "backup", "check")]
            self.assertIs(copy.kernel_args.dst_addr, add.kernel_args.a_addr)
            self.assertIs(add.kernel_args.a_addr, add.kernel_args.c_addr)
            self.assertIs(check.kernel_args.output_data_addr, add.kernel_args.c_addr)
            self.assertIsNot(backup.kernel_args.output_addr, add.kernel_args.c_addr)
            self.assertEqual(backup.kernel_args.input_a_addr.symbol_name, "acc_init")
            self.assertEqual(backup.kernel_args.input_b_addr.symbol_name, "delta")
            self.assertEqual((add.cond_exec_group_id, backup.cond_exec_group_id), (0, 1))
            for before, after in ((copy, add), (copy, backup), (add, backup),
                                  (add, check), (backup, check)):
                self.assertTrue(nx.has_path(graph, before, after))
            self.assertFalse(copy.cond_exec_en)
            marked = [node for node in graph.node_list if
                      graph.bingo_unpack_node(graph.bingo_pack_node(node))["task_type"] == 3]
            self.assertEqual(marked, [add])
            self.assertTrue(any("backup must not read" in str(w.message) for w in emitted))
            self.assertIn("bingo_replay_blocked()", header)
            exits = [node for node in graph.node_list if node.kernel_name == "__snax_bingo_kernel_exit"
                     and node.assigned_core_id == 1]
            self.assertEqual(len(exits), 2)
            self.assertTrue(all(node.cond_exec_en and node.cond_exec_group_id == 0 for node in exits))

    def test_unsafe_control_only_disables_the_add_protection(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph, header, emitted = generate(Path(temporary), unsafe=True)
            add = next(node for node in graph.node_list if node.node_name == "inplace_add")
            self.assertTrue(graph.allow_unsafe_replay)
            self.assertIs(add.non_idempotent, False)
            self.assertEqual(graph.bingo_unpack_node(graph.bingo_pack_node(add))["task_type"], 0)
            self.assertIn("TEST ONLY: allow_unsafe_replay=True", header)
            self.assertTrue(any("explicitly permits unsafe replay" in str(w.message) for w in emitted))
            self.assertIn("__branch, 1, __blocked", header)

    def test_csv_ids_are_pinned_against_the_generated_graph(self):
        driver_path = ROOT / "target/sim/automation/test/3_start_bingo_watchdog_sim.py"
        spec = importlib.util.spec_from_file_location("inplace_workload_driver", driver_path)
        driver = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(driver)
        for unsafe in (False, True):
            with self.subTest(unsafe=unsafe), tempfile.TemporaryDirectory() as temporary:
                _, _, _ = generate(Path(temporary), unsafe)
                self.assertEqual(driver.replay_safety_task_ids(Path(temporary) / "final_dfg.csv"), IDS)
                self.assertEqual(driver.INPLACE_FAULT_GID, IDS["add"])
