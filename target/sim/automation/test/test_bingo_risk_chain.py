"""Fast checks for the P6a DMA-chain scenarios."""

import importlib.util
from pathlib import Path
import unittest

_path = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
_spec = importlib.util.spec_from_file_location("bingo_risk_driver", _path)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)


def fixture(parked):
    tasks = [(i, 1, 0) for i in range(2 if parked else 4)]
    tasks += [(i, 1, 1) for i in range(2 if parked else 3, 6)]
    log = "\n".join(
        f"[BINGO_DISPATCH] {10 * (n + 1)} chip=0 task={task} core={core} cluster={cl}"
        for n, (task, core, cl) in enumerate(tasks))
    if parked:
        log += "\n[BINGO_RISK] 25 core=1 cluster=0 at risk"
    uart = (f"[Host] Bingo status: replay_stuck=0 park_fail=0x0 risk=0x{2 if parked else 0:x}\n"
            "Check [A_chain_cluster0]: PASS\nCheck [A_cluster1]: PASS\n")
    return log, uart


def full_fixture(parked):
    log, uart = fixture(parked)
    log += "\nAll chips finished successfully at 1000"
    log += "\n[BINGO_LATE] 15 core=1 cluster=0\n[BINGO_LATE] 25 core=1 cluster=0"
    if parked:
        log += ("\n[BINGO_PARK] 26 chip=0 core=1 cluster=0 HOLD"
                "\n[BINGO_PARK] 27 chip=0 core=1 cluster=0 PARKED -> core=1 cluster=1"
                "\n[BINGO_REMAP] 30 chip=0 task=2 logical_core=1 -> physical_core=1 cluster=1")
    else:
        log += ("\n[BINGO_LATE] 35 core=1 cluster=0"
                "\n[BINGO_WD] 41 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0"
                "\n[BINGO_WD] 42 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1"
                "\n[BINGO_REPLAY] 43 chip=0 task=3 type=2 logical_core=1 from=1 to=1 cluster=0 to_cluster=1"
                "\n[BINGO_RETIRED] 44 chip=0 core=1 cluster=0")
    uart += "Exit task of cluster 0 core 1 taken over\n"
    return log, uart


class RiskChainTests(unittest.TestCase):
    def check(self, parked, log=None, uart=None):
        original_log, original_uart = fixture(parked)
        return driver.evaluate_risk_chain(
            driver.SCENARIOS["s19" if parked else "s18"],
            original_log if log is None else log,
            original_uart if uart is None else uart)

    def test_baseline_and_parking(self):
        for parked in (False, True):
            with self.subTest(parked=parked):
                self.assertEqual(self.check(parked), [])

    def test_host_risk_is_required_and_exact(self):
        for parked in (False, True):
            log, uart = fixture(parked)
            for changed in (uart.replace("risk=", "other="),
                            uart.replace(f"risk=0x{2 if parked else 0:x}", "risk=0x4")):
                with self.subTest(parked=parked, uart=changed):
                    self.assertTrue(self.check(parked, uart=changed))

    def test_missing_duplicate_wrong_slot_or_chip(self):
        for parked in (False, True):
            log, _ = fixture(parked)
            for changed in (log.replace("task=2", "task=99"),
                            log + "\n[BINGO_DISPATCH] 100 chip=0 task=5 core=1 cluster=1",
                            log.replace("task=5 core=1 cluster=1", "task=5 core=1 cluster=0"),
                            log.replace("chip=0 task=0", "chip=1 task=0")):
                with self.subTest(parked=parked, log=changed):
                    self.assertTrue(self.check(parked, log=changed))

    def test_risk_must_trip_during_second_task(self):
        log, _ = fixture(True)
        for changed in (log.replace("RISK] 25", "RISK] 15"),
                        log.replace("RISK] 25", "RISK] 35"),
                        log.replace("[BINGO_RISK]", "[OTHER]")):
            with self.subTest(log=changed):
                self.assertTrue(self.check(True, log=changed))

    def test_both_goldens_are_required(self):
        for parked in (False, True):
            _, uart = fixture(parked)
            for changed in (uart.replace("Check [A_chain_cluster0]: PASS\n", ""),
                            uart.replace("Check [A_cluster1]: PASS\n", ""),
                            uart.replace(": PASS", ": FAIL"),
                            uart + "Check [A_cluster1]: PASS\n"):
                with self.subTest(parked=parked, uart=changed):
                    self.assertTrue(self.check(parked, uart=changed))

    def test_full_scenario_expectations(self):
        for parked in (False, True):
            name = "s19" if parked else "s18"
            log, uart = full_fixture(parked)
            with self.subTest(name=name):
                self.assertEqual(driver.evaluate(name, driver.SCENARIOS[name], log, uart), [])
                self.assertTrue(driver.evaluate(
                    name, driver.SCENARIOS[name], log.replace("LATE] 15", "OTHER] 15"), uart))
                self.assertTrue(driver.evaluate(
                    name, driver.SCENARIOS[name], log.replace("LATE] 15 core=1", "LATE] 15 core=0"), uart))
        log, uart = full_fixture(True)
        self.assertTrue(driver.evaluate(
            "s19", driver.SCENARIOS["s19"],
            log + "\n[BINGO_WD] 90 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1", uart))


if __name__ == "__main__":
    unittest.main()
