import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from bingo_evlog import check_evlog
from bingo_p8 import (PERIOD_PS, access_windows, check_access_level, check_recovery_hold, control_streams,
                      recovery_hold_config, recovery_window)

spec = importlib.util.spec_from_file_location(
    "p8_driver", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class RecoveryPowerTests(unittest.TestCase):
    def fixture(self):
        log = "\n".join([
            "[BINGO_PM] 280000 domain=2 level=25",
            "[BINGO_WD] 308000 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
            "[BINGO_WD] 364000 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1",
            "[BINGO_RECOVERY_HOLD] 392000 core=1 cluster=1 active=1",
            "[BINGO_PM] 532000 domain=2 level=6",
            "[BINGO_DISPATCH] 560000 chip=0 task=13 core=1 cluster=1",
            "[BINGO_RECOVERY_HOLD] 672000 core=1 cluster=1 active=0",
        ])
        uart = "\n".join([
            "[EVLOG] ts=10 code=0x01 slot=0:1 arg=0x0000",
            "[EVLOG] ts=12 code=0x03 slot=0:1 arg=0x0000",
            "[EVLOG] ts=13 code=0x0c slot=1:1 arg=0x0001",
            "[EVLOG] ts=23 code=0x0c slot=1:1 arg=0x0000",
            "[EVLOG] count=4 dropped=0",
        ])
        return log, uart

    def test_cold_hot_and_exact_events(self):
        log, uart = self.fixture()
        self.assertEqual(check_recovery_hold(log, uart, 10), [])
        self.assertEqual(check_evlog(log, uart, 3), [])
        self.assertEqual(recovery_window(log, 10)["inherited_wake_cycles"], 5)
        hot = log.replace("280000 domain=2 level=25", "280000 domain=2 level=6")
        self.assertFalse(recovery_window(hot, 10)["cold"])
        self.assertEqual(check_recovery_hold("", "", 0), [])

    def test_recovery_negative_mutations(self):
        log, uart = self.fixture()
        for changed in (
            log.replace("532000 domain=2 level=6", "532000 domain=2 level=25"),
            log.replace("532000 domain=2 level=6", "700000 domain=2 level=6"),
            log.replace("672000 core=1", "700000 core=1"),
            log.replace("392000 core=1 cluster=1", "392000 core=1 cluster=0"),
            log + "\n[BINGO_PM] 588000 domain=2 level=25",
            log.replace("392000 core=1", "420000 core=1"),
        ):
            self.assertTrue(check_recovery_hold(changed, uart, 10))
        self.assertTrue(check_recovery_hold(log, "", 10))
        self.assertTrue(check_recovery_hold(log, uart, 0))
        self.assertTrue(check_evlog(log, uart.replace("ts=13", "ts=14"), 3))

    def test_hold_selection_rounds_up(self):
        log, _ = self.fixture()
        selected = recovery_hold_config(log)
        self.assertEqual(selected["dispatch_span_cycles"], 7)
        self.assertEqual(selected["W_cycles"], 8)
        with self.assertRaises(ValueError):
            recovery_hold_config(log.replace("560000", "560001"))

    def test_configuration_fields_preserve_old_bytes(self):
        for version in (1, 2):
            cfg = driver.test_cfg_defaults(version)
            before = driver.test_cfg_bytes(cfg)
            after = driver.test_cfg_bytes(dict(cfg, recovery_hold=123, pm_access_level=12))
            self.assertEqual(struct.unpack_from("<2I", after, 112), (123, 12))
            self.assertEqual(before[:112], after[:112])
            self.assertEqual(before[120:], after[120:])
            for field in ("recovery_hold", "pm_access_level"):
                for bad in (-1, 0x100000000, "12"):
                    with self.assertRaises(ValueError):
                        driver.test_cfg_bytes(dict(cfg, **{field: bad}))
        cfg = driver.scenario_test_cfg(driver.SCENARIOS["t64"],
                                       "-DBINGO_RECOVERY_HOLD=123 -DBINGO_PM_ACCESS_LEVEL=12")
        self.assertEqual((cfg["recovery_hold"], cfg["pm_access_level"]), (123, 12))
        for name, hold, level in (("t65a", 1000, 0), ("t65s", 1000, 12), ("t65o", 0, 0)):
            cfg = driver.scenario_test_cfg(driver.SCENARIOS[name])
            self.assertEqual((cfg["pm_access_wake_hold"], cfg["pm_access_level"]), (hold, level))
        a, b = [driver.scenario_test_cfg(driver.SCENARIOS[n]) for n in ("t64", "t64h")]
        driver.check_same_ps_fault_cfg(a, b)

    def access_check(self, rows, level):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.csv"
            path.write_text("ID,Kernel\n7,__host_bingo_kernel_check_result\n"
                            "8,__host_bingo_kernel_check_result\n")
            log = "\n".join(f"[BINGO_{kind}] {cycle * PERIOD_PS} {tail}"
                            for kind, cycle, tail in sorted(rows, key=lambda row: row[1]))
            return check_access_level(log, level, path)

    def access_fixture(self, level):
        return [
            ("PM", 1, "domain=1 level=25"),
            ("PM", 1, "domain=2 level=25"),
            ("DISPATCH", 10, "chip=0 task=7 core=2 cluster=0"),
            ("PM", 12, f"domain=1 level={level}"),
            ("PM", 25, "domain=1 level=25"),
            ("DONE", 30, "chip=0 task=7 core=2 cluster=0"),
            ("DISPATCH", 40, "chip=0 task=8 core=2 cluster=0"),
            ("PM", 42, f"domain=2 level={level}"),
            ("PM", 55, "domain=2 level=25"),
            ("DONE", 60, "chip=0 task=8 core=2 cluster=0"),
        ]

    def test_access_servo_positive(self):
        self.assertEqual(self.access_check(self.access_fixture(12), 12), [])

    def test_access_normal_positive(self):
        self.assertEqual(self.access_check(self.access_fixture(6), 6), [])

    def test_access_off_positive(self):
        self.assertEqual(self.access_check(self.access_fixture(25), 25), [])

    def test_access_nonbusy_normal_is_not_servo(self):
        rows = self.access_fixture(12) + [("PM", 11, "domain=1 level=6")]
        self.assertTrue(self.access_check(rows, 12))

    def test_access_servo_cannot_change_to_normal_mid_segment(self):
        rows = self.access_fixture(12) + [("PM", 18, "domain=1 level=6")]
        self.assertTrue(self.access_check(rows, 12))

    def test_access_servo_must_appear_in_each_window(self):
        self.assertTrue(self.access_check(self.access_fixture(25), 12))
        rows = self.access_fixture(12)
        rows[2:6] = self.access_fixture(25)[2:6]
        self.assertTrue(self.access_check(rows, 12))

    def test_access_busy_normal_is_not_a_violation(self):
        rows = self.access_fixture(12) + [
            ("DISPATCH", 2, "chip=0 task=0 core=1 cluster=0"),
            ("PM", 3, "domain=1 level=6"),
            ("DONE", 11, "chip=0 task=0 core=1 cluster=0"),
        ]
        self.assertEqual(self.access_check(rows, 12), [])

    def test_access_wake_allowance_starts_before_host_dispatch(self):
        rows = self.access_fixture(12) + [
            ("DISPATCH", 2, "chip=0 task=0 core=1 cluster=0"),
            ("PM", 3, "domain=1 level=6"),
            ("DONE", 8, "chip=0 task=0 core=1 cluster=0"),
        ]
        rows[3] = ("PM", 18, "domain=1 level=12")
        self.assertEqual(self.access_check(rows, 12), [])
        rows[3] = ("PM", 19, "domain=1 level=12")
        self.assertTrue(self.access_check(rows, 12))

    def test_access_each_busy_to_idle_transition_gets_an_allowance(self):
        rows = self.access_fixture(12) + [
            ("DISPATCH", 13, "chip=0 task=0 core=1 cluster=0"),
            ("PM", 14, "domain=1 level=6"),
            ("DONE", 15, "chip=0 task=0 core=1 cluster=0"),
            ("DISPATCH", 18, "chip=0 task=1 core=0 cluster=0"),
            ("DONE", 19, "chip=0 task=1 core=0 cluster=0"),
        ]
        rows[4] = ("PM", 29, "domain=1 level=25")
        self.assertEqual(self.access_check(rows, 12), [])
        rows[4] = ("PM", 30, "domain=1 level=25")
        self.assertTrue(self.access_check(rows, 12))

    def test_access_any_busy_slot_in_domain_excludes_the_segment(self):
        rows = self.access_fixture(12) + [
            ("DISPATCH", 2, "chip=0 task=0 core=1 cluster=0"),
            ("DISPATCH", 3, "chip=0 task=1 core=0 cluster=0"),
            ("PM", 4, "domain=1 level=6"),
            ("DONE", 5, "chip=0 task=0 core=1 cluster=0"),
            ("DONE", 11, "chip=0 task=1 core=0 cluster=0"),
        ]
        self.assertEqual(self.access_check(rows, 12), [])

    def test_access_other_domain_busy_does_not_exempt_normal(self):
        rows = self.access_fixture(12) + [
            ("DISPATCH", 2, "chip=0 task=0 core=1 cluster=1"),
            ("PM", 3, "domain=1 level=6"),
            ("DONE", 20, "chip=0 task=0 core=1 cluster=1"),
        ]
        self.assertTrue(self.access_check(rows, 12))

    def test_access_normal_and_off_reject_wrong_levels(self):
        self.assertTrue(self.access_check(self.access_fixture(12), 6))
        self.assertTrue(self.access_check(self.access_fixture(6), 25))
        self.assertTrue(self.access_check(self.access_fixture(25), 6))

    def test_access_keeps_done_endpoint_and_requires_observations(self):
        rows = self.access_fixture(12) + [("PM", 30, "domain=1 level=6")]
        self.assertTrue(self.access_check(rows, 12))
        self.assertTrue(self.access_check(self.access_fixture(12)[1:], 12))
        self.assertTrue(self.access_check(self.access_fixture(12)[:-1], 12))
        rows = self.access_fixture(12) + [("DONE", 11, "chip=0 task=0 core=1 cluster=0")]
        self.assertTrue(self.access_check(rows, 12))

    def test_access_segments_keep_complete_host_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.csv"
            path.write_text("ID,Kernel\n7,__host_bingo_kernel_check_result\n"
                            "8,__host_bingo_kernel_check_result\n")
            rows = self.access_fixture(12)
            log = "\n".join(f"[BINGO_{kind}] {cycle * PERIOD_PS} {tail}"
                            for kind, cycle, tail in sorted(rows, key=lambda row: row[1]))
            windows = access_windows(log, path)
            self.assertEqual([window["duration_ps"] for window in windows], [20 * PERIOD_PS] * 2)
            for window in windows:
                self.assertEqual([segment["level"] for segment in window["segments"]], [25, 12, 25])
                self.assertEqual(sum(segment["end_ps"] - segment["start_ps"]
                                     for segment in window["segments"]), window["duration_ps"])

    def test_per_stream_control_gate_keeps_all_non_pm_events(self):
        log, _ = self.fixture()
        changed = log.replace("560000", "588000").replace("level=25", "level=6")
        self.assertEqual(control_streams(log), control_streams(changed))
        self.assertNotEqual(control_streams(log), control_streams(changed.replace("task=13", "task=14")))

    def test_native_api_preserves_full_width_values(self):
        root = Path(__file__).resolve().parents[4]
        api = (root / "target/sw/host/runtime/libbingo/src/bingo_api.c").read_text()
        functions = api[api.index("void bingo_pm_set_recovery_hold("):api.index("void bingo_evlog_enable(")]
        source = r'''
#include <stdint.h>
#include <assert.h>
static uint32_t regs[2];
static uintptr_t chiplet_addr_transform(uintptr_t a) { return a; }
static uintptr_t quad_ctrl_bingo_recovery_hold_addr(void) { return 0; }
static uintptr_t quad_ctrl_bingo_pm_access_level_addr(void) { return 1; }
static void writew(uint32_t v, uintptr_t a) { regs[a] = v; }
'''
        source += functions + r'''
int main(void) {
    bingo_pm_set_recovery_hold(UINT32_MAX); assert(regs[0] == UINT32_MAX);
    bingo_pm_set_access_level(0x1000000c); assert(regs[1] == 0x1000000c);
    bingo_pm_set_recovery_hold(0); bingo_pm_set_access_level(0);
    assert(regs[0] == 0 && regs[1] == 0); return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "api.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(path / "api.c"), "-o", str(path / "api")], check=True)
            subprocess.run([str(path / "api")], check=True)
