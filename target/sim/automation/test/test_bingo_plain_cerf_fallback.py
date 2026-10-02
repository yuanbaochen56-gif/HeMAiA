"""Fast full-checker coverage for L3 reject -> CERF fallback and its control."""

import contextlib
import importlib.util
import io
from pathlib import Path
import unittest

_path = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
_spec = importlib.util.spec_from_file_location("bingo_plain_watchdog_driver", _path)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)


def fixture(fault):
    tasks = [(10, 0, 4), (20, 1, 3), (30, 2, 3), (40, 3, 1)]
    if fault:
        tasks.append((150, 4, 2))
    tasks.append((160, 5, 4))
    if not fault:
        tasks.append((170, 8, 1))
    log = "\n".join(f"[BINGO_DISPATCH] {t} chip=0 task={task} core={core} cluster=0"
                    for t, task, core in tasks)
    if fault:
        log += (
            "\n[BINGO_WD] 50 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0"
            "\n[BINGO_WD] 60 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1"
            "\n[BINGO_REPLAY] 70 chip=0 task=3 type=0 logical_core=1 from=1 to=1 cluster=0 to_cluster=0"
            "\n[BINGO_EXPORT] 80 chip=0 task=3 type_id=2 proxy_slot=1"
            "\n[BINGO_REMOTE_REJECT_OUT] 100 chip=0 task=3 -> chip=0 proxy_slot=1"
            "\n[BINGO_REMOTE_REJECT_IN] 110 chip=0 task=3 proxy_slot=1: the proxy is stuck"
            "\n[BINGO_CERF_FB] 120 type 2 clear g0 set g1"
            "\n[BINGO_STATUS] 130 chip=0 replay_stuck=1 remote_done_mismatch=0 "
            "link_error=0 remote_timeout=0 fenced=0x2")
        dispatch = (0x5 << 60) | (1 << 44) | (2 << 40) | 3
        reject = (0xC << 60) | (1 << 44) | 3
        for t, address, word in ((90, driver.REMOTE_LINK_BASE, dispatch),
                                 (105, driver.REMOTE_LINK_BASE + 0x1000, reject)):
            for direction in ("TX", "RX"):
                log += (f"\n[BINGO_RLINK_{direction}_AW] {t} chip=0 addr=0x{address:x}"
                        f"\n[BINGO_RLINK_{direction}_W] {t} chip=0 data=0x{word:x}")
            log += f"\n[BINGO_RLINK_TX_B] {t} chip=0 resp=0"
    log += f"\n{driver.SIM_OK_MARKER} at 200"
    branch = int(fault)
    uart = (
        f"[Host] Bingo status: replay_stuck={branch} remote_done_mismatch=0 link_error=0 "
        f"fenced=0x{2 if fault else 0:x} cerf=0x{1 << branch:x} cerf_fb_en=0x4 "
        f"cerf_fb_evt=0x{4 if fault else 0:x}\n"
        f"[Int32AddCERF] gating selected g0; join complete; output branch {branch}\n"
        f"[Host] Check [C_branch_{branch}]: PASS\n")
    return log, uart


