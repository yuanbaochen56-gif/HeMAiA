"""The substitute choice of test images (BINGO_SUB_CFG): its configuration words."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "watchdog_substitute_choice", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class SubstituteChoiceTests(unittest.TestCase):
    def scene(self, **keys):
        return dict(watchdog.SCENARIOS["tch0"], image_flags="-DBINGO_SUB_CFG", **keys)

    def test_flag_takes_the_v3_row(self):
        self.assertEqual(watchdog.t1_image_flags(self.scene()), "-DBINGO_SUB_CFG -DBINGO_WD_ADAPT")
        self.assertEqual(watchdog.scenario_test_cfg(self.scene())["version"], 3)

    def test_spare_word(self):
        cfg = watchdog.scenario_test_cfg(self.scene(sub_spare=0x8))
        data = watchdog.test_cfg_bytes(cfg)
        self.assertEqual((len(data), watchdog.test_cfg_offset("sub_spare")), (384, 368))
        self.assertEqual(int.from_bytes(data[368:372], "little"), 0x8)
        # the words after it keep their offsets
        self.assertEqual((watchdog.test_cfg_offset("fault_beat_period"), watchdog.test_cfg_offset("wd_budget"),
                          watchdog.test_cfg_offset("task_time")), (372, 376, 380))
        for bad in (-1, 1 << 32, 1.0):
            with self.assertRaises(ValueError):
                watchdog.test_cfg_bytes(dict(cfg, sub_spare=bad))

    def test_spare_word_needs_the_flag(self):
        with self.assertRaises(ValueError):
            watchdog.scenario_test_cfg(dict(watchdog.SCENARIOS["tch0"], image_flags="-DBINGO_WD_LATE", sub_spare=1))
        with self.assertRaises(ValueError):
            watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(2), sub_spare=1))

    def test_partition_word(self):
        cfg = watchdog.scenario_test_cfg(self.scene(sub_part_clear=0xFFFF_DDEE))
        data = watchdog.test_cfg_bytes(cfg)
        self.assertEqual(watchdog.test_cfg_offset("sub_part_clear"), 364)
        self.assertEqual(int.from_bytes(data[364:368], "little"), 0xFFFF_DDEE)
        with self.assertRaises(ValueError):
            watchdog.scenario_test_cfg(dict(watchdog.SCENARIOS["tch0"], image_flags="-DBINGO_WD_LATE",
                                            sub_part_clear=1))

    def test_default_is_zero(self):
        data = watchdog.test_cfg_bytes(watchdog.scenario_test_cfg(self.scene()))
        self.assertEqual(int.from_bytes(data[364:372], "little"), 0)


    def test_four_cluster_rule_takes_the_spare_first(self):
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from bingo_four_cluster import substitute
        self.assertEqual(substitute(1, 1, {(1, 1)}), 0)
        self.assertEqual(substitute(1, 1, {(1, 1)}, spare=0x8), 3)
        self.assertEqual(substitute(1, 1, {(1, 1), (1, 3)}, spare=0x8), 0)
        self.assertEqual(substitute(1, 3, {(1, 3)}, spare=0x8), 0)
        self.assertEqual(substitute(1, 2, {(1, 2)}), 0)
        # partitions {0, 1} and {2, 3}: word 0x2211 (clear 0xFFFF_DDEE)
        self.assertEqual(substitute(1, 2, {(1, 2)}, partitions=0x2211), 3)
        self.assertIsNone(substitute(1, 2, {(1, 2), (1, 3)}, partitions=0x2211))
        self.assertEqual(substitute(1, 1, {(1, 1)}, spare=0x8, partitions=0x2211), 0)


if __name__ == "__main__":
    unittest.main()
