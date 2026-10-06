"""Fast checker coverage for automatic same-output host fallback (s34-s36)."""

import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

_path = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
_spec = importlib.util.spec_from_file_location("bingo_host_fallback_driver", _path)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)

# The compiler test pins these against the real workload's exported graph.
IDS = {"copy": 0, "check": 1, "backup": 2, "primary_exit": 5, "substitute_exit": 7}


def fixture(name, ids=None):
    ids = IDS if ids is None else ids
    double, faulty = name == "s34", name != "s36"
    lines = [f"[BINGO_DISPATCH] 20 chip=0 task={ids['copy']} core=1 cluster=0"]
    if faulty:
        lines += [
            "[BINGO_WD] 50 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
            "[BINGO_WD] 60 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1",
            f"[BINGO_REPLAY] 70 chip=0 task={ids['copy']} type=0 logical_core=1 "
            "from=1 to=1 cluster=0 to_cluster=1 logical_cluster=0",
            "[BINGO_RETIRED] 75 chip=0 core=1 cluster=0",
            f"[BINGO_DISPATCH] 80 chip=0 task={ids['copy']} core=1 cluster=1",
        ]
    if double:
        lines += [
            "[BINGO_WD] 100 chip=0 core=1 cluster=1 dead_suspect=1 fenced=0",
            "[BINGO_WD] 110 chip=0 core=1 cluster=1 dead_suspect=1 fenced=1",
            f"[BINGO_REPLAY_STUCK] 110 chip=0 core=1 cluster=1: no live core may run task {ids['copy']}",
            "[BINGO_CERF_FB] 120 type 2 clear g0 set g1",
            f"[BINGO_DISPATCH] 130 chip=0 task={ids['backup']} core=2 cluster=0",
        ]
    lines.append(f"[BINGO_DISPATCH] 140 chip=0 task={ids['check']} core=2 cluster=0")
    if not double:
        if faulty:
            # The victim's exit is routed away and retired by the manager
            lines += [
                f"[BINGO_EXIT_ABSORB] 145 chip=0 task={ids['primary_exit']} logical_core=1 "
                "logical_cluster=0 core=1 cluster=1",
                f"[BINGO_REMAP] 145 chip=0 task={ids['primary_exit']} logical_core=1 "
                "-> physical_core=1 cluster=1 logical_cluster=0",
            ]
        else:
            lines.append(f"[BINGO_DISPATCH] 150 chip=0 task={ids['primary_exit']} core=1 cluster=0")
        lines.append(f"[BINGO_DISPATCH] 160 chip=0 task={ids['substitute_exit']} core=1 cluster=1")
    fenced = 0x12 if double else 0x2 if faulty else 0
    lines += [
        f"[BINGO_STATUS] 180 chip=0 replay_stuck={int(double)} remote_done_mismatch=0 "
        f"link_error=0 fenced=0x{fenced:x} dead_suspect=0x{fenced:x} remote_timeout=0",
        f"{driver.SIM_OK_MARKER} at 200",
    ]
    uart = (
        f"[Host] Bingo status: replay_stuck={int(double)} remote_done_mismatch=0 link_error=0 "
        f"fenced=0x{fenced:x} cerf=0x{2 if double else 1:x} cerf_fb_en=0x4 "
        f"cerf_fb_evt=0x{4 if double else 0:x}\n"
        "[Host] Check [A_L1]: PASS\n"
        f"[DmaHostFallback] check complete; host fallback {int(double)}\n")
    return "\n".join(lines), uart


def drop(text, needle):
    return "\n".join(line for line in text.splitlines() if needle not in line)


