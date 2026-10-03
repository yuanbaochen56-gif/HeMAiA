"""Early-exit graph task IDs, branch/data failures and generated fault injection."""
import copy
import csv
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "watchdog_early_exit", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

IDS = dict(gemm1=3, prefix_store=4, requant=5, gemm2=8, deep_store=9,
           backup=10, join=11, primary_exit=13, prefix_exit=15)


def graph_csv(path, offset=0):
    rows = [
        (0, 0, 2, "__host_bingo_kernel_cerf_gating"),
        (1, 1, 1, "__snax_bingo_kernel_idma_1d_copy"),
        (2, 1, 1, "__snax_bingo_kernel_idma_1d_copy"),
        (3, 1, 0, "__snax_bingo_kernel_gemm_full"),
        (4, 1, 1, "__snax_bingo_kernel_idma_1d_copy"),
        (5, 0, 2, "__host_bingo_kernel_early_exit_requant"),
        (6, 0, 1, "__snax_bingo_kernel_idma_1d_copy"),
        (7, 0, 1, "__snax_bingo_kernel_idma_1d_copy"),
        (8, 0, 0, "__snax_bingo_kernel_gemm_full"),
        (9, 0, 1, "__snax_bingo_kernel_idma_1d_copy"),
        (10, 0, 2, "__host_bingo_kernel_idma"),
        (11, 0, 2, "__host_bingo_kernel_dummy"),
        (13, 0, 0, "__snax_bingo_kernel_exit"),
        (15, 1, 0, "__snax_bingo_kernel_exit"),
    ]
    with path.open("w") as stream:
        writer = csv.writer(stream)
        writer.writerow(["ID", "Chiplet", "Cluster", "Core", "Type", "Kernel"])
        writer.writerows((task+offset, "00", cluster, core, "normal", kernel)
                        for task, cluster, core, kernel in rows)


def fixture(shallow):
    sc = copy.deepcopy(watchdog.SCENARIOS["s43" if shallow else "s44"])
    sc["early_exit_task_ids"] = IDS
    dispatches = [(100, 3, 0, 1), (200, 4, 1, 1), (300, 5, 2, 0), (400, 8, 0, 0)]
    if shallow:
        dispatches += [(800, 10, 2, 0), (900, 11, 2, 0)]
    else:
        dispatches += [(500, 9, 1, 0), (600, 11, 2, 0),
                       (650, 13, 0, 0), (700, 15, 0, 1)]
    log = "".join(f"[BINGO_DISPATCH] {t} chip=0 task={task} core={core} cluster={cluster}\n"
                  for t, task, core, cluster in dispatches)
    if shallow:
        log += ("[BINGO_WD] 500 chip=0 core=0 cluster=0 dead_suspect=1 fenced=0\n"
                "[BINGO_WD] 600 chip=0 core=0 cluster=0 dead_suspect=1 fenced=1\n"
                "[BINGO_REPLAY_STUCK] 650 chip=0 core=0 cluster=0: no live core may run task 8 (logical core 0)\n"
                "[BINGO_CERF_FB] 700 type 1 clear g0 set g1\n")
    uart = (f"[Host] Bingo status: replay_stuck={int(shallow)} cerf=0x{2 if shallow else 1:x} "
            f"cerf_fb_en=0x2 cerf_fb_evt=0x{2 if shallow else 0:x}\n"
            f"[EarlyExit] join complete; shallow={int(shallow)}\n"
            f"[Host] Check [{'shallow_out' if shallow else 'deep_out'}]: PASS\n")
    return sc, log, uart


class EarlyExitCheckerTests(unittest.TestCase):
    def test_graph_ids_are_unique_and_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final_dfg.csv"
            graph_csv(path)
            self.assertEqual(watchdog.early_exit_task_ids(path), IDS)
            text = path.read_text()
            path.write_text(text + "30,00,0,0,normal,__snax_bingo_kernel_gemm_full\n")
            with self.assertRaises(ValueError):
                watchdog.early_exit_task_ids(path)

    def test_both_correct_branches(self):
        for shallow in (False, True):
            self.assertEqual(watchdog.evaluate_early_exit(*fixture(shallow)), [])

    def test_branch_and_data_failures(self):
        sc, log, uart = fixture(True)
        for changed in (uart.replace("shallow_out", "deep_out"),
                        uart.replace("PASS", "FAIL"), uart.replace("shallow=1", "shallow=0"),
                        uart.replace("cerf_fb_evt=0x2", "cerf_fb_evt=0x0")):
            self.assertTrue(watchdog.evaluate_early_exit(sc, log, changed))

    def test_forbidden_dispatch_and_missing_prefix(self):
        sc, log, uart = fixture(True)
        for changed in (
            log + "[BINGO_DISPATCH] 810 chip=0 task=9 core=1 cluster=0\n",
            log + "[BINGO_DISPATCH] 820 chip=0 task=8 core=0 cluster=1\n",
            log.replace("[BINGO_DISPATCH] 100 chip=0 task=3 core=0 cluster=1\n", ""),
            log.replace("type 1 clear g0 set g1", "type 2 clear g0 set g1"),
            log.replace("[BINGO_CERF_FB] 700", "[BINGO_CERF_FB] 850"),
        ):
            self.assertTrue(watchdog.evaluate_early_exit(sc, changed, uart))
        sc, log, uart = fixture(False)
        self.assertTrue(watchdog.evaluate_early_exit(
            sc, log + "[BINGO_DISPATCH] 550 chip=0 task=10 core=2 cluster=0\n", uart))
        self.assertTrue(watchdog.evaluate_early_exit(
            sc, log.replace("[BINGO_DISPATCH] 500 chip=0 task=9",
                            "[BINGO_DISPATCH] 620 chip=0 task=9"), uart))

    def test_missing_graph(self):
        sc, log, uart = fixture(True)
        del sc["early_exit_task_ids"]
        self.assertTrue(watchdog.evaluate_early_exit(sc, log, uart))

    def test_fault_gid_comes_from_generated_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph = root / ("target/sw/host/apps/offload_bingo_hw/single_chip/workloads"
                            "/early_exit_2cluster/final_dfg.csv")
            graph.parent.mkdir(parents=True)
            graph_csv(graph, offset=100)
            runner = object.__new__(watchdog.EarlyExitSimRunner)
            runner.inject_fault = True
            runner.output_dir = root
            with patch.object(watchdog, "_REPO_ROOT", root), \
                    patch.object(watchdog, "clean_app_builds") as clean, \
                    patch.object(watchdog.NoTraceSimRunner, "build_apps_and_stage",
                                 return_value=[]) as stage:
                runner.build_apps_and_stage([{"extra_user_flags": "-DBINGO_EE_EXPECT_SHALLOW=1"}])
            self.assertEqual(stage.call_count, 2)
            flags = stage.call_args.args[0][0]["extra_user_flags"]
            self.assertIn("-DBINGO_WD_FAULT_GID=108 ", flags)
            self.assertIn("-DBINGO_WD_FAULT_CORE=0", flags)
            self.assertIn("-DBINGO_WD_FAULT_CLUSTER=0", flags)
            clean.assert_called_once_with("early_exit_2cluster")


if __name__ == "__main__":
    unittest.main()
