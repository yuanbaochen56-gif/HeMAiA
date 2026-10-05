"""Opt-in simulation logging and the Questa launcher's argument convention."""
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "watchdog_dispatch_log", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
ROOT = Path(__file__).resolve().parents[4]


class DispatchLogTests(unittest.TestCase):
    def fenced_pairing(self, *, second_task=0, fault_any_core=True, completed=False):
        with tempfile.TemporaryDirectory() as directory:
            graph = Path(directory) / "graph.csv"
            graph.write_text("ID,Kernel,Chiplet,Core,Cluster\n"
                             "8,__host_bingo_kernel_exit,0x0,2,0\n")
            scene = dict(victim=(0, 1, 0), fault_gid=0, fault_stall_cycles=0,
                         fault_any_core=fault_any_core, expect_fence=True,
                         expect_eoc=True, expect_replay_stuck=False,
                         expect_wd_other={(0, 1, 1): [(1, 0), (1, 1)]},
                         dispatch_log=True, dispatch_graph_csv=graph)
            log = ("[BINGO_DISPATCH] 10 chip=0 task=0 core=1 cluster=0\n"
                   f"[BINGO_DISPATCH] 20 chip=0 task={second_task} core=1 cluster=1\n"
                   "[BINGO_WD] 30 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0\n"
                   "[BINGO_WD] 40 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1\n"
                   "[BINGO_WD] 50 chip=0 core=1 cluster=1 dead_suspect=1 fenced=0\n"
                   "[BINGO_WD] 60 chip=0 core=1 cluster=1 dead_suspect=1 fenced=1\n")
            if completed:
                log += f"[BINGO_DONE] 70 chip=0 task={second_task} core=1 cluster=1\n"
            log += ("[BINGO_DISPATCH] 80 chip=0 task=8 core=2 cluster=0\n"
                    "All chips finished successfully at 90\n")
            with patch.object(watchdog, "dispatch_host_slot", return_value=(0, 2, 0)):
                return watchdog.evaluate("pairing_fixture", scene, log, "Check [C_l3]: PASS\n")

    def test_two_declared_fenced_fault_tasks_and_host_exit_pass(self):
        self.assertEqual(self.fenced_pairing(), [])

    def test_second_fenced_slot_must_hold_the_fault_task(self):
        self.assertTrue(any("unpaired dispatches" in problem
                            for problem in self.fenced_pairing(second_task=1)))

    def test_other_fenced_slot_requires_fault_any_core(self):
        self.assertTrue(any("unpaired dispatches" in problem
                            for problem in self.fenced_pairing(fault_any_core=False)))

    def test_declared_fenced_fault_task_must_still_be_pending(self):
        self.assertTrue(any("unpaired dispatches" in problem
                            for problem in self.fenced_pairing(completed=True)))

    def test_template_logging_is_simulation_only_and_done_is_opt_in(self):
        text = (ROOT / "hw/occamy/occamy_quad_ctrl.sv.tpl").read_text()
        start = text.index("logic dispatch_log_en;")
        self.assertGreater(text.rfind("`ifndef SYNTHESIS", 0, start),
                           text.rfind("`endif", 0, start))
        end = text.index("`endif", start)
        monitor = text[start:end]
        self.assertIn('$test$plusargs("bingo_dispatch_log")', monitor)
        self.assertIn("(|bingo_cerf_fb_en) || (|bingo_risk_late) || dispatch_log_en", monitor)
        self.assertIn("i_bingo_hw_manager.ready_queue_pop[core][cluster]", monitor)
        self.assertIn("&& dispatch_log_en &&\n"
                      "            i_bingo_hw_manager.done_q_push[core][cluster]", monitor)
        self.assertIn("[BINGO_DISPATCH] %0t chip=%0d task=%0d core=%0d cluster=%0d", monitor)
        self.assertIn("[BINGO_DONE] %0t chip=%0d task=%0d core=%0d cluster=%0d", monitor)

    def test_plusargs_reach_vsim_without_changing_default_launch(self):
        import hemaia_sim_runner as runner
        for enabled in (False, True):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                binary = path / "task/bin"
                binary.mkdir(parents=True)
                (binary / "occamy_chip.vsim").write_text(
                    '#!/bin/sh\nvsim $2\n')
                (binary / "occamy_chip.vsim").chmod(0o755)
                tool = path / "vsim"
                tool.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > args.txt\n'
                                'echo "All chips finished successfully"\n')
                tool.chmod(0o755)
                instance = runner.HeMAiASimRunner(
                    repo_root=ROOT, output_dir=path, engine="vsim", with_waveform=False,
                    cfg="target/rtl/cfg/hemaia_ci.hjson", sim_cfg="target/sim/cfg/sim_rtl.hjson",
                    with_macro=False, with_d2d=False, with_pll=False,
                    plusargs=["+bingo_dispatch_log"] if enabled else [])
                with patch.dict(os.environ, {"PATH": str(path) + ":" + os.environ["PATH"]}):
                    results, _ = instance.run_simulations([(path / "task", "fixture")])
                self.assertTrue(results[str(path / "task")][0])
                self.assertEqual((binary / "args.txt").read_text().split(),
                                 ["+bingo_dispatch_log"] if enabled else [])

    def test_driver_opt_in_and_continuity_default(self):
        text = Path(watchdog.__file__).read_text()
        self.assertIn('plusargs=["+bingo_dispatch_log"] if sc.get("dispatch_log", False) else []', text)
        for name in ("t18", "t19", "t40", "t41", "t42", "t43", "t44"):
            self.assertFalse(watchdog.SCENARIOS[name].get("dispatch_log", False))
        self.assertIn('lines.append(f"- dispatch_log:', text)


if __name__ == "__main__":
    unittest.main()
