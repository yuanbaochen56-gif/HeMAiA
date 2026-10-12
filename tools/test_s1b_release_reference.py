import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(W / "target/sim/automation/test"))
from bingo_sdc_recover_reference import (freeze_reference, compare_release, load_reference,
    require_reference_tasks, absolute_latencies, ReferenceError, METRIC)
from s1b_observations import readiness
from s1b_system_r5 import scene_plan, require_clean_product_log


class FrozenReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.context = dict(graph={"same": "graph"}, metric=METRIC)
        self.provenance = dict(sdc_enable=1, recovery_enable=0, fault_tx=None, negative=False,
                               recovery_runs_started=0, gates_status="PASS")
        self.path = Path(self.temp.name) / "reference"
        self.binding = freeze_reference(self.path, {"10": 0}, {10: 12432000},
                                        self.context, self.provenance)

    def compare(self, latency):
        return compare_release({"10": 0}, {10: latency}, self.binding, self.context)

    def test_absolute_over_1us_passes_if_extra_within_cap(self):
        r = self.compare(13432000)
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["tasks"]["10"], dict(eligible_ps=0, dispatch_ps=13432000,
            latency_ps=13432000, L_ref_ps=12432000, extra_ps=1000000, **{"pass": True}))

    def test_extra_greater_than_1us_fails(self):
        r = self.compare(13432001)
        self.assertEqual(r["status"], "FAIL")
        self.assertEqual(r["tasks"]["10"]["extra_ps"], 1000001)

    def test_faster_run_reports_zero_extra(self):
        r = self.compare(12000000)
        self.assertEqual(r["tasks"]["10"]["extra_ps"], 0)
        self.assertEqual(r["tasks"]["10"]["latency_ps"], 12000000)

    def test_tampered_file_fails_before_json_parse(self):
        p = Path(self.binding["path"]); p.chmod(0o600); p.write_text("not JSON")
        with self.assertRaisesRegex(ReferenceError, "SHA256 mismatch"):
            self.compare(12432000)

    def test_tampered_sidecar_fails(self):
        p = Path(self.binding["hash_path"]); p.chmod(0o600); p.write_text("0"*64)
        with self.assertRaisesRegex(ReferenceError, "SHA256 mismatch"):
            self.compare(12432000)

    def test_missing_reference_task_stops(self):
        with self.assertRaisesRegex(ReferenceError, "Missing reference task 2.*STOP_USER"):
            compare_release({"2": 0}, {2: 28}, self.binding, self.context)
        with self.assertRaisesRegex(ReferenceError, "Missing reference tasks.*STOP_USER"):
            require_reference_tasks(self.binding, self.context, [10, 2])

    def test_refreeze_is_forbidden(self):
        original = Path(self.binding["path"]).read_bytes()
        with self.assertRaises(FileExistsError):
            freeze_reference(self.path, {"10": 0}, {10: 999}, self.context, self.provenance)
        self.assertEqual(Path(self.binding["path"]).read_bytes(), original)

    def test_changed_graph_platform_or_metric_stops(self):
        for key in ("graph", "metric"):
            bad = copy.deepcopy(self.context); bad[key] = "changed"
            with self.assertRaises(ReferenceError):
                load_reference(self.binding, bad)

    def test_only_clean_pre_recovery_disabled_capture_allowed(self):
        for key, value in (("sdc_enable", 0), ("recovery_enable", 1), ("fault_tx", 0), ("negative", True),
                           ("recovery_runs_started", 1), ("gates_status", "FAIL")):
            bad = dict(self.provenance); bad[key] = value
            path = Path(self.temp.name) / key
            with self.assertRaises(ReferenceError):
                freeze_reference(path, {"10": 0}, {10: 28}, self.context, bad)
            self.assertFalse(path.exists())

    def test_invalid_or_missing_readiness_stops(self):
        for eligibility, dispatch in (({}, {10: 1}), ({"10": 2}, {10: 1}),
                                      ({"10": None}, {10: 1}), ({}, {})):
            with self.assertRaises(ReferenceError):
                absolute_latencies(eligibility, dispatch)

    def test_late_ingress_check_does_not_move_real_readiness(self):
        rows = [dict(task=10, cluster=0, core=2, check_en=0, type=0, cond=0),
                dict(task=0, cluster=0, core=1, check_en=1, check_code=4,
                     check_tag=0, type=0, cond=0)]
        text = ("0 START\n28 DISPATCH 10 0 2\n56 DONE 10 0 2\n84 SET 0 2 2 0\n"
                "2000084 CHECK 0 0 1 4 0\n2000112 DISPATCH 0 0 1\n")
        r = readiness(text, rows, 28)
        self.assertEqual(r["eligibility"]["0"], 84)
        self.assertEqual(r["waits_ps"]["0"], 2000028)

    def test_HOL_wait_remains_in_absolute_latency(self):
        rows = [dict(task=i, cluster=0, core=2, check_en=0, type=0, cond=0) for i in (10, 0)]
        r = readiness("0 START\n28 DISPATCH 10 0 2\n56 DONE 10 0 2\n"
                      "2000112 DISPATCH 0 0 2\n", rows, 28)
        self.assertEqual(r["eligibility"]["0"], 56)
        self.assertEqual(r["waits_ps"]["0"], 2000056)

    def test_runner_captures_separate_reference_first(self):
        sealed = json.loads((W / "evidence/PredictionsS1bR4/predictions.json").read_text())
        plan = scene_plan(sealed)
        self.assertEqual(plan[0][:2], ("s1b_reference_recovery_off", "s1b_healthy_on"))
        self.assertEqual(plan[0][2]["sdc_enable"], 1)
        self.assertEqual(plan[0][2]["recovery_enable"], 0)
        self.assertTrue(plan[0][3]); self.assertFalse(any(r[3] for r in plan[1:]))
        self.assertEqual([r[0] for r in plan[1:]], list(sealed["scenes"]))

    def test_existing_disabled_reference_is_missing_four_enabled_tasks(self):
        sealed = json.loads((W / "evidence/PredictionsS1bR4/predictions.json").read_text())
        def executing(name):
            return {e["task"] for e in sealed["predictions"][name]["model"]["events"]
                    if e["kind"] == "DISPATCH"}
        self.assertEqual(executing("s1b_healthy_on")-executing("s1b_healthy_off"), {2, 3, 6, 7})

    def test_product_cache_warning_stops(self):
        with self.assertRaisesRegex(ReferenceError, "Product WT.*STOP_USER"):
            require_clean_product_log("** Warning: [l1 dcache] reading invalid data on port 1")

    def test_frozen_files_are_read_only(self):
        for key in ("path", "hash_path"):
            self.assertEqual(Path(self.binding[key]).stat().st_mode & 0o777, 0o444)

    def test_writable_reference_stops_even_when_hash_matches(self):
        for key in ("path", "hash_path"):
            path = Path(self.binding[key]); path.chmod(0o644)
            with self.assertRaisesRegex(ReferenceError, "read-only"):
                load_reference(self.binding, self.context)
            path.chmod(0o444)

    def test_missing_or_boolean_recovery_control_is_rejected(self):
        for key, value in (("sdc_enable", True), ("recovery_enable", False),
                           ("recovery_runs_started", False)):
            bad = dict(self.provenance); bad[key] = value
            with self.assertRaises(ReferenceError):
                freeze_reference(Path(self.temp.name)/key, {"10": 0}, {10: 28}, self.context, bad)
        bad = dict(self.provenance); del bad["recovery_enable"]
        with self.assertRaises(ReferenceError):
            freeze_reference(Path(self.temp.name)/"missing", {"10": 0}, {10: 28}, self.context, bad)


if __name__ == "__main__":
    unittest.main()
