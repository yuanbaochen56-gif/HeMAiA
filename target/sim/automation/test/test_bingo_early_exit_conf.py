"""Confidence/fault branch checking and the strict skipped-fault timing gate."""
import copy
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "watchdog_early_exit_conf", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

IDS = dict(select=0, gemm1=3, prefix_store=4, requant=5, gate=6, gemm2=9,
           deep_store=10, backup=11, join=12, primary_exit=14, prefix_exit=16)


def fixture(name):
    sc = copy.deepcopy(watchdog.SCENARIOS[name])
    sc.update(early_exit_task_ids=IDS, early_exit_conf_data=dict(threshold=16, margins=[4, 43]))
    sample, decision, fault = sc["t1_user"][0], int(name in ("t45", "t48")), int(name == "t47")
    shallow = int(decision or fault)
    dispatches = [(10, 0, 2, 0), (100, 3, 0, 1), (200, 4, 1, 1),
                  (300, 5, 2, 0), (350, 6, 2, 0)]
    if not decision:
        dispatches += [(400, 9, 0, 0)]
    if shallow:
        dispatches += [(800 if fault else 400, 11, 2, 0), (900 if fault else 500, 12, 2, 0)]
    else:
        dispatches += [(500, 10, 1, 0), (600, 12, 2, 0), (650, 14, 0, 0), (700, 16, 0, 1)]
    log = "".join(f"[BINGO_DISPATCH] {t} chip=0 task={task} core={core} cluster={cluster}\n"
                  for t, task, core, cluster in dispatches)
    if fault:
        log += ("[BINGO_WD] 500 chip=0 core=0 cluster=0 dead_suspect=1 fenced=0\n"
                "[BINGO_WD] 600 chip=0 core=0 cluster=0 dead_suspect=1 fenced=1\n"
                "[BINGO_REPLAY_STUCK] 650 chip=0 core=0 cluster=0: no live core may run task 9 (logical core 0)\n"
                "[BINGO_CERF_FB] 700 type 1 clear g0 set g1\n")
    log += "All chips finished successfully at 1000\n"
    uart = (f"[Host] Bingo status: replay_stuck={fault} cerf=0x{2 if shallow else 1:x} "
            f"cerf_fb_en=0x2 cerf_fb_evt=0x{2 if fault else 0:x}\n"
            f"[EarlyExitConf] sample={sample} decision={decision} fault_evt={fault} shallow={shallow}\n"
            f"[Host] Check [{'shallow_out' if shallow else 'deep_out'}]: PASS\n")
    return sc, log, uart


