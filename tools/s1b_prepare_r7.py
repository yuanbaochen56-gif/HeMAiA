"""Offline adjudication and write-once freeze of the user-selected padded reference.

Reads existing simulator artifacts only. Never launches a build or simulation.
Historical evidence stays unchanged; all new artifacts live in chain_s1b_r7.
"""
import copy
import datetime
import json
from pathlib import Path

from bingo_sdc_recover_reference import (freeze_reference, reference_context,
                                         require_reference_tasks)
from s1b_envelope_r7 import MEASURED_REFERENCE_PS, rebase_healthy_budget
from s1b_padded_rebind_r4 import fingerprint, validate_profile
from s1b_reference_control import check_reference_control, reference_scene
from s1b_system_r1 import finite_trace, judge, sha, write

W = Path(__file__).resolve().parents[1]
E = W / "evidence/chain_s1b_r7"
PRIOR = W / "evidence/GateS1bR3/profile/predictions.json"
CAPTURE = W / "results/GateS1bR3/s1b_reference_recovery_off"
CHANGED_CAPTURE_TOOLS = {str(W / "tools" / name) for name in
                         ("s1b_system_r1.py", "s1b_system_r5.py")}


def prepare():
    before = json.loads((E / "before.json").read_text())
    assert sha(PRIOR) == before["evidence"][str(PRIOR)]
    prior = json.loads(PRIOR.read_text())
    sealed = copy.deepcopy(prior)
    changes = []
    # Every physical input must still match the capture. Only the reviewed
    # Python judge/runner edits may differ from the old tooling seal.
    for path, expected in prior["bindings"].items():
        actual = sha(path)
        if actual != expected:
            assert path in CHANGED_CAPTURE_TOOLS, ("Unapproved capture input changed", path)
            assert before["tools"][str(Path(path).relative_to(W))] == expected
            sealed["bindings"][path] = actual
            changes.append(dict(path=path, before=expected, after=actual))
    for path, expected in prior["source_hashes"].items():
        actual = sha(W / path)
        if actual != expected:
            assert str(W / path) in CHANGED_CAPTURE_TOOLS, ("Physical source changed", path)
            assert before["tools"][path] == expected
            sealed["source_hashes"][path] = actual
    assert {row["path"] for row in changes} == CHANGED_CAPTURE_TOOLS
    for path, expected in before["evidence"].items():
        assert sha(path) == expected, ("Historical evidence/image changed", path)
    build = json.loads(Path(prior["image_gate"]["path"]).read_text())
    assert sha(prior["image_gate"]["path"]) == prior["image_gate"]["sha256"]
    assert sha(prior["preparation_gate"]["path"]) == prior["preparation_gate"]["sha256"]
    assert build["status"] == "PASS" and len(build["images"]) == 18
    assert build["exact_comparison"]["byte_comparison"] is True
    assert all(row["default_byte_identical"] for row in build["images"][:-1])
    for row in build["images"]:
        image = Path(row["output"]) / "test_cfg_unpatched.bin"
        assert sha(image) == row["image_id"] == before["evidence"][str(image)]
        sealed["bindings"][str(image)] = sha(image)
    assert prior["default_image_id"] == before["default_image_id"]
    assert prior["image"]["image_id"] == before["padded_image_id"]
    sealed["budgets"] = rebase_healthy_budget(prior["budgets"])
    for name in ("s1b_system_r1.py", "s1b_system_r5.py", "s1b_envelope_r7.py",
                 "s1b_release_r7.py", "s1b_prepare_r7.py", "s1b_freeze_r1.py",
                 "test_s1b_judge_r7.py"):
        path = W / "tools" / name
        sealed["bindings"][str(path)] = sha(path)
    sealed["profile_fingerprint_sha256"] = fingerprint(sealed)
    validate_profile(sealed)
    bins, = CAPTURE.glob("task_*/bin")
    log_path, uart_path = bins / "sim_run.log", bins / "uart_chip_0_0.log"
    observer_path = CAPTURE / "observations.txt"
    record_path = CAPTURE / "test_cfg.json"
    record = json.loads(record_path.read_text())
    assert record["image_id"] == sealed["image"]["image_id"]
    assert record["configuration"] == sealed["reference_prediction"]["cfg"]
    log = log_path.read_text()
    result = judge("s1b_healthy_on", reference_scene(sealed), log,
                   uart_path.read_text(), observer_path.read_text(), sealed,
                   reference_capture=True)
    assert result["status"] == "PASS", ("Frozen reference gate failed: STOP_USER", result["problems"])
    result["finite_trace"] = finite_trace(reference_scene(sealed), log, bins, sealed)
    result["reference_control"] = check_reference_control(log, sealed, result["dispatch"], result["done"])
    edges = [int(line) for line in (bins / "clock_observations.txt").read_text().splitlines()]
    assert len(edges) == 4
    assert all(b - a == sealed["clocks"]["manager_period_ps"] for a, b in zip(edges, edges[1:]))
    assert result["completion_ps"] - result["dispatch"][10] == MEASURED_REFERENCE_PS
    result.update(actual_manager_edges_ps=edges, image_id=record["image_id"],
                  existing_capture=str(CAPTURE), adjudication="OFFLINE_REPLAY_OF_EXISTING_PADDED_RUN",
                  healthy_envelope_rebase=sealed["budgets"]["healthy_envelope_rebase"])
    # The old runner stopped before freezing L_ref or starting any recovery
    # scene. Check the recorded count and actual output directory population.
    progress = json.loads((W / "evidence/GateS1bR3/system/progress.json").read_text())
    assert progress["runs"] == [] and progress["simulation_started"] == 1 and progress["reference_capture"]
    assert sorted(p.name for p in CAPTURE.parent.iterdir()) == [CAPTURE.name]
    assert not (E / "system").exists() and not (W / "results/chain_s1b_r7").exists()
    profile = E / "profile"
    profile.mkdir()
    adjudication_path = E / "reference_adjudication.json"
    write(adjudication_path, result)
    adjudication_path.chmod(0o444)
    trace, = bins.glob("logs/trace_chip_00_hart_00000.log")
    capture_paths = [log_path, uart_path, observer_path, record_path,
                     bins / "clock_observations.txt", trace, PRIOR,
                     W / "evidence/GateS1bR3/system/progress.json"]
    provenance = dict(sdc_enable=1, recovery_enable=0, fault_tx=None, negative=False,
        gates_status="PASS", recovery_runs_started=0, directory=str(CAPTURE),
        reference_control=result["reference_control"], configuration=record["configuration"],
        padded_profile=sealed["padded_profile"],
        profile_fingerprint_sha256=sealed["profile_fingerprint_sha256"],
        user_decision="2026-10-11 18:30 CEST: freeze the measured padded reference",
        adjudication="Existing capture replayed offline after the two authorized fixes",
        prior_predictions_sha256=sha(PRIOR), tooling_changes=changes,
        judgement_path=str(adjudication_path), judgement_sha256=sha(adjudication_path),
        observations_sha256=sha(observer_path), log_sha256=sha(log_path), uart_sha256=sha(uart_path),
        capture_bindings={str(path): sha(path) for path in capture_paths},
        healthy_envelope_rebase=sealed["budgets"]["healthy_envelope_rebase"])
    reference = freeze_reference(E / "reference", result["readiness"]["eligibility"],
                                 result["dispatch"], reference_context(sealed), provenance)
    tasks = [event["task"] for prediction in sealed["predictions"].values()
             for event in prediction["model"]["events"] if event["kind"] == "DISPATCH"]
    require_reference_tasks(reference, reference_context(sealed), tasks)
    sealed.update(release_reference=reference,
        r7_frozen_at=datetime.datetime.now().astimezone().isoformat(),
        r7_user_decision="2026-10-11 18:30 CEST",
        r7_prior_predictions=dict(path=str(PRIOR), sha256=sha(PRIOR)),
        r7_reference_capture_runs=1, r7_recovery_runs_before_seal=0)
    write(profile / "predictions.json", sealed)
    (profile / "predictions.json").chmod(0o444)
    write(profile / "preflight_proof.json", dict(status="PASS_OFFLINE_ONLY",
        prior_predictions_sha256=sha(PRIOR), predictions_sha256=sha(profile / "predictions.json"),
        L_ref=reference, graph_and_descriptors_unchanged=True,
        seven_scenes_and_predictions_unchanged=True, clocks_cache_and_guards_unchanged=True,
        default_image_byte_identical=True, default_image_id=prior["default_image_id"],
        padded_image_id=prior["image"]["image_id"], tooling_changes=changes,
        changed_gate="Healthy envelope only: 8592 -> 8700 us",
        unchanged_mismatch_envelopes_us=[9616, 10640],
        reference_capture_simulations_this_phase=0, recovery_simulations_this_phase=0))
    for key in ("graph", "descriptors", "predictions", "scenes", "clocks", "cache",
                "simulation_guards", "release_observation", "completion_word_to_sim_end_ps"):
        assert sealed[key] == prior[key], ("Unapproved gate or graph change", key)
    print("Frozen measured L_ref:", reference["sha256"], "; tasks:", len(result["dispatch"]))
    return reference


if __name__ == "__main__":
    prepare()
