"""Behavioral checks for A2 latency/cause and the healthy progress window."""
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "watchdog_risk_confirm", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


def fault_log(r):
    tick = 28000
    last = 1000000000
    suspect = last + 100001*tick
    fence = last + ((r or 200000)+2)*tick
    text = (f"[BINGO_WD] {suspect} chip=0 core=1 cluster=0 dead_suspect=1 fenced=0\n"
            f"[BINGO_WD] {fence} chip=0 core=1 cluster=0 dead_suspect=1 fenced=1\n")
    if r:
        text += f"[BINGO_RISK_CONFIRM] {fence-tick} core=1 cluster=0 threshold={r}\n"
    return text


def healthy_log(interval=111000):
    text = ""
    for task in range(6):
        dispatch = 1000000000 + task*4000000000
        text += f"[BINGO_DISPATCH] {dispatch} chip=0 task={task} core=1 cluster=0\n"
        text += f"[BINGO_LATE] {dispatch+interval*28000} core=1 cluster=0\n"
    return text


class RiskConfirmTests(unittest.TestCase):
    def test_disabled_and_shorter_confirm(self):
        for name, r in (("s40", 0), ("s41", 125000)):
            self.assertEqual(watchdog.evaluate_risk_confirm(watchdog.SCENARIOS[name], fault_log(r)), [])

    def test_wrong_latency_and_cause(self):
        sc = watchdog.SCENARIOS["s41"]
        self.assertTrue(watchdog.evaluate_risk_confirm(sc, fault_log(0)))
        self.assertTrue(watchdog.evaluate_risk_confirm(sc, fault_log(125000).replace(
            "threshold=125000", "threshold=100000")))
        self.assertTrue(watchdog.evaluate_risk_confirm(sc, fault_log(125000) + fault_log(125000)))
        self.assertTrue(watchdog.evaluate_risk_confirm(watchdog.SCENARIOS["s40"], fault_log(125000)))

    def test_healthy_window(self):
        self.assertEqual(watchdog.evaluate_risk_confirm(
            watchdog.SCENARIOS["s42"], healthy_log()), [])
        for interval in (100000, 104999, 118751, 125000):
            self.assertTrue(watchdog.evaluate_risk_confirm(
                watchdog.SCENARIOS["s42"], healthy_log(interval)))

    def test_missing_progress_and_false_replay(self):
        sc = watchdog.SCENARIOS["s42"]
        self.assertTrue(watchdog.evaluate_risk_confirm(sc, healthy_log().replace(
            "task=5", "task=6")))
        self.assertTrue(watchdog.evaluate_risk_confirm(sc, healthy_log() + "[BINGO_REPLAY]"))
        self.assertTrue(watchdog.evaluate_risk_confirm(sc, healthy_log() + fault_log(125000)))

    def test_risk_without_parking_keeps_fault_chain_expectations(self):
        sc = copy.deepcopy(watchdog.SCENARIOS["s40"])
        log = "[BINGO_RISK] 250 core=1 cluster=0 at risk\n"
        for i, (task, cluster) in enumerate(((0, 0), (1, 0), (2, 0), (3, 0),
                                           (3, 1), (4, 1), (5, 1))):
            log += f"[BINGO_DISPATCH] {100+i*100} chip=0 task={task} core=1 cluster={cluster}\n"
        uart = ("[Host] Bingo status: busy=0x0 risk=0x0\n"
                "Check [A_chain_cluster0]: PASS\nCheck [A_cluster1]: PASS\n")
        self.assertEqual(watchdog.evaluate_risk_chain(sc, log, uart), [])


if __name__ == "__main__":
    unittest.main()
