"""Offline tests for the R9 injected-task budget. No simulation."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test"), str(W / "util/automation_scripts")]
import s1b_injected_budget_r9 as budget

PRED = W / "evidence/chain_s1b_r7/profile/predictions.json"
R8 = W / "results/GateS1bR8"
INJECT = "[BINGO_SDC_INJECT] gid={} cluster={} core={} offset=17 mask=4\n"


def event(kind, stamp_us, task, cluster, core):
    return f"[BINGO_{kind}] {int(stamp_us * 1_000_000)} chip=0 task={task} core={core} cluster={cluster}\n"


def scene_log(task, cluster, core, duration_us, other=None):
    log = event("DISPATCH", 100, task, cluster, core) + event("DONE", 100 + duration_us, task, cluster, core)
    if other:
        log += event("DISPATCH", 2000, other[0], 0, 1) + event("DONE", 2000 + other[1], other[0], 0, 1)
    return log


class InjectedBudgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sealed = json.loads(PRED.read_text())
        cls.record = budget.derive(cls.sealed, budget.r8_measurement(), budget.healthy_print_rates())

    def judged(self, problems):
        return dict(status="FAIL" if problems else "PASS", problems=list(problems))

    def apply(self, name, log, uart, problems):
        return budget.apply(self.judged(problems), name, self.sealed["scenes"][name], log, uart,
                            self.sealed, self.record)

    def test_line_lengths_follow_the_sealed_formats(self):
        self.assertEqual(budget.rendered_length(budget.EXIT_FORMAT, (0, 0, 0)), 41)
        self.assertEqual(budget.rendered_length(budget.INJECT_FORMAT, (1, 0, 1, 17, 4)), 60)
        self.assertEqual(budget.rendered_length(budget.INJECT_FORMAT, (12, 0, 1, 17, 4)), 61)
        self.assertEqual(budget.source_formats(self.sealed)["inject"], budget.INJECT_FORMAT)

    def test_budget_is_derived_from_frozen_allowances_only_for_injected_scenes(self):
        scenes = self.record["scenes"]
        self.assertEqual(sorted(scenes), ["s1b_stage0_primary_off", "s1b_stage0_primary_on",
                                          "s1b_stage0_shadow_on", "s1b_stage1_primary_on"])
        self.assertEqual({(s["task"], s["cluster"], s["core"]) for s in scenes.values()},
                         {(1, 0, 1), (5, 0, 1), (2, 1, 1)})
        for row in scenes.values():
            self.assertAlmostEqual(row["exact_us"], 64 + 128 + 512 * 60 / 41)
            self.assertEqual(row["budget_us"], 942)
            self.assertGreater(row["budget_ps"] - 64_000_000, row["predicted_hook_ps"]["high"])
        self.assertEqual(self.record["frozen_allowances_us"], dict(device_dma_each=64, device_exit_each=512))

    def test_r8_hook_measurement_from_the_dma_trace(self):
        m = self.record["r8_measurement"]
        self.assertEqual(m["hook_cost_ps"], 368_872_000 - 15_596_000)
        self.assertEqual(m["injected"]["hook_dma_read_modify_write_ps"], 2_808_000)
        self.assertEqual([row[1] for row in m["injected"]["requests"]], [64, 1, 1])
        low, high = (self.record["scenes"]["s1b_stage0_primary_on"]["predicted_hook_ps"][k]
                     for k in ("low", "high"))
        self.assertLessEqual(low, m["hook_cost_ps"])
        self.assertLessEqual(m["hook_cost_ps"], high)

    def test_injected_task_uses_its_budget(self):
        out = self.apply("s1b_stage0_primary_on", scene_log(1, 0, 1, 368.872), INJECT.format(1, 0, 1),
                         ["Task1 device_dma_each budget exceeded"])
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(out["problems"], [])
        self.assertTrue(out["injected_task_budget"]["applied"])
        self.assertEqual(out["injected_task_budget"]["measured_ps"], 368_872_000)

    def test_injected_task_over_its_budget_fails(self):
        out = self.apply("s1b_stage0_shadow_on", scene_log(2, 1, 1, 942.001), INJECT.format(2, 1, 1),
                         ["Task2 device_dma_each budget exceeded"])
        self.assertEqual(out["problems"], ["Task2 injected_task budget exceeded"])
        self.assertEqual(out["status"], "FAIL")

    def test_every_other_dma_keeps_64_us(self):
        out = self.apply("s1b_stage0_primary_on", scene_log(1, 0, 1, 300, other=(5, 64.001)),
                         INJECT.format(1, 0, 1),
                         ["Task1 device_dma_each budget exceeded", "Task5 device_dma_each budget exceeded"])
        self.assertEqual(out["problems"], ["Task5 device_dma_each budget exceeded"])
        self.assertEqual(out["status"], "FAIL")

    def test_wrong_slot_or_activation_keeps_64_us(self):
        label = ["Task5 device_dma_each budget exceeded"]
        for log, uart in ((scene_log(5, 1, 1, 300), INJECT.format(5, 0, 1)),
                          (scene_log(5, 0, 1, 300), ""),
                          (scene_log(5, 0, 1, 300), INJECT.format(5, 0, 1) * 2),
                          (scene_log(5, 0, 1, 300), INJECT.format(1, 0, 1))):
            out = self.apply("s1b_stage1_primary_on", log, uart, label)
            self.assertEqual(out["problems"], label)
            self.assertFalse(out["injected_task_budget"]["applied"])

    def test_non_injected_scenes_are_unchanged(self):
        for name in ("s1b_healthy_on", "s1b_healthy_off", "s1b_invalid_status"):
            out = self.apply(name, scene_log(1, 0, 1, 64.001), "", ["Task1 device_dma_each budget exceeded"])
            self.assertEqual(out["problems"], ["Task1 device_dma_each budget exceeded"])
            self.assertIsNone(out["injected_task_budget"])
        record = copy.deepcopy(self.record)
        record["scenes"]["s1b_healthy_on"] = record["scenes"]["s1b_stage0_primary_on"]
        with self.assertRaises(budget.BudgetError):
            budget.apply(self.judged([]), "s1b_healthy_on", self.sealed["scenes"]["s1b_healthy_on"],
                         "", "", self.sealed, record)

    def test_disagreement_with_the_unchanged_judge_is_rejected(self):
        with self.assertRaises(budget.BudgetError):
            self.apply("s1b_stage0_primary_on", scene_log(1, 0, 1, 60), INJECT.format(1, 0, 1),
                       ["Task1 device_dma_each budget exceeded"])
        with self.assertRaises(budget.BudgetError):
            self.apply("s1b_stage0_primary_on", scene_log(1, 0, 1, 70), INJECT.format(1, 0, 1), [])

    def test_freeze_is_write_once_and_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            binding = budget.freeze(Path(tmp) / "profile", self.record)
            self.assertEqual(budget.load(binding)["scenes"], self.record["scenes"])
            with self.assertRaises(FileExistsError):
                budget.freeze(Path(tmp) / "profile", self.record)
            with self.assertRaises(budget.BudgetError):
                budget.load(dict(binding, sha256="0" * 64))
            os.chmod(binding["path"], 0o644)
            with self.assertRaises(budget.BudgetError):
                budget.load(binding)

    def test_r8_scene3_failure_was_only_the_injected_budget(self):
        import bingo_sdc_recover_check
        import s1b_system_r1
        import s1b_reference_r8 as r8
        run = json.loads((R8 / "s1b_stage0_primary_on/judgement.json").read_text())
        self.assertEqual(run["problems"], ["Task1 device_dma_each budget exceeded"])
        bins, = (R8 / "s1b_stage0_primary_on").glob("task_*/bin")
        original = (bingo_sdc_recover_check.compare_release, s1b_system_r1.compare_release)
        bingo_sdc_recover_check.compare_release = s1b_system_r1.compare_release = r8.compare_release
        try:
            reference = dict(self.sealed["release_reference"], sdc_enable=1)
            judged = s1b_system_r1.judge("s1b_stage0_primary_on", self.sealed["scenes"]["s1b_stage0_primary_on"],
                                         (bins / "sim_run.log").read_text(), (bins / "uart_chip_0_0.log").read_text(),
                                         (R8 / "s1b_stage0_primary_on/observations.txt").read_text(),
                                         self.sealed, reference)
        finally:
            bingo_sdc_recover_check.compare_release, s1b_system_r1.compare_release = original
        self.assertEqual(judged["problems"], run["problems"])
        out = budget.apply(judged, "s1b_stage0_primary_on", self.sealed["scenes"]["s1b_stage0_primary_on"],
                           (bins / "sim_run.log").read_text(), (bins / "uart_chip_0_0.log").read_text(),
                           self.sealed, self.record)
        self.assertEqual(out["problems"], [])
        self.assertTrue(out["injected_task_budget"]["passed"])


if __name__ == "__main__":
    unittest.main()
