"""The approved C2b data controls and layout-preserving instruction."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "watchdog_c2b", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class C2bTests(unittest.TestCase):
    def test_nophb_only_changes_the_image_flag(self):
        scenes = watchdog.c2b_scenarios()
        self.assertEqual(len(scenes), 4)
        for label in ("chain", "dummy", "moe2"):
            scene = scenes[f"c2_{label}_nophb"]
            baseline = watchdog.SCENARIOS[f"c2_{label}_off"]
            self.assertEqual(watchdog.scenario_test_cfg(scene),
                             watchdog.scenario_test_cfg(baseline))
            self.assertEqual(watchdog.t1_image_flags(scene), "-DBINGO_WD_NOP_HEARTBEAT")
            for key in ("cfg", "timeout_cycles", "confirm_timeout_cycles", "extra_cfg"):
                self.assertEqual(scene[key], baseline[key])
            self.assertEqual(scene["c2_part"], 2)

    def test_cerf0_only_moves_the_cerf_entry_to_host_type(self):
        first = watchdog.scenario_test_cfg(watchdog.SCENARIOS["c2_chain_cerf"])
        second = watchdog.scenario_test_cfg(watchdog.SCENARIOS["c2_chain_cerf0"])
        self.assertEqual(second, dict(first, cerf_fb_core=2))
        self.assertEqual(watchdog.t1_image_flags(watchdog.SCENARIOS["c2_chain_cerf0"]), "")

    def test_flag_cannot_enter_a_same_image_family(self):
        for extra in (dict(t1=False), dict(same_as="tch0")):
            scene = dict(t1=True, image_flags="-DBINGO_WD_NOP_HEARTBEAT")
            scene.update(extra)
            with self.assertRaisesRegex(ValueError, "same-image family"):
                watchdog.t1_image_flags(scene)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            watchdog.t1_image_flags(dict(t1=True, image_flags="-DBINGO_WD_NOP_HEARTBEAT=1"))

    def test_exact_approved_assembly_and_exit_guard(self):
        root = Path(__file__).resolve().parents[4]
        text = (root / "target/sw/device/runtime/src/bingo_hw_heartbeat.h").read_text()
        branch = text.split("#elif defined(BINGO_WD_NOP_HEARTBEAT)", 1)[1].split("#endif", 1)[0]
        self.assertIn("if (!(value & BINGO_HW_HEARTBEAT_EXIT))", branch)
        self.assertIn(r'".option push\n.option norvc\naddi x0, %0, 0\n.option pop"', branch)
        self.assertIn(': : "r"(value)', branch)
        self.assertIn("return;", branch)
        self.assertIn('asm volatile("csrw ', text.split("#endif", 1)[1])


if __name__ == "__main__":
    unittest.main()