class HostFallbackCheckerTests(unittest.TestCase):
    def evaluate(self, name, log=None, uart=None, ids=None):
        ids = IDS if ids is None else ids
        original_log, original_uart = fixture(name, ids)
        sc = {**driver.SCENARIOS[name], "host_fallback_task_ids": ids}
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.evaluate(name, sc, original_log if log is None else log,
                                   original_uart if uart is None else uart)

    def test_scenarios_and_reserved_numbers(self):
        for name in ("s34", "s35", "s36"):
            sc = driver.SCENARIOS[name]
            self.assertEqual(sc["workload"], "dma_host_fallback_2cluster")
            self.assertEqual(sc["extra_cfg"], driver.L2_CFG_KEYS)
            self.assertTrue(sc["host_fallback"])
            self.assertNotIn("BINGO_CERF_FB_", sc["extra_flags"])
        self.assertTrue(driver.SCENARIOS["s34"]["fault_any_core"])
        self.assertNotIn("fault_any_core", driver.SCENARIOS["s35"])
        self.assertIsNone(driver.SCENARIOS["s36"]["fault_stall_cycles"])
        self.assertTrue(all(f"s{i}" not in driver.SCENARIOS for i in range(30, 34)))

    def test_all_fixtures_pass(self):
        for name in ("s34", "s35", "s36"):
            with self.subTest(scenario=name):
                self.assertEqual(self.evaluate(name), [])

    def test_checker_uses_supplied_graph_ids_not_old_dma_ids(self):
        ids = {name: value + 20 for name, value in IDS.items()}
        for name in ("s34", "s35", "s36"):
            sc = {**driver.SCENARIOS[name], "fault_gid": ids["copy"],
                  "host_fallback_task_ids": ids}
            log, uart = fixture(name, ids)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(driver.evaluate(name, sc, log, uart), [])

    def test_csv_parsing_requires_unique_kernels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.csv"
            text = ("ID,Chiplet,Cluster,Core,Type,Kernel\n"
                    "0,00,0,1,normal,__snax_bingo_kernel_idma_1d_copy\n"
                    "1,00,0,2,normal,__host_bingo_kernel_check_result\n"
                    "2,00,0,2,normal,__host_bingo_kernel_idma\n"
                    "5,00,0,1,normal,__snax_bingo_kernel_exit\n"
                    "7,00,1,1,normal,__snax_bingo_kernel_exit\n")
            path.write_text(text)
            self.assertEqual(driver.host_fallback_task_ids(path), IDS)
            path.write_text(text + "9,00,0,1,normal,__snax_bingo_kernel_idma_1d_copy\n")
            with self.assertRaisesRegex(ValueError, "one copy"):
                driver.host_fallback_task_ids(path)

    def test_s34_mutations_rejected(self):
        log, uart = fixture("s34")
        mutations = {
            "no CERF": (drop(log, "[BINGO_CERF_FB]"), uart),
            "two CERF": (log + "\n[BINGO_CERF_FB] 125 type 2 clear g0 set g1", uart),
            "wrong type": (log.replace("type 2 clear", "type 1 clear"), uart),
            "wrong group": (log.replace("set g1", "set g0"), uart),
            "no stuck": (drop(log, "[BINGO_REPLAY_STUCK]"), uart),
            "no replay": (drop(log, "[BINGO_REPLAY]"), uart),
            "no substitute fence": (drop(log, "cluster=1 dead_suspect=1 fenced=1"), uart),
            "no backup": (drop(log, "task=2 core=2"), uart),
            "backup wrong slot": (log.replace("task=2 core=2 cluster=0", "task=2 core=1 cluster=0"), uart),
            "backup before CERF": (log.replace("130 chip=0 task=2", "115 chip=0 task=2"), uart),
            "check before backup": (log.replace("140 chip=0 task=1", "125 chip=0 task=1"), uart),
            "primary exit runs": (log + "\n[BINGO_DISPATCH] 145 chip=0 task=5 core=1 cluster=1", uart),
            "substitute exit runs": (log + "\n[BINGO_DISPATCH] 145 chip=0 task=7 core=1 cluster=1", uart),
            "event bit missing": (log, uart.replace("cerf_fb_evt=0x4", "cerf_fb_evt=0x0")),
            "wrong output": (log, uart.replace("A_L1", "A_L1_1")),
            "bad golden": (log, uart.replace("PASS", "FAIL")),
            "missing marker": (log, drop(uart, "[DmaHostFallback]")),
        }
        for label, (bad_log, bad_uart) in mutations.items():
            with self.subTest(mutation=label):
                self.assertNotEqual(self.evaluate("s34", bad_log, bad_uart), [])

    def test_s35_and_s36_mutations_rejected(self):
        for name in ("s35", "s36"):
            log, uart = fixture(name)
            mutations = {
                "CERF": (log + "\n[BINGO_CERF_FB] 120 type 2 clear g0 set g1", uart),
                "backup runs": (log + "\n[BINGO_DISPATCH] 135 chip=0 task=2 core=2 cluster=0", uart),
                "no check": (drop(log, "task=1 core=2"), uart),
                "wrong event": (log, uart.replace("cerf_fb_evt=0x0", "cerf_fb_evt=0x4")),
                "no EOC": (drop(log, driver.SIM_OK_MARKER), uart),
                "unexpected WD": (log + "\n[BINGO_WD] 100 chip=0 core=0 cluster=1 dead_suspect=1 fenced=0", uart),
            }
            if name == "s35":
                mutations["no replay"] = (drop(log, "[BINGO_REPLAY]"), uart)
                mutations["no absorb"] = (drop(log, "[BINGO_EXIT_ABSORB]"), uart)
                mutations["primary exit runs"] = (
                    log + "\n[BINGO_DISPATCH] 150 chip=0 task=5 core=1 cluster=1", uart)
                mutations["takeover"] = (
                    log, uart + "[Cluster 1 Core 1]: Exit task of cluster 0 core 1 taken over, "
                    "not exiting (chip 0)\n")
            else:
                mutations["unexpected replay"] = (
                    log + "\n[BINGO_REPLAY] 70 chip=0 task=0 type=0 logical_core=1 "
                    "from=1 to=1 cluster=0 to_cluster=1 logical_cluster=0", uart)
            for label, (bad_log, bad_uart) in mutations.items():
                with self.subTest(scenario=name, mutation=label):
                    self.assertNotEqual(self.evaluate(name, bad_log, bad_uart), [])


if __name__ == "__main__":
    unittest.main()
