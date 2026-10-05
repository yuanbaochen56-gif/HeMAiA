import itertools
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from bingo_c3 import CLASSES, REASONS, classify, classify_files, outcome_candidates

SCRIPT = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
OK = "# All chips finished successfully at 100\n"
DISPATCH = "# [BINGO_DISPATCH] 10 chip=0 task=4 core=0 cluster=0\n"
WD = "# [BINGO_WD] 30 chip=0 core=0 cluster=0 dead_suspect=1 fenced=1\n"
TIMEOUT = "TIMEOUT: simulation exceeded 900s\n"
UART = "[Host] Bingo status: replay_stuck=0 fenced=0x0\nCheck [D]: PASS\n"
ERR = ("# Simulation of chip_0_0 finished with status 3\n"
       "# ** Fatal: All chips finished with errors at 100\n")
STUCK = "# [BINGO_REPLAY_STUCK] 50 no live core\n"
REPLAY = "# [BINGO_REPLAY] 40 chip=0 task=4\n"
VLM_FATAL = "# ** Fatal: Read failure in vlm process (0,0)\n"


class ClassificationTests(unittest.TestCase):
    def test_five_classes_handwritten(self):
        for expected, log, uart in (
                ("recovered", OK, UART),
                ("degraded", OK + "[BINGO_CERF_FB] 50 type 1 clear g0 set g1\n", UART),
                ("detected-stuck", WD + STUCK + TIMEOUT, "Host Start\n"),
                ("hang", TIMEOUT, "Host Start\n"),
                ("wrong", ERR, UART.replace("PASS", "FAIL"))):
            with self.subTest(expected=expected):
                result = classify(log, uart)
                self.assertEqual(result["class"], expected)
                self.assertEqual(result["unclassified_reason"], "")

    def test_unclassified_each_reason(self):
        examples = (
            ("U1", None, UART, {}),
            ("U2", "simulator crashed\n", "Host Start\n", {}),
            ("U3", OK, "[Host] Bingo status: replay_stuck=0\n", {}),
            ("U4", OK + DISPATCH, UART, dict(fault_gid=5, victim=(0, 0, 0))),
            ("U5", OK, UART.replace("replay_stuck=0", "replay_stuck=1"), {}),
        )
        for reason, log, uart, kwargs in examples:
            with self.subTest(reason=reason):
                result = classify(log, uart, **kwargs)
                self.assertEqual(result["class"], "unclassified")
                self.assertIn(reason, result["unclassified_reason"].split(";"))
                if reason != "U1":
                    self.assertEqual(result["unclassified_reason"], reason)

    def test_missing_and_empty_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            log, uart = Path(tmp) / "sim_run.log", Path(tmp) / "uart_chip_0_0.log"
            self.assertIn("U1", classify_files(log, uart)["unclassified_reason"])
            log.touch()
            uart.write_text(UART)
            self.assertIn("U1", classify_files(log, uart)["unclassified_reason"])

    def test_abnormal_markers(self):
        for marker in ("[BINGO_ASSERT]", "[ASSERT FAILED]",
                       "Assertion some.property failed"):
            self.assertIn("U2", classify(OK + marker, UART)["unclassified_reason"])
        self.assertEqual(classify(WD + "Fatal: crash", UART)["class"], "unclassified")
        self.assertEqual(classify(WD + TIMEOUT, UART)["class"], "hang")

    def test_host_check_failure_is_completed_wrong_or_detected(self):
        for stuck, expected in (("", "wrong"), (STUCK, "detected-stuck")):
            with self.subTest(stuck=bool(stuck)):
                result = classify(ERR + WD + REPLAY + stuck, UART.replace("PASS", "FAIL"))
                self.assertTrue(result["eoc"])
                self.assertEqual(result["exit_status"], 3)
                self.assertEqual(result["class"], expected)
                self.assertEqual(result["annot"], "after_replay")
                self.assertEqual(result["unclassified_reason"], "")

    def test_recovery_then_timeout_is_hang(self):
        result = classify(WD + REPLAY + TIMEOUT, UART)
        self.assertEqual(result["class"], "hang")
        self.assertEqual(result["annot"], "after_replay")
        self.assertEqual(classify(WD + TIMEOUT, UART)["annot"], "after_fence")

    def test_truncated_and_conflicting_endings(self):
        self.assertEqual(classify(DISPATCH, UART)["unclassified_reason"], "U2")
        self.assertEqual(classify(OK + TIMEOUT, UART)["unclassified_reason"], "U2")

    def test_status_check_consistency(self):
        for log, uart in ((ERR, UART), (OK, UART.replace("PASS", "FAIL")),
                          (OK + "Simulation of chip_0_0 finished with status 0\n",
                           UART.replace("PASS", "FAIL"))):
            with self.subTest(log=log, uart=uart):
                self.assertEqual(classify(log, uart)["unclassified_reason"], "U3")

    def test_non_chip_end_fatal(self):
        self.assertEqual(classify(OK + "# ** Fatal: simulator crash\n", UART)
                         ["unclassified_reason"], "U2")
        self.assertEqual(classify(ERR + "# ** Fatal: simulator crash\n",
                                 UART.replace("PASS", "FAIL"))["unclassified_reason"], "U2")

    def test_timeout_final_unique_vlm_fatal_is_kill_artifact(self):
        for prefix, expected in ((WD + REPLAY, "hang"), (WD + STUCK, "detected-stuck")):
            with self.subTest(expected=expected):
                log = prefix + TIMEOUT + VLM_FATAL + "\n  \n"
                result = classify(log, UART)
                self.assertEqual(result["class"], expected)
                self.assertTrue(result["kill_artifact"])
                self.assertEqual(result["unclassified_reason"], "")
                self.assertEqual(result["fatal_lines"], [dict(
                    source="sim_run.log", line_number=4, raw=VLM_FATAL.strip(),
                    chip_end=False, kill_artifact=True)])
        result = classify(TIMEOUT + VLM_FATAL.replace("(0,0)", "(12,34)"), UART)
        self.assertEqual(result["class"], "hang")
        self.assertTrue(result["kill_artifact"])

    def test_vlm_fatal_without_timeout_is_abnormal(self):
        result = classify(OK + VLM_FATAL, UART)
        self.assertEqual(result["unclassified_reason"], "U2")
        self.assertFalse(result["kill_artifact"])

    def test_vlm_fatal_not_final_is_abnormal(self):
        result = classify(TIMEOUT + VLM_FATAL + "# more output\n", UART)
        self.assertEqual(result["unclassified_reason"], "U2")
        self.assertFalse(result["kill_artifact"])

    def test_vlm_fatal_not_unique_is_abnormal(self):
        for log, uart in (
                (TIMEOUT + "# ** Fatal: other crash\n" + VLM_FATAL, UART),
                (TIMEOUT + VLM_FATAL + "# ** Fatal: other crash\n", UART),
                (TIMEOUT + VLM_FATAL + VLM_FATAL, UART),
                (TIMEOUT + VLM_FATAL, UART + "** Fatal: UART crash\n")):
            with self.subTest(log=log, uart=uart):
                result = classify(log, uart)
                self.assertEqual(result["unclassified_reason"], "U2")
                self.assertFalse(result["kill_artifact"])
                self.assertEqual(len(result["fatal_lines"]), 2)

    def test_multichip_status_uses_maximum(self):
        log = (ERR.replace("chip_0_0", "chip_12_34")
               + "Simulation of chip_1_2 finished with status 0\n")
        result = classify(log, UART.replace("PASS", "FAIL"))
        self.assertEqual(result["exit_status"], 3)
        self.assertEqual(result["class"], "wrong")
        self.assertTrue(result["fatal_lines"][0]["chip_end"])
        self.assertFalse(result["kill_artifact"])

    def test_cerf_annotation(self):
        result = classify(OK + WD + "[BINGO_CERF_FB] 50 type 1 clear g0 set g1\n", UART)
        self.assertEqual(result["class"], "degraded")
        self.assertEqual(result["annot"], "after_fence;after_cerf")

    def test_all_checks_status_and_fault_slot(self):
        self.assertEqual(classify(ERR, UART + "Check [other]: FAIL")["class"], "wrong")
        self.assertEqual(classify(OK + DISPATCH, UART, fault_gid=4,
                                  victim=(0, 0, 0))["class"], "recovered")
        self.assertEqual(classify(OK + DISPATCH, UART, fault_gid=4,
                                  victim=(0, 0, 1))["unclassified_reason"], "U4")
        self.assertEqual(classify(OK + DISPATCH, UART, fault_gid=4,
                                  victim=(0, 0, 1), any_core=True)["class"], "recovered")
        self.assertEqual(classify(OK, UART)["class"], "recovered")  # healthy skips U4
        self.assertEqual(classify(OK, "Check [D]: PASS")["unclassified_reason"], "U3")

    def test_multiple_reasons_and_raw_events(self):
        result = classify(OK + DISPATCH + "[BINGO_ASSERT] bad", "Check [D]: PASS",
                          fault_gid=7, victim=(0, 0, 0))
        self.assertEqual(result["unclassified_reason"], "U2;U3;U4")
        self.assertIn(dict(source="sim_run.log", line_number=2, raw=DISPATCH.strip()),
                      result["events"])

    def test_exhaustive_extended_partition(self):
        names = ("eoc", "checks_pass", "cerf", "stuck", "fence", "timeout")
        reached = set()
        for values in itertools.product((False, True), repeat=len(names) + len(REASONS)):
            signals = dict(zip(names, values[:len(names)]))
            prechecks = dict(zip(REASONS, values[len(names):]))
            candidates, reasons = outcome_candidates(signals, prechecks)
            self.assertEqual(len(candidates), 1, (signals, prechecks))
            reached.update(candidates)
            self.assertEqual(bool(reasons), candidates == ["unclassified"])
        self.assertEqual(reached, set(CLASSES))


class ScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = runpy.run_path(str(SCRIPT))
        cls.scenes = cls.driver["c3_scenarios"]()

    def test_matrix_counts_and_order(self):
        counts = {family: sum(s["c3_family"] == family for s in self.scenes.values())
                  for family in ("healthy", "A", "A-L1", "B", "C", "D", "E")}
        self.assertEqual(counts, dict(zip(counts, (7, 16, 2, 18, 6, 6, 3))))
        self.assertEqual(len(self.scenes), 58)
        order = list(counts)
        indices = [order.index(s["c3_family"]) for s in self.scenes.values()]
        self.assertEqual(indices, sorted(indices))

    def test_t1_controls_and_fault_types(self):
        for scene in self.scenes.values():
            self.assertTrue(scene["dispatch_log"])
            cfg = self.driver["scenario_test_cfg"](scene)
            self.assertTrue(scene["t1"])
            fault_type = scene["c3_fault_type"]
            if fault_type == "healthy":
                self.assertEqual(cfg["fault_gid"], 0xFFFFFFFF)
                self.assertEqual(cfg["fault_cluster"], 0xFFFFFFFF)
                self.assertEqual(cfg["fault_core"], 0xFFFFFFFF)
            else:
                self.assertEqual(cfg["fault_after_kernel"], int(fault_type == "post"))
                self.assertEqual(cfg["fault_stall_cycles"],
                                 {"pre": 0, "post": 0, "slow": 350000, "zombie": 700000}[fault_type])
        controls = [s for s in self.scenes.values() if s["c3_family"] == "healthy"]
        self.assertEqual(len({(s["workload"], s["cfg_suffix"]) for s in controls}), 7)

    def test_task_positions_and_timeouts(self):
        a = [s for s in self.scenes.values() if s["c3_family"] == "A"]
        self.assertEqual({s["fault_gid"] for s in a}, {0, 3, 5, 6})
        for scene in a:
            self.assertEqual(scene["victim"], (0, 1, int(scene["fault_gid"] == 6)))
        for scene in self.scenes.values():
            blocked = (scene["c3_family"] == "A-L1" or
                       scene["c3_family"] == "B" and scene["cfg_suffix"] == "_cerf_l1"
                       and scene["fault_gid"] in (2, 5))
            self.assertEqual(scene["sim_timeout_s"], 900 if blocked else 1200)

    def test_e2_scenarios_and_make_switch(self):
        scenes = self.driver["c3_e2_scenarios"]()
        self.assertEqual(list(scenes), ["c3_ok_inplace_e2", "c3_e2_pre",
                                       "c3_e2_post", "c3_e2_zombie"])
        for scene in scenes.values():
            self.assertTrue(scene["inplace_branch_check"])
            self.assertEqual(scene["workload"], "int32_inplace_cerf_2cluster")
        cls = self.driver["CoreTypeCheckedSimRunner"]
        runner = object.__new__(cls)
        runner.inplace_branch_check = True
        with patch.object(self.driver["HeMAiASimRunner"], "_container") as container:
            runner._container(["make", "apps", "WORKLOAD=int32_inplace_cerf_2cluster"])
            self.assertEqual(container.call_args.args[0][-1], "BRANCH_CHECK=1")
            runner._container(["make", "rtl"])
            self.assertEqual(container.call_args.args[0], ["make", "rtl"])


if __name__ == "__main__":
    unittest.main()
