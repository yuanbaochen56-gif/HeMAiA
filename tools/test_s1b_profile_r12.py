"""Offline tests for the R12 r10 gate profile. No simulation."""
import copy
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test"), str(W / "util/automation_scripts")]
from bingo_sdc_recover_reference import reference_context
import s1b_injected_budget_r9 as budget
import s1b_profile_r10 as r10
import s1b_profile_r11 as r11
import s1b_profile_r12 as profiles
import s1b_reference_r8 as r8
from s1b_system_r1 import sha

PRED = W / "evidence/chain_s1b_r7/profile/predictions.json"
R10 = W / "evidence/GateS1bR10"
R11 = W / "evidence/GateS1bR11"
PROBE = profiles.FAIL2_SCENE
REUSED = {"s1b_healthy_off": W / "evidence/GateS1bR8", "s1b_healthy_on": W / "evidence/GateS1bR8",
          "s1b_stage0_primary_on": W / "evidence/GateS1bR9", "s1b_stage1_primary_on": W / "evidence/GateS1bR9",
          "s1b_stage0_primary_off": R10, "s1b_stage0_shadow_on": R11}
COMPLETION_LINE = "# S1B_COMPLETION_WRITTEN time=7120853000 word=5"


class GateProfileR10Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sealed = json.loads(PRED.read_text())
        state = json.loads((R11 / "system/progress.json").read_text())
        r10_state = json.loads((R10 / "system/progress.json").read_text())
        cls.bindings = {int(k): v for k, v in state["references"].items()}
        cls.record = budget.load(state["budgets"])
        cls.frozen_r9 = r11.load(state["gate_profile"])
        frozen_r8 = r10.load(r10_state["gate_profile"])
        r8_profile = r10.derive(cls.sealed, dict(path=str(PRED), sha256=sha(PRED)), state["budgets"], cls.record)
        cls.r9_profile = r11.derive(cls.sealed, r8_profile, frozen_r8, cls.bindings,
                                    reference_context(cls.sealed), cls.record)
        cls.profile = cls.derive()
        out = Path(state["runs"][1]["directory"])
        bins, = out.glob("task_*/bin")
        cls.log, cls.uart = (bins / "sim_run.log").read_text(), (bins / "uart_chip_0_0.log").read_text()
        cls.probe_r11 = cls.judge(r11.judge, PROBE, R11, cls.frozen_r9)

    @classmethod
    def derive(cls, sealed=None, frozen_r9=None):
        return profiles.derive(sealed or cls.sealed, cls.r9_profile, frozen_r9 or cls.frozen_r9)

    @classmethod
    def judge(cls, function, name, evidence, profile):
        out = Path(next(run for run in json.loads((evidence / "system/progress.json").read_text())["runs"]
                        if run["name"] == name)["directory"])
        bins, = out.glob("task_*/bin")
        scene = cls.sealed["scenes"][name]
        reference = r8.select_reference(cls.bindings, scene, cls.sealed["predictions"][name]["cfg"])
        return function(name, scene, (bins / "sim_run.log").read_text(), (bins / "uart_chip_0_0.log").read_text(),
                        (out / "observations.txt").read_text(), cls.sealed, reference, cls.record, profile)

    def fail2(self, log=None, uart=None):
        """The decision on the retained probe judgement, with the r1 FAIL2 problem recomputed for log/uart."""
        log, uart = log or self.log, uart or self.uart
        judged = copy.deepcopy(self.probe_r11)
        judged["problems"] = [p for p in judged["problems"] if p != profiles.FAIL2_LABEL]
        if profiles.FAIL2_UART not in uart or not re.search(profiles.STATUS5, log):
            judged["problems"].append(profiles.FAIL2_LABEL)
        return profiles.fail2(judged, PROBE, log, uart, self.profile)

    def test_profile_is_r9_plus_one_decision(self):
        p = self.profile
        added = {"fail2_completion_word", "r9_user_decision", "r9_profile_binding"}
        self.assertEqual(set(p), set(self.frozen_r9) - {"frozen_at"} | added)
        for key in set(self.frozen_r9) - {"frozen_at", "label", "user_decision", "decisions"}:
            self.assertEqual(p[key], self.frozen_r9[key], key)
        self.assertEqual(p["label"], "r10_gate_profile")
        self.assertEqual(p["r9_user_decision"], self.frozen_r9["user_decision"])
        self.assertEqual({k: v for k, v in p["decisions"].items() if k != "fail2_completion_word"},
                         self.frozen_r9["decisions"])
        rule = p["fail2_completion_word"]
        self.assertEqual((rule["scene"], rule["return_code"], rule["completion_word"]), (PROBE, 2, 5))
        self.assertEqual(rule["start_s"]["sha256"], profiles.START_S_SHA)
        self.assertEqual(rule["checker"]["sha256"], profiles.CHECKER_SHA)

    def test_derive_refuses_anything_else(self):
        frozen = copy.deepcopy(self.frozen_r9)
        frozen["release_injected_predecessor"]["cap_ps"] = 2_000_000
        with self.assertRaises(profiles.ProfileError):
            self.derive(frozen_r9=frozen)
        for change in (lambda s: s["negative_probe"]["expected"].update(return_code=3),
                       lambda s: s["predictions"][PROBE]["model"].update(completion_word=7),
                       lambda s: s["scenes"]["s1b_healthy_on"].update(recovery_negative=True),
                       lambda s: s["source_hashes"].update({profiles.START_S: "0" * 64}),
                       lambda s: s["source_hashes"].update({profiles.CHECKER: "0" * 64})):
            sealed = copy.deepcopy(self.sealed)
            change(sealed)
            with self.assertRaises(profiles.ProfileError):
                self.derive(sealed=sealed)

    def test_encoding_is_start_s_exit(self):
        self.assertEqual([profiles.encode(code) for code in (0, 1, 2)], [1, 3, 5])

    def test_retained_r11_probe_passes_with_only_fail2_changed(self):
        old = self.probe_r11
        self.assertEqual((old["status"], old["problems"]), ("FAIL", [profiles.FAIL2_LABEL]))
        new = self.judge(profiles.judge, PROBE, R11, self.profile)
        self.assertEqual((new["status"], new["problems"]), ("PASS", []))
        evidence = new["fail2_evidence"]
        self.assertEqual((evidence["completion_word"], evidence["decoded_return_code"], evidence["status5_in_log"],
                          evidence["uart_line_present"], evidence["passed"]), (5, 2, True, False, True))
        self.assertEqual(evidence["completion_word_ps"], 7_120_853_000)
        for key in set(old) | set(new):
            if key not in ("status", "problems", "fail2_evidence"):
                self.assertEqual(new.get(key), old.get(key), key)

    def test_other_completion_words_fail(self):
        for word in (1, 3, 4, 7):
            out = self.fail2(log=self.log.replace(COMPLETION_LINE, COMPLETION_LINE[:-1] + str(word)))
            self.assertEqual((out["status"], out["problems"]), ("FAIL", [profiles.FAIL2_LABEL]), word)
        out = self.fail2(log=self.log.replace(COMPLETION_LINE, COMPLETION_LINE + "\n" + COMPLETION_LINE))
        self.assertEqual(out["status"], "FAIL")
        out = self.fail2(log=self.log.replace(COMPLETION_LINE, "#"))
        self.assertEqual((out["status"], out["fail2_evidence"]["completion_word"]), ("FAIL", None))

    def test_log_half_is_kept(self):
        log = self.log.replace("finished with status 5 at", "finished with status 50 at")
        out = self.fail2(log=log)
        self.assertEqual((out["status"], out["problems"]), ("FAIL", [profiles.FAIL2_LABEL]))
        self.assertTrue(out["fail2_evidence"]["completion_word_observed"])

    def test_uart_line_alone_no_longer_suffices(self):
        uart = self.uart + "Chip(0, 0): [Host] Offload Finish with ret = 2\r\n"
        out = self.fail2(uart=uart, log=self.log.replace(COMPLETION_LINE, "#"))
        self.assertEqual(out["status"], "FAIL")
        self.assertEqual(self.fail2(uart=uart)["status"], "PASS")

    def test_other_problems_are_kept(self):
        judged = copy.deepcopy(self.probe_r11)
        judged["problems"].insert(0, "Consumer released after failure")
        out = profiles.fail2(judged, PROBE, self.log, self.uart, self.profile)
        self.assertEqual((out["status"], out["problems"]), ("FAIL", ["Consumer released after failure"]))

    def test_disagreement_with_the_unchanged_judge_is_rejected(self):
        judged = copy.deepcopy(self.probe_r11)
        judged["problems"] = []
        with self.assertRaises(profiles.ProfileError):
            profiles.fail2(judged, PROBE, self.log, self.uart, self.profile)

    def test_other_scenes_are_untouched(self):
        judged = copy.deepcopy(self.probe_r11)
        out = profiles.fail2(judged, "s1b_stage0_primary_off", self.log, self.uart, self.profile)
        self.assertIsNone(out["fail2_evidence"])
        self.assertEqual((out["status"], out["problems"]), (judged["status"], judged["problems"]))

    def test_reused_runs_keep_their_verdict(self):
        for name, evidence in REUSED.items():
            old = self.judge(r11.judge, name, evidence, self.frozen_r9)
            new = self.judge(profiles.judge, name, evidence, self.profile)
            self.assertIsNone(new["fail2_evidence"], name)
            self.assertEqual((new["status"], new["problems"]), ("PASS", []), name)
            self.assertEqual({k: v for k, v in new.items() if k != "fail2_evidence"}, old, name)

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
                r11.load(binding)
            os.chmod(binding["path"], 0o644)
            with self.assertRaises(profiles.ProfileError):
                profiles.load(binding)


