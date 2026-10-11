"""Replay measured padded evidence and challenge the unchanged frozen gates offline."""
import copy
import json
from pathlib import Path
import re
import unittest

from s1b_envelope_r7 import (HEALTHY_LIMIT_US, healthy_rebase_record,
                            rebase_healthy_budget, success_envelope_us)
from s1b_reference_control import reference_scene
from s1b_system_r1 import judge, require_clean_product_log

W = Path(__file__).resolve().parents[1]


class JudgeR7Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sealed = json.loads((W / "evidence/GateS1bR3/profile/predictions.json").read_text())
        cls.sealed["budgets"] = rebase_healthy_budget(cls.sealed["budgets"])
        root = W / "results/GateS1bR3/s1b_reference_recovery_off"
        bins, = root.glob("task_*/bin")
        cls.log = (bins / "sim_run.log").read_text()
        cls.uart = (bins / "uart_chip_0_0.log").read_text()
        cls.observer = (root / "observations.txt").read_text()

    def replay(self, log=None, sealed=None):
        sealed = self.sealed if sealed is None else sealed
        return judge("s1b_healthy_on", reference_scene(sealed),
                     self.log if log is None else log, self.uart, self.observer,
                     sealed, reference_capture=True)

    def test_real_done_slots_match_cluster_core_model(self):
        result = self.replay()
        self.assertEqual(result["status"], "PASS", result["problems"])
        self.assertEqual(result["completion_ps"] - result["dispatch"][10], 8_613_922_000)
        self.assertEqual(result["warning_gate"], dict(status="PASS", cache_warnings=0, questa_errors=0))
        self.assertEqual(set(self.sealed["reference_prediction"]["done_by_slot"]),
                         {"0:0", "0:1", "0:2", "1:0", "1:1"})

    def test_old_chip_prefixed_keys_are_rejected(self):
        sealed = copy.deepcopy(self.sealed)
        rows = sealed["reference_prediction"]["done_by_slot"]
        sealed["reference_prediction"]["done_by_slot"] = {"0:" + k: v for k, v in rows.items()}
        self.assertIn("Per-slot DONE sequence differs from frozen tagged model", self.replay(sealed=sealed)["problems"])

    def test_reordered_done_is_rejected(self):
        sealed = copy.deepcopy(self.sealed)
        sealed["reference_prediction"]["done_by_slot"]["0:2"][:2] = [0, 10]
        self.assertIn("Per-slot DONE sequence differs from frozen tagged model", self.replay(sealed=sealed)["problems"])

    def test_chip_check_is_preserved_after_slot_fix(self):
        result = self.replay(self.log.replace("chip=0 task=10", "chip=1 task=10"))
        self.assertIn("Unexpected chip in recovery events", result["problems"])
        self.assertEqual(result["status"], "FAIL")

    def test_healthy_envelope_exact_boundary(self):
        entry = self.replay()["dispatch"][10]
        for extra_ps, expected in ((0, "PASS"), (1, "FAIL")):
            end = entry + HEALTHY_LIMIT_US * 1_000_000 + extra_ps
            log = re.sub(r"All chips finished successfully at \d+", f"All chips finished successfully at {end}", self.log)
            result = self.replay(log)
            self.assertEqual(result["status"], expected, result["problems"])

    def test_legacy_limit_reproduces_envelope_failure(self):
        sealed = copy.deepcopy(self.sealed)
        sealed["budgets"]["normal_all_healthy_envelope"] = 8592
        self.assertEqual(self.replay(sealed=sealed)["problems"], ["Frozen success envelope exceeded"])

    def test_only_healthy_gate_changes(self):
        old = json.loads((W / "evidence/GateS1bR3/profile/predictions.json").read_text())["budgets"]
        new = rebase_healthy_budget(old)
        self.assertEqual(old["normal_all_healthy_envelope"], 8592)
        restored = copy.deepcopy(new)
        restored["normal_all_healthy_envelope"] = 8592
        del restored["healthy_envelope_rebase"]
        self.assertEqual(restored, old)
        for mismatches, limit in ((0, 8700), (1, 9616), (2, 10640)):
            self.assertEqual(success_envelope_us(new, mismatches), limit)
        self.assertEqual(healthy_rebase_record()["exact_one_percent_target_us"], "8700.06122")
        self.assertEqual(healthy_rebase_record()["rounding_shortfall_us"], "0.06122")

    def test_cache_warnings_and_questa_errors_stop(self):
        for diagnostic in ("** Warning: [l1 dcache] reading invalid data on port 0",
                           "# ** Warning: (vsim-1) cache parity warning",
                           "# ** Error: (vsim-3000) invalid memory hierarchy",
                           "# Errors: 1, Warnings: 0"):
            with self.assertRaisesRegex(ValueError, "STOP_USER"):
                self.replay(self.log + "\n" + diagnostic)
        # The finite rejection probe requires the existing expected fatal exit,
        # which is distinct from a Questa Error diagnostic.
        self.assertEqual(require_clean_product_log("# ** Fatal: All chips finished with errors"),
                         dict(status="PASS", cache_warnings=0, questa_errors=0))


if __name__ == "__main__":
    unittest.main()
