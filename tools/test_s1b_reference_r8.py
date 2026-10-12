"""Offline checks for per-configuration L_ref routing, replaying the real r7 evidence."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(W / "target/sim/automation/test"))
import bingo_sdc_recover_check
import bingo_sdc_recover_reference as base
from bingo_sdc_recover_reference import ReferenceError, reference_context
import s1b_reference_r8 as r8
from s1b_reference_control import reference_probe
import s1b_system_r1
from s1b_system_r1 import judge

PRED = W / "evidence/chain_s1b_r7/profile/predictions.json"
OFF = W / "results/chain_s1b_r7/s1b_healthy_off"


class PerConfigurationReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sealed = json.loads(PRED.read_text())
        cls.context = reference_context(cls.sealed)
        cls.sdc_on = dict(cls.sealed["release_reference"], sdc_enable=1)
        cls.off = json.loads((OFF / "judgement.json").read_text())
        bins, = OFF.glob("task_*/bin")
        cls.log = (bins / "sim_run.log").read_text()
        cls.uart = (bins / "uart_chip_0_0.log").read_text()
        cls.observer = (OFF / "observations.txt").read_text()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.provenance = dict(sdc_enable=0, recovery_enable=0, fault_tx=None, negative=False,
                               recovery_runs_started=0, gates_status="PASS")

    def observed(self, shift_task15=0):
        eligibility = dict(self.off["readiness"]["eligibility"])
        dispatch = {int(k): v for k, v in self.off["readiness"]["dispatch"].items()}
        dispatch[15] += shift_task15
        return eligibility, dispatch

    def freeze_off(self, name="reference", provenance=None):
        eligibility, dispatch = self.observed()
        return r8.freeze_reference(Path(self.temp.name) / name, eligibility, dispatch, self.context,
                                   self.provenance if provenance is None else provenance)

    def test_r7_failure_reproduced_against_sdc_on_reference(self):
        result = r8.compare_release(*self.observed(), self.sdc_on, self.context)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["configuration"], "sdc_on_recovery_off")
        self.assertEqual(result["tasks"]["15"]["extra_ps"], 7_756_000)
        self.assertEqual([t for t, row in result["tasks"].items() if row["extra_ps"]], ["15"])
        self.assertEqual(result["tasks"], self.off["release"]["tasks"])

    def test_sdc_on_path_is_the_unchanged_r7_function(self):
        ours = r8.compare_release(*self.observed(), self.sdc_on, self.context)
        theirs = base.compare_release(*self.observed(), self.sdc_on, self.context)
        self.assertEqual({k: v for k, v in ours.items() if k != "configuration"}, theirs)

    def test_sdc_off_reference_keeps_one_microsecond_cap_on_host_exit(self):
        binding = self.freeze_off()
        self.assertEqual(binding["sdc_enable"], 0)
        for shift, status in ((0, "PASS"), (1_000_000, "PASS"), (1_000_001, "FAIL")):
            result = r8.compare_release(*self.observed(shift), binding, self.context)
            self.assertEqual(result["status"], status)
            self.assertEqual(result["tasks"]["15"]["extra_ps"], shift)
            self.assertEqual(result["cap_ps"], 1_000_000)
            self.assertEqual(result["configuration"], "sdc_off_recovery_off")

    def test_sdc_off_freeze_is_write_once_and_read_only(self):
        binding = self.freeze_off()
        for key in ("path", "hash_path"):
            self.assertEqual(Path(binding[key]).stat().st_mode & 0o777, 0o444)
        with self.assertRaises(FileExistsError):
            self.freeze_off()
        Path(binding["path"]).chmod(0o644)
        with self.assertRaisesRegex(ReferenceError, "read-only"):
            r8.load_reference(binding, self.context)
        Path(binding["path"]).write_text("{}")
        with self.assertRaisesRegex(ReferenceError, "SHA256 mismatch"):
            r8.load_reference(binding, self.context)

    def test_invalid_sdc_off_provenance_is_rejected(self):
        for change in (dict(recovery_enable=1), dict(recovery_runs_started=1), dict(fault_tx=0),
                       dict(negative=True), dict(gates_status="FAIL"), dict(sdc_enable=2)):
            with self.subTest(change=change), self.assertRaises(ReferenceError):
                self.freeze_off(name=str(change), provenance=dict(self.provenance, **change))

    def test_declared_configuration_must_match_provenance(self):
        off = self.freeze_off()
        with self.assertRaises(ReferenceError):
            r8.load_reference(dict(off, sdc_enable=1), self.context)
        with self.assertRaisesRegex(ReferenceError, "declared configuration"):
            r8.load_reference(dict(self.sdc_on, sdc_enable=0), self.context)
        with self.assertRaisesRegex(ReferenceError, "no SDC configuration"):
            r8.load_reference(self.sealed["release_reference"], self.context)

    def test_context_mismatch_is_rejected(self):
        off = self.freeze_off()
        context = copy.deepcopy(self.context)
        context["image_id"] = "0" * 64
        with self.assertRaisesRegex(ReferenceError, "does not match"):
            r8.load_reference(off, context)

    def test_scene_selection(self):
        scenes, predictions = self.sealed["scenes"], self.sealed["predictions"]
        configs = {name: r8.scene_configuration(scene, predictions[name]["cfg"])
                   for name, scene in scenes.items()}
        self.assertEqual(sorted(n for n, c in configs.items() if c == 0),
                         ["s1b_healthy_off", "s1b_stage0_primary_off"])
        off_scene, off_cfg = scenes["s1b_healthy_off"], predictions["s1b_healthy_off"]["cfg"]
        with self.assertRaisesRegex(ReferenceError, "not frozen before scenario launch"):
            r8.select_reference({1: self.sdc_on}, off_scene, off_cfg)
        with self.assertRaisesRegex(ReferenceError, "ambiguous"):
            r8.select_reference({1: self.sdc_on}, off_scene, dict(off_cfg, sdc_enable=1))
        with self.assertRaisesRegex(ReferenceError, "differs"):
            r8.select_reference({0: r8.capture_binding(0)}, off_scene, off_cfg)
        bindings = {1: self.sdc_on, 0: self.freeze_off()}
        on_scene, on_cfg = scenes["s1b_healthy_on"], predictions["s1b_healthy_on"]["cfg"]
        self.assertIs(r8.select_reference(bindings, on_scene, on_cfg), self.sdc_on)
        self.assertIs(r8.select_reference(bindings, off_scene, off_cfg), bindings[0])
        r8.require_reference_tasks(bindings[0], self.context,
            [e["task"] for e in predictions["s1b_stage0_primary_off"]["model"]["events"] if e["kind"] == "DISPATCH"])
        with self.assertRaisesRegex(ReferenceError, r"Missing reference tasks \['2', '3', '6', '7'\]"):
            r8.require_reference_tasks(bindings[0], self.context, range(16))

    def test_capture_binding_only_validates_the_executing_set(self):
        result = r8.compare_release(*self.observed(), r8.capture_binding(0), self.context)
        self.assertEqual(result["mode"], r8.CAPTURE_MODE)
        self.assertEqual(result["tasks"]["15"]["latency_ps"], 872_676_000)
        eligibility, dispatch = self.observed()
        del dispatch[15]
        with self.assertRaisesRegex(ReferenceError, "exact executing set"):
            r8.compare_release(eligibility, dispatch, r8.capture_binding(0), self.context)
        with self.assertRaises(ReferenceError):
            r8.capture_binding(1)
        with self.assertRaisesRegex(ReferenceError, "Invalid reference capture"):
            r8.compare_release(*self.observed(), dict(capture="x", sdc_enable=0), self.context)

    def patch_judge(self):
        saved = bingo_sdc_recover_check.compare_release, s1b_system_r1.compare_release
        bingo_sdc_recover_check.compare_release = s1b_system_r1.compare_release = r8.compare_release
        self.addCleanup(lambda: (setattr(bingo_sdc_recover_check, "compare_release", saved[0]),
                                 setattr(s1b_system_r1, "compare_release", saved[1])))

    def replay(self, binding):
        return judge("s1b_healthy_off", self.sealed["scenes"]["s1b_healthy_off"], self.log, self.uart,
                     self.observer, self.sealed, binding)

    def test_judge_replay_with_each_reference(self):
        self.patch_judge()
        old = self.replay(self.sdc_on)
        self.assertEqual(old["status"], "FAIL")
        self.assertEqual(len(old["problems"]), 1)
        self.assertTrue(old["problems"][0].startswith("G-release extra exceeds 1us"))
        captured = self.replay(r8.capture_binding(0))
        self.assertEqual(captured["status"], "PASS", captured["problems"])
        self.assertEqual(captured["release"]["mode"], r8.CAPTURE_MODE)
        matched = self.replay(self.freeze_off())
        self.assertEqual(matched["status"], "PASS", matched["problems"])
        self.assertEqual(max(r["extra_ps"] for r in matched["release"]["tasks"].values()), 0)

    def test_judge_replay_still_fails_other_gates(self):
        self.patch_judge()
        result = judge("s1b_healthy_off", self.sealed["scenes"]["s1b_healthy_off"],
                       self.log.replace("chip=0 task=10", "chip=1 task=10"), self.uart,
                       self.observer, self.sealed, self.freeze_off())
        self.assertIn("Unexpected chip in recovery events", result["problems"])

    def control_log(self, value="ffffffff00000000", stamp=5141400000, extra=""):
        return (f"# S1B_REFERENCE_TRIGGER time={stamp}\n"
                f"# S1B_REFERENCE_SDC_OFF time={stamp} tx=0 value={value}\n"
                f"# S1B_REFERENCE_SDC_OFF time={stamp} tx=1 value={value}\n" + extra)

    def check_control(self, log, dispatch=None):
        dispatch = dispatch or {int(k): v for k, v in self.off["dispatch"].items()}
        done = {int(k): v for k, v in self.off["done"].items()}
        return r8.check_sdc_off_control(log, self.sealed, reference_probe(self.sealed), dispatch, done)

    def test_read_only_sdc_off_control(self):
        tcl = r8.sdc_off_control_tcl(reference_probe(self.sealed))
        self.assertNotIn("force", tcl)
        self.assertNotIn("deposit", tcl)
        self.assertEqual(tcl.count("ffffffff00000000"), 2)
        self.assertEqual(self.check_control(self.control_log())["deposits"], 0)
        for log in (self.control_log(value="ffffffff00000001"),
                    self.control_log(stamp=5141645000),
                    self.control_log(stamp=5141300000),
                    self.control_log(extra="# S1B_REFERENCE_OFF time=1 tx=0 before=a after=b\n"),
                    self.control_log().replace("tx=1", "tx=0")):
            with self.subTest(log=log), self.assertRaises(ReferenceError):
                self.check_control(log)
        dispatch = {int(k): v for k, v in self.off["dispatch"].items()}
        with self.assertRaisesRegex(ReferenceError, "shadow or compare"):
            self.check_control(self.control_log(), {**dispatch, 2: 5141500000})


if __name__ == "__main__":
    unittest.main()