class RetainedRejudgeTest(unittest.TestCase):
    """The R12 re-judge of the sealed R11 probe run, against a profile and plan frozen in a temporary directory."""

    @classmethod
    def setUpClass(cls):
        import s1b_release_r12 as release
        cls.release, cls.saved = release, release.E
        cls.tmp = tempfile.TemporaryDirectory()
        release.E = Path(cls.tmp.name) / "GateS1bR12"
        (cls.sealed, configs, reused8, reused9, reused10, cls.reused11,
         cls.bindings, cls.record, profile) = release.preflight()
        cls.budgets = reused9["budgets"]
        cls.profile = profiles.freeze(release.E / "profile", profile)
        cls.plan = release.freeze_plan(cls.sealed, configs, {}, (reused8, reused9, reused10, cls.reused11),
                                       cls.bindings, cls.profile, {})
        cls.seeds = release.s1b_release_r9.seed_records(
            reused8["runs"] + reused9["runs"] + reused10["runs"] + cls.reused11["runs"])

    @classmethod
    def tearDownClass(cls):
        cls.release.E = cls.saved
        cls.tmp.cleanup()

    def rejudge(self, profile=None, plan=None):
        return self.release.rejudge_retained(self.sealed, self.bindings, self.record, profile or self.profile,
                                             plan or self.plan, self.budgets, self.reused11["retained"], self.seeds)

    def test_r11_inputs_are_the_sealed_stop(self):
        retained = self.reused11["retained"]
        self.assertEqual((retained["name"], retained["status"], retained["problems"]),
                         (PROBE, "FAIL", [profiles.FAIL2_LABEL]))
        self.assertEqual([run["name"] for run in self.reused11["runs"]], ["s1b_stage0_shadow_on"])

    def test_rejudge_changes_only_the_fail2_evidence(self):
        new = self.rejudge()
        self.assertEqual((new["status"], new["problems"]), ("PASS", []))
        retained = self.reused11["retained"]
        self.assertEqual({k for k in set(new) | set(retained) if new.get(k) != retained.get(k)},
                         {"status", "problems", "fail2_evidence"})

    def test_rejudge_rechecks_the_frozen_profile(self):
        with self.assertRaises(profiles.ProfileError):
            self.rejudge(profile=dict(self.profile, sha256="0" * 64))
        os.chmod(self.profile["path"], 0o644)
        try:
            with self.assertRaises(profiles.ProfileError):
                self.rejudge()
        finally:
            os.chmod(self.profile["path"], 0o444)

    def test_rejudge_rechecks_the_frozen_plan(self):
        with self.assertRaises(AssertionError):
            self.rejudge(plan=dict(self.plan, sha256="0" * 64))


if __name__ == "__main__":
    unittest.main()
