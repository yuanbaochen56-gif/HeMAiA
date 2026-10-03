"""F4 is the same recovery contract as B1, with an int32 host kernel."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from test_bingo_host_fallback import driver, fixture, IDS, drop


def add_fixture(name):
    baseline = {"t53": "s34", "t54": "s35", "t55": "s36"}[name]
    log, uart = fixture(baseline)
    return log, uart.replace("A_L1", "C_l3").replace("DmaHostFallback", "AddHostFallback")


class AddHostFallbackCheckerTests(unittest.TestCase):
    def evaluate(self, name, log, uart):
        sc = dict(driver.SCENARIOS[name], host_fallback_task_ids=IDS)
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.evaluate(name, sc, log, uart)

    def test_all_three_controls_pass_and_share_one_t1_family(self):
        self.assertEqual(driver.T1_FAMILIES["F4"], ("t53", "t54", "t55"))
        for name in driver.T1_FAMILIES["F4"]:
            sc = driver.SCENARIOS[name]
            self.assertTrue(sc["t1"] and sc["add_host_fallback"])
            self.assertEqual(sc["workload"], "add_host_fallback_2cluster")
            self.assertEqual(sc["extra_flags"], "")
            self.assertEqual(self.evaluate(name, *add_fixture(name)), [])

    def test_backup_branch_slot_exit_event_and_golden_failures(self):
        log, uart = add_fixture("t53")
        for changed_log, changed_uart in (
                (drop(log, "[BINGO_CERF_FB]"), uart),
                (drop(log, "[BINGO_REPLAY]"), uart),
                (drop(log, "task=2 core=2"), uart),
                (log.replace("task=2 core=2", "task=2 core=1"), uart),
                (log + "\n[BINGO_DISPATCH] 145 chip=0 task=5 core=1 cluster=1", uart),
                (log, uart.replace("C_l3", "A_L1")),
                (log, uart.replace("PASS", "FAIL")),
                (log, uart.replace("cerf_fb_evt=0x4", "cerf_fb_evt=0x0")),
                (log, uart.replace("AddHostFallback", "DmaHostFallback"))):
            self.assertTrue(self.evaluate("t53", changed_log, changed_uart))
        for name in ("t54", "t55"):
            log, uart = add_fixture(name)
            self.assertTrue(self.evaluate(
                name, log + "\n[BINGO_DISPATCH] 135 chip=0 task=2 core=2 cluster=0", uart))
            self.assertTrue(self.evaluate(name, drop(log, driver.SIM_OK_MARKER), uart))

    def test_ids_require_add_kernels_not_copy_kernels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "graph.csv"
            path.write_text(
                "ID,Chiplet,Cluster,Core,Type,Kernel\n"
                "0,00,0,1,normal,__snax_bingo_kernel_int32_add\n"
                "1,00,0,2,normal,__host_bingo_kernel_check_result\n"
                "2,00,0,2,normal,__host_bingo_kernel_add_i32\n"
                "5,00,0,1,normal,__snax_bingo_kernel_exit\n"
                "7,00,1,1,normal,__snax_bingo_kernel_exit\n")
            self.assertEqual(driver.host_fallback_task_ids(path, add=True), IDS)
            with self.assertRaises(ValueError):
                driver.host_fallback_task_ids(path)


if __name__ == "__main__":
    unittest.main()
