"""Offline tests for the R11 r9 gate profile. No simulation."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test"), str(W / "util/automation_scripts")]
from bingo_sdc_recover_reference import reference_context
import s1b_injected_budget_r9 as budget
import s1b_profile_r10 as r10
import s1b_profile_r11 as profiles
import s1b_reference_r8 as r8
from s1b_system_r1 import sha

PRED = W / "evidence/chain_s1b_r7/profile/predictions.json"
R10 = W / "evidence/GateS1bR10"
SHADOW = "s1b_stage0_shadow_on"
REUSED = {"s1b_healthy_off": W / "evidence/GateS1bR8", "s1b_healthy_on": W / "evidence/GateS1bR8",
          "s1b_stage0_primary_on": W / "evidence/GateS1bR9", "s1b_stage1_primary_on": W / "evidence/GateS1bR9",
          "s1b_stage0_primary_off": R10}


class GateProfileR9Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sealed = json.loads(PRED.read_text())
        state = json.loads((R10 / "system/progress.json").read_text())
        cls.state = state
        cls.bindings = {int(k): v for k, v in state["references"].items()}
        cls.record = budget.load(state["budgets"])
        cls.frozen_r8 = r10.load(state["gate_profile"])
        cls.r8_profile = r10.derive(cls.sealed, dict(path=str(PRED), sha256=sha(PRED)), state["budgets"], cls.record)
        cls.context = reference_context(cls.sealed)
        cls.profile = cls.derive()
        cls.shadow_r10 = cls.judge(r10.judge, SHADOW, R10, cls.profile)

    @classmethod
    def derive(cls, sealed=None, frozen_r8=None, record=None):
        return profiles.derive(sealed or cls.sealed, cls.r8_profile, frozen_r8 or cls.frozen_r8,
                               cls.bindings, cls.context, record or cls.record)

    @classmethod
    def judge(cls, function, name, evidence, profile):
        out = Path(next(run for run in json.loads((evidence / "system/progress.json").read_text())["runs"]
                        if run["name"] == name)["directory"])
        bins, = out.glob("task_*/bin")
        scene = cls.sealed["scenes"][name]
        reference = r8.select_reference(cls.bindings, scene, cls.sealed["predictions"][name]["cfg"])
        return function(name, scene, (bins / "sim_run.log").read_text(), (bins / "uart_chip_0_0.log").read_text(),
                        (out / "observations.txt").read_text(), cls.sealed, reference, cls.record, profile)

    def mutated(self, change):
        judged = copy.deepcopy(self.shadow_r10)
        change(judged)
        failing = {t: r for t, r in judged["release"]["tasks"].items() if r["extra_ps"] > profiles.CAP_PS}
        for row in judged["release"]["tasks"].values():
            row["pass"] = row["extra_ps"] <= profiles.CAP_PS
        judged["problems"] = [p for p in judged["problems"] if not p.startswith(profiles.RELEASE_LABEL)]
        if failing:
            judged["problems"].append(profiles.RELEASE_LABEL + str(judged["release"]["tasks"]))
        return profiles.release(judged, SHADOW, self.profile)

    def delay_p0(self, judged, ps):
        row = judged["release"]["tasks"]["1"]
        row["dispatch_ps"] += ps
        row["latency_ps"] += ps
        row["extra_ps"] = max(0, row["latency_ps"] - row["L_ref_ps"])
        judged["dispatch"][1] += ps

    def test_profile_is_r8_plus_one_release_row(self):
        p = self.profile
        self.assertEqual({k: v for k, v in self.frozen_r8.items() if k not in ("frozen_at", "label", "user_decision",
                                                                               "decisions")},
                         {k: p[k] for k in self.frozen_r8 if k not in ("frozen_at", "label", "user_decision",
                                                                       "decisions")})
        self.assertEqual(p["label"], "r9_gate_profile")
        self.assertEqual(set(p["decisions"]), {"terminal_lines", "envelope", "release_injected_predecessor"})
        rows = p["release_injected_predecessor"]["rows"]
        self.assertEqual(list(rows), [SHADOW])
        row = rows[SHADOW]
        self.assertEqual((row["task"], row["injected_task"], row["reference_run_ps"], row["injected_budget_us"]),
                         (1, 2, 15_568_000, 942))
        self.assertEqual(row["reference"]["sha256"], "95396f31ba6779b2447d265cb8c9d36946a3acc9286948415199b1909b93ec75")
        self.assertEqual(p["release_injected_predecessor"]["cap_ps"], 1_000_000)

    def test_derive_refuses_anything_else(self):
        frozen = dict(self.frozen_r8, scene_envelopes_us={"s1b_stage0_primary_off": 9579})
        with self.assertRaises(profiles.ProfileError):
            self.derive(frozen_r8=frozen)
        sealed = copy.deepcopy(self.sealed)
        sealed["release_observation"]["threshold_ps"] = 2_000_000
        with self.assertRaises(profiles.ProfileError):
            self.derive(sealed=sealed)
        record = copy.deepcopy(self.record)
        record["scenes"][SHADOW]["budget_us"] = 943
        with self.assertRaises(profiles.ProfileError):
            self.derive(record=record)
        record = copy.deepcopy(self.record)
        record["scenes"][SHADOW]["task"] = 1
        with self.assertRaises(profiles.ProfileError):
            self.derive(record=record)

    def test_retained_r10_shadow_on_passes_with_only_the_release_row_changed(self):
        old = self.shadow_r10
        self.assertEqual([p[:len(profiles.RELEASE_LABEL)] for p in old["problems"]], [profiles.RELEASE_LABEL])
        new = self.judge(profiles.judge, SHADOW, R10, self.profile)
        self.assertEqual((new["status"], new["problems"]), ("PASS", []))
        adjustment = new["release_adjustment"]
        self.assertTrue(adjustment["applied"])
        self.assertEqual((adjustment["extra_ps"], adjustment["injected_run_increase_ps"], adjustment["gate_extra_ps"]),
                         (352_688_000, 352_688_000, 0))
        for task, row in old["release"]["tasks"].items():
            if task != "1":
                self.assertEqual(new["release"]["tasks"][task], row)
        self.assertEqual(new["injected_task_budget"], old["injected_task_budget"])
        self.assertEqual(new["success_envelope"], old["success_envelope"])

    def test_cap_stays_one_microsecond_after_subtraction(self):
        self.assertEqual(self.mutated(lambda j: self.delay_p0(j, 1_000_000))["status"], "PASS")
        out = self.mutated(lambda j: self.delay_p0(j, 1_000_001))
        self.assertEqual(out["release_adjustment"]["gate_extra_ps"], 1_000_001)
        self.assertEqual(out["status"], "FAIL")

    def test_no_subtraction_unless_the_injected_task_is_the_waited_on_predecessor(self):
        def unapplied(j):
            j["injected_task_budget"]["applied"] = False

        def over_budget(j):
            j["injected_task_budget"]["passed"] = False

        def other_dispatch_first(j):
            j["dispatch"][99] = j["done"][2] + 1

        def injected_outside_window(j):
            row = j["release"]["tasks"]["1"]
            row["eligible_ps"] = j["dispatch"][2] + 1
            row["latency_ps"] = row["dispatch_ps"] - row["eligible_ps"]
            row["extra_ps"] = max(0, row["latency_ps"] - row["L_ref_ps"])

        for change in (unapplied, over_budget, other_dispatch_first, injected_outside_window):
            out = self.mutated(change)
            self.assertFalse(out["release_adjustment"]["applied"], change.__name__)
            self.assertEqual(out["status"], "FAIL", change.__name__)
            self.assertEqual(out["release_adjustment"]["gate_extra_ps"], out["release_adjustment"]["extra_ps"])

    def test_every_other_row_keeps_the_r1_check(self):
        def task9(j):
            row = j["release"]["tasks"]["9"]
            row["latency_ps"] = row["L_ref_ps"] + 1_000_001
            row["extra_ps"] = 1_000_001
        out = self.mutated(task9)
        self.assertTrue(out["release_adjustment"]["applied"])
        self.assertEqual(out["status"], "FAIL")
        self.assertFalse(out["release"]["tasks"]["9"]["pass"])

    def test_disagreement_with_the_unchanged_judge_is_rejected(self):
        judged = copy.deepcopy(self.shadow_r10)
        judged["problems"] = []
        with self.assertRaises(profiles.ProfileError):
            profiles.release(judged, SHADOW, self.profile)
        judged = copy.deepcopy(self.shadow_r10)
        judged["release"]["tasks"]["9"]["extra_ps"] += 1
        with self.assertRaises(profiles.ProfileError):
            profiles.release(judged, SHADOW, self.profile)

    def test_reused_runs_keep_their_verdict(self):
        for name, evidence in REUSED.items():
            old = self.judge(r10.judge, name, evidence, self.frozen_r8)
            new = self.judge(profiles.judge, name, evidence, self.profile)
            self.assertIsNone(new["release_adjustment"], name)
            self.assertEqual((new["status"], new["problems"]), ("PASS", []), name)
            for key in ("release", "injected_task_budget", "warning_gate", "success_envelope", "dispatch", "done"):
                self.assertEqual(new[key], old[key], (name, key))

    def test_scene_without_declared_row_is_untouched(self):
        judged = copy.deepcopy(self.shadow_r10)
        out = profiles.release(judged, "s1b_invalid_status", self.profile)
        self.assertIsNone(out["release_adjustment"])
        self.assertEqual((out["status"], out["problems"]), (judged["status"], judged["problems"]))

    def test_freeze_is_write_once_and_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            binding = profiles.freeze(Path(tmp) / "profile", self.profile)
            loaded = profiles.load(binding)
            self.assertEqual({k: v for k, v in loaded.items() if k != "frozen_at"}, json.loads(json.dumps(self.profile)))
            with self.assertRaises(FileExistsError):
                profiles.freeze(Path(tmp) / "profile", self.profile)
            with self.assertRaises(profiles.ProfileError):
                profiles.load(dict(binding, sha256="0" * 64))
            with self.assertRaises(profiles.ProfileError):
                r10.load(binding)
            os.chmod(binding["path"], 0o644)
            with self.assertRaises(profiles.ProfileError):
                profiles.load(binding)


if __name__ == "__main__":
    unittest.main()
