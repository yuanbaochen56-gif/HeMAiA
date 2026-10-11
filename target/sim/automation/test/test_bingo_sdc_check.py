"""Classify hand-written SDC outcomes and generate graph-bound scenes."""
import unittest

from bingo_sdc_check import classify_sdc, evaluate_sdc
from bingo_sdc_scenarios import sdc_scenarios
from test_bingo_test_cfg import watchdog


def evidence(passed=True, mismatch=False, injected=False):
    log = ("All chips finished successfully" if passed else
           "Simulation of chip_0_0 finished with status 2\n** Fatal: All chips finished with errors")
    uart = "[Host] Bingo status: replay_stuck=0\n[Host] Check [chain]: " + ("PASS" if passed else "FAIL") + "\n"
    if injected:
        uart += "[BINGO_SDC_INJECT] gid=2 cluster=0 core=1 offset=17 mask=128\n"
    if mismatch:
        uart += "[Host] SDC mismatch: primary=2 shadow=10 compare=11 code=18\n"
    uart += f"[Host] SDC report: {int(mismatch)} mismatches\n"
    return log, uart


class SdcCheckerTests(unittest.TestCase):
    def test_detection_shadow_baseline_and_health_categories(self):
        for passed, mismatch, enabled, expected in (
                (True, False, False, "recovered"), (True, False, True, "recovered"),
                (False, True, True, "detected-sdc"),
                (True, True, True, "detected-sdc-shadow"),
                (False, False, False, "wrong")):
            with self.subTest(expected=expected):
                self.assertEqual(classify_sdc(*evidence(passed, mismatch),
                                             enabled=enabled)["class"], expected)

    def test_missing_contradictory_or_unactivated_evidence_fails_closed(self):
        log, uart = evidence()
        for altered in (uart.replace("[Host] SDC report: 0 mismatches\n", ""),
                        uart.replace("0 mismatches", "1 mismatches"),
                        uart + "[BINGO_SDC_WARN] bad offset\n"):
            self.assertEqual(classify_sdc(log, altered, enabled=True)["class"], "unclassified")
        self.assertEqual(classify_sdc(*evidence(True, True), enabled=False)["class"], "unclassified")
        self.assertEqual(classify_sdc(log, uart, enabled=True,
            fault=(2, 0, 1, 17, 128))["class"], "unclassified")
        self.assertEqual(classify_sdc("", uart, enabled=True)["class"], "unclassified")

    def test_scene_expectations_are_derived_from_the_actual_pairs(self):
        report = dict(nodes=[dict(node_id=i, selected=True, shadow_id=6 + 2 * i,
                                  compare_id=7 + 2 * i) for i in range(6)])
        scenes = sdc_scenarios(watchdog.SCENARIOS["c3_ok_chain_l2"], report)
        self.assertEqual(len(scenes), 6)
        self.assertEqual({watchdog.t1_image_flags(sc) for sc in scenes.values()},
                         {"-DBINGO_SDC -DBINGO_WD_ADAPT"})
        self.assertEqual(scenes["s1_shadow2_on"]["fault_sdc_gid"], 10)
        self.assertEqual(scenes["s1_copy5_on"]["sdc_expect_mismatches"],
                         [dict(primary=5, shadow=16, compare=17, code=18)])
        for scene in scenes.values():
            self.assertEqual(len(watchdog.test_cfg_bytes(watchdog.scenario_test_cfg(scene))), 384)
        selected = scenes["s1_copy2_on"]
        self.assertEqual(evaluate_sdc(selected, *evidence(False, True, True)), [])
        self.assertEqual(watchdog.evaluate("s1_copy2_on", selected,
                                          *evidence(False, True, True)), [])


if __name__ == "__main__":
    unittest.main()
