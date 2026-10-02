"""Fast log-checker tests, without an RTL build or Questa."""

import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

_path = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
_spec = importlib.util.spec_from_file_location("bingo_watchdog_driver", _path)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)


def fixture(fault):
    tasks = [(10, 2, 1, 0), (20, 3, 1, 0), (30, 4, 0, 0)]
    if fault:
        tasks += [(110, 6, 1, 1), (120, 7, 1, 1), (130, 8, 0, 1), (140, 9, 1, 1)]
    else:
        tasks += [(40, 5, 1, 0), (160, 12, 0, 0)]
    tasks.append((150, 10, 2, 0))
    log = "\n".join(f"[BINGO_DISPATCH] {t} chip=0 task={task} core={core} cluster={cl}"
                    for t, task, core, cl in tasks)
    if fault:
        log += "\n[BINGO_CERF_FB] 100 type 1 clear g0 set g1"
    expert = int(fault)
    uart = (f"[Host] Bingo status: replay_stuck={int(fault)} cerf=0x{1 << expert:x} cerf_fb_en=0x2 "
            f"cerf_fb_evt=0x{2 if fault else 0:x}\n"
            f"[Host] Check [expert_{expert}_D]: PASS\n"
            f"[MoE2] router selected expert 0; join complete; output expert {expert}\n")
    return log, uart


class CerfFallbackTests(unittest.TestCase):
    def check_fixture(self, fault, log=None, uart=None):
        original_log, original_uart = fixture(fault)
        sc = driver.SCENARIOS["s16" if fault else "s17"]
        return driver.evaluate_cerf_fallback(
            sc, original_log if log is None else log, original_uart if uart is None else uart)

    def test_fallback_and_healthy(self):
        for fault in (True, False):
            with self.subTest(fault=fault):
                self.assertEqual(self.check_fixture(fault), [])

    def test_missing_duplicate_or_wrong_fallback(self):
        log, _ = fixture(True)
        for changed in (
            log.replace("[BINGO_CERF_FB]", "[OTHER]"),
            log + "\n[BINGO_CERF_FB] 101 type 1 clear g0 set g1",
            log.replace("type 1 clear g0 set g1", "type 2 clear g0 set g1"),
            log.replace("clear g0 set g1", "clear g1 set g0"),
        ):
            with self.subTest(log=changed):
                self.assertTrue(self.check_fixture(True, log=changed))

    def test_cleared_task_after_switch_or_early_backup(self):
        log, _ = fixture(True)
        self.assertTrue(self.check_fixture(True, log=log.replace("DISPATCH] 30", "DISPATCH] 101")))
        self.assertTrue(self.check_fixture(True, log=log.replace("DISPATCH] 110", "DISPATCH] 90")))

    def test_wrong_slot_missing_backup_or_missing_join(self):
        log, _ = fixture(True)
        for changed in (
            log.replace("task=8 core=0 cluster=1", "task=8 core=0 cluster=0"),
            log.replace("task=8", "task=99"),
            log.replace("task=10", "task=99"),
            log + "\n[BINGO_DISPATCH] 151 chip=0 task=8 core=0 cluster=1",
        ):
            with self.subTest(log=changed):
                self.assertTrue(self.check_fixture(True, log=changed))

    def test_wrong_host_event_or_golden(self):
        _, uart = fixture(True)
        for changed in (
            uart.replace("cerf_fb_evt=0x2", "cerf_fb_evt=0x0"),
            uart.replace("cerf=0x2", "cerf=0x1"),
            uart.replace("expert_1_D", "expert_0_D"),
            uart.replace(": PASS", ": FAIL"),
        ):
            with self.subTest(uart=changed):
                self.assertTrue(self.check_fixture(True, uart=changed))

    def test_healthy_must_not_run_backup_or_fallback(self):
        log, _ = fixture(False)
        self.assertTrue(self.check_fixture(False, log=log + "\n[BINGO_DISPATCH] 140 chip=0 task=8 core=0 cluster=1"))
        self.assertTrue(self.check_fixture(False, log=log + "\n[BINGO_CERF_FB] 100 type 1 clear g0 set g1"))

    def test_one_and_two_cluster_type_tables(self):
        for entries, clusters in (("4'd1", "0..0"), ("4'd1, 4'd1", "1..0")):
            with self.subTest(clusters=clusters), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                rtl = root / driver.QUAD_CTRL_RTL
                rtl.parent.mkdir(parents=True)
                rtl.write_text(f"    {entries}, // core 0, clusters {clusters}\n")
                with patch.object(driver, "_REPO_ROOT", root):
                    self.assertEqual(driver.check_core_types({0: 1}), [])
                    self.assertTrue(driver.check_core_types({0: 2}))

    def test_non_bingo_assertion_is_a_failure(self):
        log, uart = fixture(False)
        log += "\n** Error: [ASSERT FAILED] DMAWriteDataCorrect"
        problems = driver.evaluate("s17", driver.SCENARIOS["s17"], log, uart)
        self.assertTrue(any("RTL assertion failure" in p for p in problems))

    def test_core_type_check_survives_regenerated_rtl(self):
        for runner_cls in (driver.CoreTypeCheckedSimRunner, driver.NoTraceSimRunner):
            for initial_type in (1, 2):
                with self.subTest(runner=runner_cls.__name__, initial_type=initial_type):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        rtl = root / driver.QUAD_CTRL_RTL
                        rtl.parent.mkdir(parents=True)
                        rtl.write_text(f"    4'd{initial_type}, // core 0, clusters 0..0\n")

                        def overwrite_during_simulation(*_args):
                            rtl.write_text("    4'd3, // core 0, clusters 0..0\n")
                            return {}, 0

                        with patch.object(driver.HeMAiASimRunner, "__init__", return_value=None):
                            runner = runner_cls(expected_core_types={0: 1})
                        with patch.object(driver, "_REPO_ROOT", root), patch.object(
                            driver.HeMAiASimRunner, "run_simulations",
                            side_effect=overwrite_during_simulation,
                        ):
                            runner.run_simulations([])
                            self.assertEqual(bool(runner.core_type_problems), initial_type != 1)
                            self.assertTrue(driver.check_core_types({0: 1}))

    def test_core_type_check_is_optional(self):
        with patch.object(driver.HeMAiASimRunner, "__init__", return_value=None):
            runner = driver.CoreTypeCheckedSimRunner()
        with patch.object(driver, "check_core_types") as check, patch.object(
            driver.HeMAiASimRunner, "run_simulations", return_value=({}, 0),
        ):
            runner.run_simulations([])
            check.assert_not_called()
            self.assertEqual(runner.core_type_problems, [])


if __name__ == "__main__":
    unittest.main()
