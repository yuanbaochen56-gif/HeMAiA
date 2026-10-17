"""Scenes judged by an evaluation script are marked in result.md (EXTERNAL_JUDGES)."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "external_judge_driver", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class ExternalJudgeTests(unittest.TestCase):
    def test_the_c3_sweep_and_the_fail_stop_c6_scenes(self):
        scenes = driver.SCENARIOS
        self.assertIn("bingo_c3.classify", driver.external_judge(scenes["c3_a_g5_post"]))
        self.assertIn("bingo_c6.classify_c6", driver.external_judge(scenes["c6_eec_none"]))
        self.assertIn("bingo_c6.classify_c6", driver.external_judge(scenes["c6_moe2_none"]))
        for name in ("s1", "c6_eec_hw", "c4_chain_h100", "p9_moe2_ok_on", "b2v3_ok"):
            self.assertIsNone(driver.external_judge(scenes[name]), name)

    def test_the_scene_table_is_unchanged(self):
        self.assertFalse(any("judge" in key for scene in driver.SCENARIOS.values() for key in scene))


if __name__ == "__main__":
    unittest.main()
