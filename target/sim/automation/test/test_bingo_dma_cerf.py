"""Fast full-checker coverage for the DM-core double death (s28) and its control (s29)."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest

_path = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
_spec = importlib.util.spec_from_file_location("bingo_dma_cerf_watchdog_driver", _path)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)


def fixture(double):
    """Logs as the RTL / host print them; times are arbitrary but ordered."""
    lines = [
        "[BINGO_DISPATCH] 10 chip=0 task=0 core=2 cluster=0",
        "[BINGO_DISPATCH] 20 chip=0 task=1 core=1 cluster=0",
        "[BINGO_DISPATCH] 25 chip=0 task=6 core=0 cluster=0",
        "[BINGO_DISPATCH] 26 chip=0 task=8 core=0 cluster=1",
        "[BINGO_WD] 50 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
        "[BINGO_WD] 60 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1",
        "[BINGO_REPLAY] 70 chip=0 task=1 type=0 logical_core=1 from=1 to=1 cluster=0 "
        "to_cluster=1 logical_cluster=0",
        "[BINGO_RETIRED] 75 chip=0 core=1 cluster=0",
        "[BINGO_DISPATCH] 80 chip=0 task=1 core=1 cluster=1",
    ]
    if double:
        lines += [
            "[BINGO_WD] 100 chip=0 core=1 cluster=1 dead_suspect=1 fenced=0",
            "[BINGO_WD] 110 chip=0 core=1 cluster=1 dead_suspect=1 fenced=1",
            "[BINGO_REPLAY_STUCK] 110 chip=0 core=1 cluster=1: no live core may run task 1 "
            "(logical core 1)",
            "[BINGO_CERF_FB] 120 type 2 clear g0 set g1",
            "[BINGO_DISPATCH] 130 chip=0 task=3 core=2 cluster=0",
            "[BINGO_DISPATCH] 140 chip=0 task=4 core=2 cluster=0",
            "[BINGO_STATUS] 150 chip=0 replay_stuck=1 remote_done_mismatch=0 "
            "link_error=0 fenced=0x12 dead_suspect=0x12 remote_timeout=0",
        ]
    else:
        lines += [
            "[BINGO_REMAP] 90 chip=0 task=2 logical_core=1 -> physical_core=1 cluster=1 "
            "logical_cluster=0",
            "[BINGO_DISPATCH] 100 chip=0 task=2 core=1 cluster=1",
            "[BINGO_DISPATCH] 110 chip=0 task=4 core=2 cluster=0",
            # The victim's exit is routed away and retired by the manager
            "[BINGO_EXIT_ABSORB] 115 chip=0 task=7 logical_core=1 logical_cluster=0 core=1 cluster=1",
            "[BINGO_REMAP] 115 chip=0 task=7 logical_core=1 -> physical_core=1 cluster=1 "
            "logical_cluster=0",
            "[BINGO_DISPATCH] 130 chip=0 task=9 core=1 cluster=1",
            "[BINGO_STATUS] 150 chip=0 replay_stuck=0 remote_done_mismatch=0 "
            "link_error=0 fenced=0x2 dead_suspect=0x2 remote_timeout=0",
        ]
    lines += ["[BINGO_DISPATCH] 160 chip=0 task=10 core=2 cluster=0",
              f"{driver.SIM_OK_MARKER} at 200"]
    branch = int(double)
    uart = (
        f"[Host] Bingo status: replay_stuck={branch} remote_done_mismatch=0 link_error=0 "
        f"fenced=0x{0x12 if double else 0x2:x} cerf=0x{1 << branch:x} cerf_fb_en=0x4 "
        f"cerf_fb_evt=0x{4 if double else 0:x}\n"
        f"[DmaCERF] gating selected g0; join complete; output branch {branch}\n"
        f"[Host] Check [A_branch_{branch}]: PASS\n")
    return "\n".join(lines), uart


def drop(text, needle):
    return "\n".join(line for line in text.splitlines() if needle not in line)


class DmaCerfCheckerTests(unittest.TestCase):
    def evaluate(self, name, log=None, uart=None):
        original_log, original_uart = fixture(name == "s28")
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.evaluate(name, driver.SCENARIOS[name],
                                   original_log if log is None else log,
                                   original_uart if uart is None else uart)

    def test_scenarios(self):
        s28, s29 = driver.SCENARIOS["s28"], driver.SCENARIOS["s29"]
        for sc in (s28, s29):
            self.assertEqual(sc["workload"], "dma_cerf_2cluster")
            self.assertEqual(sc["extra_cfg"], driver.L2_CFG_KEYS)
            self.assertEqual(sc["fault_gid"], 1)
            self.assertEqual(sc["fault_stall_cycles"], 0)
            self.assertEqual((sc["victim"], sc["substitute"], sc["substitute_cluster"]),
                             ((0, 1, 0), 1, 1))
            self.assertNotIn("BINGO_CERF_FB_", sc["extra_flags"])
        self.assertTrue(s28["fault_any_core"])
        self.assertNotIn("fault_any_core", s29)
        self.assertIn("-DBINGO_DMA_EXPECT_BRANCH=1", s28["extra_flags"])
        self.assertIn("-DBINGO_DMA_EXPECT_BRANCH=0", s29["extra_flags"])

    def test_fixtures_pass(self):
        for name in ("s28", "s29"):
            with self.subTest(scenario=name):
                self.assertEqual(self.evaluate(name), [])

    def test_s28_mutations_are_rejected(self):
        log, uart = fixture(True)
        mutations = {
            "no fallback": (drop(log, "[BINGO_CERF_FB]"), uart),
            "wrong type": (log.replace("type 2 clear", "type 1 clear"), uart),
            "fallback before the substitute fence": (
                log.replace("[BINGO_CERF_FB] 120", "[BINGO_CERF_FB] 105"), uart),
            "host copy before the fallback": (
                log.replace("130 chip=0 task=3", "115 chip=0 task=3"), uart),
            "copy 1 dispatched": (log + "\n[BINGO_DISPATCH] 135 chip=0 task=2 core=1 cluster=1", uart),
            "substitute exit dispatched": (
                log + "\n[BINGO_DISPATCH] 145 chip=0 task=9 core=1 cluster=1", uart),
            "substitute never fenced": (drop(log, "cluster=1 dead_suspect=1 fenced=1"), uart),
            "no stuck report": (drop(log, "[BINGO_REPLAY_STUCK]"), uart),
            "no replay": (drop(log, "[BINGO_REPLAY]"), uart),
            "third core suspect": (
                log + "\n[BINGO_WD] 105 chip=0 core=0 cluster=1 dead_suspect=1 fenced=0", uart),
            "no event bit": (log, uart.replace("cerf_fb_evt=0x4", "cerf_fb_evt=0x0")),
            "primary output checked": (log, uart.replace("A_branch_1", "A_branch_0")),
            "backup output fails": (log, uart.replace("]: PASS", "]: FAIL")),
            "no marker": (log, drop(uart, "[DmaCERF]")),
        }
        for label, (bad_log, bad_uart) in mutations.items():
            with self.subTest(mutation=label):
                self.assertNotEqual(self.evaluate("s28", bad_log, bad_uart), [])

    def test_s29_mutations_are_rejected(self):
        log, uart = fixture(False)
        mutations = {
            "fallback": (log + "\n[BINGO_CERF_FB] 120 type 2 clear g0 set g1", uart),
            "host copy dispatched": (log + "\n[BINGO_DISPATCH] 135 chip=0 task=3 core=2 cluster=0", uart),
            "copy 1 stays home": (log.replace("100 chip=0 task=2 core=1 cluster=1",
                                              "100 chip=0 task=2 core=1 cluster=0"), uart),
            "substitute suspect": (
                log + "\n[BINGO_WD] 105 chip=0 core=1 cluster=1 dead_suspect=1 fenced=0", uart),
            "stuck": (log + "\n[BINGO_REPLAY_STUCK] 110 chip=0 core=1 cluster=1: no live core", uart),
            "no absorb": (drop(log, "[BINGO_EXIT_ABSORB]"), uart),
            "absorbed twice": (log + "\n[BINGO_EXIT_ABSORB] 125 chip=0 task=7 logical_core=1 "
                               "logical_cluster=0 core=1 cluster=1", uart),
            "takeover": (log, uart + "[Cluster 1 Core 1]: Exit task of cluster 0 core 1 taken over, "
                         "not exiting (chip 0)\n"),
            "backup output checked": (log, uart.replace("A_branch_0", "A_branch_1")),
        }
        for label, (bad_log, bad_uart) in mutations.items():
            with self.subTest(mutation=label):
                self.assertNotEqual(self.evaluate("s29", bad_log, bad_uart), [])


if __name__ == "__main__":
    unittest.main()
