"""Run the original seven scenes with the write-once measured padded R7 reference.

Use --check-only for the offline preflight. Simulation belongs to Phase B.
"""
import argparse
import json
from pathlib import Path

import s1b_system_r5 as runner
from bingo_sdc_recover_reference import load_reference, reference_context, require_reference_tasks
from s1b_envelope_r7 import HEALTHY_LIMIT_US, healthy_rebase_record
from s1b_padded_rebind_r4 import validate_profile

W = Path(__file__).resolve().parents[1]
E = W / "evidence/chain_s1b_r7"
PRED = E / "profile/predictions.json"


def record_result(status, reason):
    # Explicitly requested local report; never stage or commit this Markdown.
    (W / "RESULT.md").write_text(
        f"Status: {status}\n\n{reason}\n\n"
        "Healthy envelope: 8700.000 us. G-release: max(0, latency - L_ref) <= 1 us per task. "
        "Cache warnings and Questa errors must both be zero. Other gates retain their frozen limits.\n"
        "Evidence: evidence/chain_s1b_r7/. No push or merge; RESULT.md must remain uncommitted.\n")


def preflight():
    if PRED.stat().st_mode & 0o222:
        raise ValueError("R7 frozen configuration must be read-only: STOP_USER")
    sealed = json.loads(PRED.read_text())
    validate_profile(sealed)
    budgets = sealed["budgets"]
    if (budgets["normal_all_healthy_envelope"] != HEALTHY_LIMIT_US
            or budgets["healthy_envelope_rebase"] != healthy_rebase_record()
            or budgets["normal_two_mismatch_envelope"] != 10640
            or budgets["failure_after_FAIL_return_to_completion_word"] != 2048
            or sealed["release_observation"]["threshold_ps"] != 1_000_000):
        raise ValueError("R7 gate decision differs from frozen limits: STOP_USER")
    tasks = [event["task"] for p in sealed["predictions"].values()
             for event in p["model"]["events"] if event["kind"] == "DISPATCH"]
    reference = sealed["release_reference"]
    context = reference_context(sealed)
    load_reference(reference, context)
    require_reference_tasks(reference, context, tasks)
    plan = runner.scene_plan(sealed)[1:]
    if len(plan) != 7 or sum(bool(s.get("recovery_negative")) for _, _, s, _ in plan) != 1:
        raise ValueError("Original six scenarios plus invalid-status probe changed: STOP_USER")
    return sealed


def main(check_only=False):
    sealed = preflight()
    if check_only:
        print(json.dumps(dict(status="PASS_OFFLINE_PREFLIGHT", simulations=0,
            L_ref_sha256=sealed["release_reference"]["sha256"],
            scenarios=list(sealed["scenes"]), healthy_envelope_us=HEALTHY_LIMIT_US)))
        return 0
    runner.E = E / "system"
    runner.ROOT = W / "results/chain_s1b_r7"
    runner.PRED = PRED
    code = runner.main(reference_binding=sealed["release_reference"])
    result = json.loads((runner.E / "result.json").read_text())
    progress = json.loads((runner.E / "progress.json").read_text())
    passed = (code == 0 and result["status"] == "PASS" and result["runs"] == 7
              and [r["name"] for r in progress["runs"]] == list(sealed["scenes"])
              and all(r["status"] == "PASS" and r["release"]["status"] == "PASS"
                      and r["warning_gate"] == dict(status="PASS", cache_warnings=0, questa_errors=0)
                      for r in progress["runs"]))
    if passed:
        record_result("DONE", "All six recovery scenarios and the invalid-status rejection probe passed all frozen gates.")
        return 0
    record_result("STOP_USER", result.get("reason") or "Incomplete or failed frozen scenario gates.")
    return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    try:
        code = main(parser.parse_args().check_only)
    except Exception as error:
        record_result("STOP_USER", f"R7 preflight or runner failed: {error!r}. No gate waiver or automatic retry.")
        print("STOP_USER", repr(error))
        code = 1
    raise SystemExit(code)