class PlainCerfFallbackCheckerTests(unittest.TestCase):
    def check_fixture(self, fault, log=None, uart=None):
        original_log, original_uart = fixture(fault)
        name = "s26" if fault else "s27"
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.evaluate(name, driver.SCENARIOS[name],
                                   original_log if log is None else log,
                                   original_uart if uart is None else uart)

    def test_scenarios_keep_s13_configuration_and_compiler_tables(self):
        for name in ("s26", "s27"):
            with self.subTest(scenario=name):
                sc = driver.SCENARIOS[name]
                self.assertEqual(sc["workload"], "int32_add_2plain_cerf_1cluster")
                self.assertEqual(sc["extra_cfg"], driver.SCENARIOS["s13"]["extra_cfg"])
                self.assertEqual(sc["cluster_swap"], driver.TWO_PLAIN_CLUSTER_SWAP)
                self.assertEqual(sc["expect_core_types"], driver.TWO_PLAIN_CORE_TYPES)
                self.assertTrue(sc["expect_eoc"])
                self.assertLess(sc["sim_timeout_s"], 1800)
                self.assertNotIn("BINGO_CERF_FB_", sc["extra_flags"])
                self.assertIn(f"-DBINGO_ADD_EXPECT_BRANCH={int(name == 's26')}",
                              sc["extra_flags"])
        self.assertEqual(driver.SCENARIOS["s26"]["fault_gid"], 3)
        self.assertEqual(driver.SCENARIOS["s26"]["fault_stall_cycles"], 0)
        self.assertIsNone(driver.SCENARIOS["s27"]["fault_stall_cycles"])
        self.assertIn("-DBINGO_BOOST_POLICY=0x101",
                      driver.SCENARIOS["s25"]["extra_flags"].split())

    def test_fault_and_control_pass_full_evaluation(self):
        for fault in (True, False):
            with self.subTest(fault=fault):
                self.assertEqual(self.check_fixture(fault), [])

    def test_reject_and_fallback_must_be_present_once_and_ordered(self):
        log, _ = fixture(True)
        for changed in (
            log.replace("[BINGO_REMOTE_REJECT_IN]", "[OTHER]"),
            log.replace("REJECT_IN] 110", "REJECT_IN] 125"),
            log.replace("[BINGO_CERF_FB]", "[OTHER]"),
            log.replace("type 2 clear g0 set g1", "type 1 clear g0 set g1"),
            log.replace("clear g0 set g1", "clear g1 set g0"),
            log + "\n[BINGO_CERF_FB] 121 type 2 clear g0 set g1",
            log.replace("[BINGO_EXPORT]", "[OTHER]"),
        ):
            with self.subTest(log=changed):
                self.assertTrue(self.check_fixture(True, log=changed))

    def test_backup_and_join_must_run_once_in_order_on_correct_slots(self):
        log, _ = fixture(True)
        for changed in (
            log.replace("task=4 core=2", "task=4 core=1"),
            log.replace("task=4 core=2 cluster=0", "task=4 core=2 cluster=1"),
            log.replace("task=4", "task=99"),
            log.replace("task=5", "task=99"),
            log.replace("DISPATCH] 150", "DISPATCH] 115"),
            log.replace("DISPATCH] 160", "DISPATCH] 145"),
            log + "\n[BINGO_DISPATCH] 155 chip=0 task=4 core=2 cluster=0",
            log + "\n[BINGO_DISPATCH] 175 chip=0 task=8 core=1 cluster=0",
        ):
            with self.subTest(log=changed):
                self.assertTrue(self.check_fixture(True, log=changed))

    def test_proxy_must_drain_without_reexport_local_takeover_or_link_error(self):
        log, _ = fixture(True)
        for changed in (
            log + "\n[BINGO_EXPORT] 180 chip=0 task=8 type_id=2 proxy_slot=1",
            log.replace("from=1 to=1", "from=1 to=2"),
            log + "\n[BINGO_REMAP] 180 chip=0 task=8 logical_core=1 -> physical_core=2 cluster=0",
            log.replace("B] 105 chip=0 resp=0", "B] 105 chip=0 resp=2"),
            log.replace("[BINGO_RLINK_RX_W]", "[OTHER]"),
            log.replace("link_error=0", "link_error=1"),
            log.replace("replay_stuck=1", "replay_stuck=0"),
        ):
            with self.subTest(log=changed):
                self.assertTrue(self.check_fixture(True, log=changed))

    def test_fault_and_control_require_eoc_event_and_selected_golden(self):
        for fault in (True, False):
            log, uart = fixture(fault)
            branch = int(fault)
            for changed_log, changed_uart in (
                (log.replace(driver.SIM_OK_MARKER, "NO_EOC"), uart),
                (log, uart.replace(f"cerf_fb_evt=0x{4 if fault else 0:x}",
                                   f"cerf_fb_evt=0x{0 if fault else 4:x}")),
                (log, uart.replace(f"cerf=0x{1 << branch:x}", f"cerf=0x{1 << (1 - branch):x}")),
                (log, uart.replace("cerf_fb_en=0x4", "cerf_fb_en=0x2")),
                (log, uart.replace(f"C_branch_{branch}", f"C_branch_{1 - branch}")),
                (log, uart.replace(": PASS", ": FAIL")),
                (log, uart.replace("join complete", "no join")),
            ):
                with self.subTest(fault=fault, log=changed_log, uart=changed_uart):
                    self.assertTrue(self.check_fixture(fault, log=changed_log, uart=changed_uart))

    def test_healthy_control_never_dispatches_backup_or_uses_remote_link(self):
        log, _ = fixture(False)
        for changed in (
            log + "\n[BINGO_DISPATCH] 145 chip=0 task=4 core=2 cluster=0",
            log + "\n[BINGO_CERF_FB] 120 type 2 clear g0 set g1",
            log + "\n[BINGO_REMOTE_REJECT_IN] 110 chip=0 task=3 proxy_slot=1",
            log + "\n[BINGO_EXPORT] 80 chip=0 task=3 type_id=2 proxy_slot=1",
            log.replace("task=8", "task=99"),
        ):
            with self.subTest(log=changed):
                self.assertTrue(self.check_fixture(False, log=changed))


if __name__ == "__main__":
    unittest.main()
