"""Offline tests for the R10 r8 gate profile. No simulation."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test"), str(W / "util/automation_scripts")]
from bingo_sdc_recover_reference import ReferenceError
import s1b_injected_budget_r9 as budget
import s1b_profile_r10 as profiles
from s1b_system_r1 import sha

PRED = W / "evidence/chain_s1b_r7/profile/predictions.json"
R9 = W / "evidence/GateS1bR9"
R9_RESULTS = W / "results/GateS1bR9"
OFF = "s1b_stage0_primary_off"


def retained(name):
    out = R9_RESULTS / name
    bins, = out.glob("task_*/bin")
    return out, bins, (bins / "sim_run.log").read_text()


class GateProfileTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sealed = json.loads(PRED.read_text())
        cls.binding = json.loads((R9 / "profile/plan.json").read_text())["injected_task_budgets"]
        cls.record = budget.load(cls.binding)
        cls.profile = profiles.derive(cls.sealed, dict(path=str(PRED), sha256=sha(PRED)), cls.binding, cls.record)
        cls.off_log = retained(OFF)[2]
        cls.clean_log = retained("s1b_stage0_primary_on")[2]

    def judged(self, problems, span_us, thirds=(0, 0)):
        return dict(status="FAIL" if problems else "PASS", problems=list(problems),
                    recovery=[dict(third=t) for t in thirds], completion_ps=1_000_000 + round(span_us * 1e6),
                    dispatch={10: 1_000_000})

    def envelope(self, name, problems, span_us, thirds=(0, 0)):
        return profiles.envelope(self.judged(problems, span_us, thirds), name, self.sealed["scenes"][name],
                                 self.sealed, self.profile)

    def test_profile_holds_only_the_two_decisions(self):
        p = self.profile
        self.assertEqual(p["terminal_scenes"], ["s1b_stage0_primary_off", "s1b_invalid_status"])
        self.assertEqual(p["scene_envelopes_us"], {OFF: 9578})
        self.assertEqual(p["unchanged_envelopes_us"], {"0": 8700, "1": 9616, "2": 10640})
        self.assertEqual((p["envelope_rule"]["healthy_limit_us"], p["envelope_rule"]["extra_us"]), (8700, 878))
        self.assertEqual([row["line"] for row in p["terminal_lines"]], [37, 50])
        self.assertEqual(p["check_finish"]["sha256"], profiles.CHECK_FINISH_SHA)
        self.assertEqual(p["injected_task_budgets"]["sha256"], self.binding["sha256"])

    def test_derive_refuses_anything_else(self):
        record = copy.deepcopy(self.record)
        record["scenes"][OFF]["budget_us"] = 943
        with self.assertRaises(profiles.ProfileError):
            profiles.derive(self.sealed, {}, self.binding, record)
        sealed = copy.deepcopy(self.sealed)
        sealed["predictions"]["s1b_healthy_on"]["model"]["completion_word"] = 5
        with self.assertRaises(profiles.ProfileError):
            profiles.derive(sealed, {}, self.binding, self.record)
        sealed = copy.deepcopy(self.sealed)
        sealed["budgets"]["normal_all_healthy_envelope"] = 8701
        with self.assertRaises(profiles.ProfileError):
            profiles.derive(sealed, {}, self.binding, self.record)

    def test_terminal_gate_accepts_the_retained_r9_ending(self):
        for name in profiles.TERMINAL_SCENES:
            gate = profiles.log_gate(name, self.off_log, self.profile)
            self.assertEqual(gate["questa_errors"], 2)
            self.assertEqual([row["line"] for row in gate["locations"]], [37, 50])

    def test_terminal_gate_rejects_any_other_error(self):
        log = self.off_log
        error = "# ** Error: Simulation of chip_0_0 finished with status 5 at 13765647000\n"
        fatal = "# ** Fatal: All chips finished with errors at 13765647000\n"
        self.assertEqual((log.count(error), log.count(fatal)), (1, 1))
        variants = [
            log.replace(error, "# ** Error: other\n#    Time: 1 ns  Scope: testharness.x File: y.sv Line: 1\n" + error),
            log.replace(error, error.replace("status 5", "status 4")),
            log.replace(" Line: 37", " Line: 38"),
            log.replace(fatal, fatal.replace("13765647000", "13765648000")),
            log.replace("Errors: 2, Warnings: 0", "Errors: 3, Warnings: 0"),
            log.replace(fatal, ""),
            log.replace(error, "# ** Warning: cache line reading invalid data on port 0\n" + error),
            log + "# ** Fatal: late\n",
            log.replace("evidence/PreparationS1bR1/HeMAiA/target/sim/testharness/util/check_finish_rtl.sv Line",
                        "evidence/missing/target/sim/testharness/util/check_finish_rtl.sv Line"),
            self.clean_log]
        for text in variants:
            with self.assertRaises(ReferenceError):
                profiles.log_gate(OFF, text, self.profile)

    def test_other_scenes_keep_zero_questa_errors(self):
        for name in self.sealed["scenes"]:
            if name in profiles.TERMINAL_SCENES:
                continue
            with self.assertRaises(ReferenceError):
                profiles.log_gate(name, self.off_log, self.profile)
            self.assertEqual(profiles.log_gate(name, self.clean_log, self.profile)["questa_errors"], 0)

    def test_envelope_changes_only_stage0_primary_off(self):
        label = profiles.ENVELOPE_LABEL
        self.assertEqual(self.envelope(OFF, [label], 9578)["status"], "PASS")
        out = self.envelope(OFF, [label], 9578.001)
        self.assertEqual((out["problems"], out["success_envelope"]["limit_us"]), ([label], 9578))
        out = self.envelope("s1b_healthy_off", [label], 8700.001)
        self.assertEqual((out["problems"], out["success_envelope"]["limit_us"]), ([label], 8700))
        self.assertEqual(self.envelope("s1b_stage0_shadow_on", [label], 9616.001, (1, 0))["problems"], [label])
        self.assertEqual(self.envelope("s1b_stage0_shadow_on", [], 9616, (1, 0))["status"], "PASS")
        self.assertIsNone(self.envelope("s1b_invalid_status", [], 99999)["success_envelope"])

    def test_disagreement_with_the_unchanged_judge_is_rejected(self):
        with self.assertRaises(profiles.ProfileError):
            self.envelope(OFF, [], 8700.001)
        with self.assertRaises(profiles.ProfileError):
            self.envelope("s1b_healthy_on", [profiles.ENVELOPE_LABEL], 8000)

    def test_r9_scene5_passes_with_only_the_two_decisions(self):
        out, bins, log = retained(OFF)
        args = (OFF, self.sealed["scenes"][OFF], log, (bins / "uart_chip_0_0.log").read_text(),
                (out / "observations.txt").read_text(), self.sealed,
                json.loads((R9 / "system/progress.json").read_text())["references"]["0"])
        with profiles.r1_judge(None) as r1, self.assertRaises(ReferenceError):
            r1(*args)
        with profiles.r1_judge(profiles.log_gate(OFF, log, self.profile)) as r1:
            self.assertEqual(r1(*args)["problems"], ["Task1 device_dma_each budget exceeded",
                                                     profiles.ENVELOPE_LABEL])
        judged = profiles.judge(*args, self.record, self.profile)
        self.assertEqual((judged["status"], judged["problems"]), ("PASS", []))
        self.assertEqual(judged["success_envelope"]["entry_to_end_ps"], 8_906_494_000)
        self.assertTrue(judged["injected_task_budget"]["passed"])

    def test_freeze_is_write_once_and_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            binding = profiles.freeze(Path(tmp) / "profile", self.profile)
            loaded = profiles.load(binding)
            self.assertEqual({k: v for k, v in loaded.items() if k != "frozen_at"}, json.loads(json.dumps(self.profile)))
            with self.assertRaises(FileExistsError):
                profiles.freeze(Path(tmp) / "profile", self.profile)
            with self.assertRaises(profiles.ProfileError):
                profiles.load(dict(binding, sha256="0" * 64))
            os.chmod(binding["path"], 0o644)
            with self.assertRaises(profiles.ProfileError):
                profiles.load(binding)


if __name__ == "__main__":
    unittest.main()