class EarlyExitConfCheckerTests(unittest.TestCase):
    def test_runner_uses_generated_gemm2_slot_for_both_health_and_fault(self):
        from test_bingo_test_cfg import write_banks

        for inject in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                graph = (path / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads"
                         "/early_exit_conf_2cluster/final_dfg.csv")
                graph.parent.mkdir(parents=True)
                graph.write_text("ID,Chiplet,Cluster,Core,Type,Kernel\n"
                                 "108,00,1,1,normal,__snax_bingo_kernel_gemm_full\n")
                graph.with_name("early_exit_conf_data.json").write_text(
                    json.dumps(dict(threshold=16, margins=[2, 87])))
                task_dir = path / "task"
                write_banks(task_dir / "bin/app_chip_0_0",
                            watchdog.test_cfg_bytes(watchdog.test_cfg_defaults()))
                runner = watchdog.TestCfgSimRunner(
                    test_cfg=watchdog.test_cfg_defaults(), early_exit_conf=True,
                    inject_fault=inject, repo_root=path, output_dir=path, skip_setup=True,
                    engine="vsim", with_waveform=False, cfg="target/rtl/cfg/hemaia_ci.hjson",
                    sim_cfg="target/sim/cfg/sim_rtl.hjson",
                    with_macro=False, with_d2d=False, with_pll=False)
                with patch.object(watchdog.NoTraceSimRunner, "build_apps_and_stage",
                                  return_value=[(task_dir, "app")]) as build, \
                        patch.object(watchdog, "early_exit_conf_task_ids",
                                     return_value={"gemm2": 108}), \
                        patch.object(watchdog.shutil, "copyfile"), \
                        patch.object(watchdog, "test_cfg_elf_location",
                                     return_value=dict(offset=0, size=256)):
                    runner.build_apps_and_stage([{}])
                build.assert_called_once()
                cfg = runner.test_cfg_record["configuration"]
                self.assertEqual((cfg["fault_cluster"], cfg["fault_core"]), (1, 1))
                self.assertEqual(cfg["fault_gid"], 108 if inject else 0xFFFFFFFF)

    def test_four_correct_branches(self):
        for name in ("t45", "t46", "t47", "t48"):
            self.assertEqual(watchdog.evaluate_early_exit_conf(*fixture(name)), [], name)

    def test_wrong_branch_decision_margin_and_golden(self):
        sc, log, uart = fixture("t45")
        for changed in (uart.replace("decision=1", "decision=0"),
                        uart.replace("shallow=1", "shallow=0"),
                        uart.replace("shallow_out", "deep_out"),
                        uart.replace("PASS", "FAIL"), uart + uart):
            self.assertTrue(watchdog.evaluate_early_exit_conf(sc, log, changed))
        sc["early_exit_conf_data"]["margins"][1] = 4
        self.assertTrue(watchdog.evaluate_early_exit_conf(sc, log, uart))

    def test_confident_runs_forbid_deep_tasks_faults_and_exits(self):
        for name in ("t45", "t48"):
            sc, log, uart = fixture(name)
            for changed in (
                log + "[BINGO_CERF_FB] 700 type 1 clear g0 set g1\n",
                log + "[BINGO_DISPATCH] 360 chip=0 task=9 core=0 cluster=0\n",
                log + "[BINGO_DISPATCH] 360 chip=0 task=10 core=1 cluster=0\n",
                log + "[BINGO_DISPATCH] 360 chip=0 task=14 core=0 cluster=0\n",
                log + "[BINGO_DISPATCH] 360 chip=0 task=16 core=0 cluster=1\n",
                log + "[BINGO_WD] 700 chip=0 core=0 cluster=0 dead_suspect=1 fenced=0\n",
            ):
                self.assertTrue(watchdog.evaluate_early_exit_conf(sc, changed, uart), name)

    def test_deep_healthy_forbids_backup_and_fault_requires_one_fallback(self):
        sc, log, uart = fixture("t46")
        self.assertTrue(watchdog.evaluate_early_exit_conf(
            sc, log + "[BINGO_DISPATCH] 550 chip=0 task=11 core=2 cluster=0\n", uart))
        sc, log, uart = fixture("t47")
        for changed in (log.replace("[BINGO_CERF_FB] 700 type 1 clear g0 set g1\n", ""),
                        log + "[BINGO_CERF_FB] 701 type 1 clear g0 set g1\n",
                        log.replace("type 1 clear", "type 2 clear"),
                        log.replace("[BINGO_CERF_FB] 700", "[BINGO_CERF_FB] 801"),
                        log + "[BINGO_DISPATCH] 850 chip=0 task=10 core=1 cluster=0\n"):
            self.assertTrue(watchdog.evaluate_early_exit_conf(sc, changed, uart))

    def test_prefix_required_and_ordered(self):
        sc, log, uart = fixture("t45")
        for changed in (log.replace("[BINGO_DISPATCH] 10 chip=0 task=0 core=2 cluster=0\n", ""),
                        log.replace("[BINGO_DISPATCH] 350 chip=0 task=6",
                                    "[BINGO_DISPATCH] 299 chip=0 task=6")):
            self.assertTrue(watchdog.evaluate_early_exit_conf(sc, changed, uart))

    def test_missing_graph_or_margin_data_is_failure(self):
        for field in ("early_exit_task_ids", "early_exit_conf_data"):
            sc, log, uart = fixture("t45")
            del sc[field]
            self.assertTrue(watchdog.evaluate_early_exit_conf(sc, log, uart))

    def test_skipped_fault_pair_is_strict_to_each_ps_and_uart_byte(self):
        _, first, uart = fixture("t45")
        _, second, other_uart = fixture("t48")
        self.assertEqual(watchdog.check_early_exit_conf_pair(first, uart, second, other_uart), [])
        for changed in (second.replace("at 1000", "at 1001"),
                        second.replace("[BINGO_DISPATCH] 100", "[BINGO_DISPATCH] 101"),
                        second.replace("All chips finished successfully at 1000\n", "")):
            self.assertTrue(watchdog.check_early_exit_conf_pair(first, uart, changed, other_uart))
        self.assertTrue(watchdog.check_early_exit_conf_pair(first, uart, second, other_uart + "\n"))
        self.assertTrue(watchdog.check_early_exit_conf_pair(
            first, uart.encode(), second, other_uart.replace("\n", "\r\n").encode()))

    def test_graph_ids_are_discovered_including_selector_and_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "final_dfg.csv"
            rows = [
                (0, 0, 2, "__host_bingo_kernel_ee_select"),
                (3, 1, 0, "__snax_bingo_kernel_gemm_full"),
                (4, 1, 1, "__snax_bingo_kernel_idma_1d_copy"),
                (5, 0, 2, "__host_bingo_kernel_early_exit_requant"),
                (6, 0, 2, "__host_bingo_kernel_ee_conf_gate"),
                (9, 0, 0, "__snax_bingo_kernel_gemm_full"),
                (10, 0, 1, "__snax_bingo_kernel_idma_1d_copy"),
                (11, 0, 2, "__host_bingo_kernel_idma"),
                (12, 0, 2, "__host_bingo_kernel_dummy"),
                (14, 0, 0, "__snax_bingo_kernel_exit"),
                (16, 1, 0, "__snax_bingo_kernel_exit"),
            ]
            with path.open("w") as stream:
                writer = csv.writer(stream)
                writer.writerow(["ID", "Chiplet", "Cluster", "Core", "Type", "Kernel"])
                writer.writerows((task + 100, "00", cluster, core, "normal", kernel)
                                 for task, cluster, core, kernel in rows)
            self.assertEqual(watchdog.early_exit_conf_task_ids(path),
                             {name: value + 100 for name, value in IDS.items()})
            path.write_text(path.read_text() + "200,00,0,2,gating,__host_bingo_kernel_ee_conf_gate\n")
            with self.assertRaises(ValueError):
                watchdog.early_exit_conf_task_ids(path)

    def test_family_and_scenario_data_only_controls(self):
        self.assertEqual(watchdog.T1_FAMILIES["F3"], ("t45", "t46", "t47", "t48"))
        for name in watchdog.T1_FAMILIES["F3"]:
            sc = watchdog.SCENARIOS[name]
            cfg = watchdog.scenario_test_cfg(sc)
            self.assertEqual(cfg["user"], sc["t1_user"])
            self.assertEqual(sc["workload"], "early_exit_conf_2cluster")
            self.assertTrue(sc["t1"])
            self.assertEqual(sc["extra_flags"], "")
            self.assertEqual((cfg["fault_cluster"], cfg["fault_core"]), (0, 0))
        configs = [watchdog.scenario_test_cfg(watchdog.SCENARIOS[name])
                   for name in watchdog.T1_FAMILIES["F3"]]
        for cfg in configs[1:]:
            watchdog.check_same_ps_fault_cfg(configs[0], cfg)
        self.assertEqual(configs[0]["fault_gid"], 0xFFFFFFFF)
        self.assertEqual(configs[1]["fault_gid"], 0xFFFFFFFF)


if __name__ == "__main__":
    unittest.main()
