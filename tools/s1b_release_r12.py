"""R12: frozen r10 gate profile, then the retained R11 negative-probe run re-judged, S1b-2 and S1b-4.

R11 stopped at s1b_invalid_status: the recovery checker requires the UART line
"Offload Finish with ret = 2", which only OFFLOAD_BINGO_HW_DEBUG builds print. The
user decided at 22:33 CEST to accept completion word 5 as the proof of return code 2,
in that scene only. R12 freezes this as the r10 profile and a plan before any
judging and keeps every other gate.

No simulation. The R11 probe run is complete: the simulation reached its
predeclared $fatal end, the runner finished every per-scene step before the judge
stopped it, and every file of the run is in the R11 seal. The decision changes only
how the judge reads evidence the run already holds; the image, configuration and
simulator inputs are unchanged, so a rerun could only repeat the run. R12 re-judges
the sealed raw outputs under the frozen profile, repeats the R11 runner's per-scene
steps offline, and requires that only the FAIL2 problem changes.

The R8 healthy pair, R9 scenes 3-4, R10 s1b_stage0_primary_off and R11
s1b_stage0_shadow_on are reused: checked against their seals and re-judged offline
with the R12 judge, which must not change any verdict. --check-only runs all of that,
and a dry re-judge of the probe with the unfrozen profile, without writing.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import traceback

from bingo_sdc_recover_reference import reference_context
from s1b_system_r1 import sha, write, finite_trace, tcl_observer
import s1b_reference_r8 as r8
import s1b_injected_budget_r9 as budget
import s1b_release_r8
import s1b_release_r9
import s1b_release_r11
import s1b_profile_r11
import s1b_profile_r12 as profiles
from s1b_system_r8 import planned_dispatch
from s1b_metrics_r8 import metrics

W = Path(__file__).resolve().parents[1]
E = W / "evidence/GateS1bR12"
R8 = W / "evidence/GateS1bR8"
R9 = W / "evidence/GateS1bR9"
R10 = W / "evidence/GateS1bR10"
R11 = W / "evidence/GateS1bR11"
R11_MANIFEST_SHA = "352e60b878dfd35b7ee344f91bed81fe0e1f7036a4862453fab4100833da36b8"
R11_PLAN_SHA = "bac13b7e158b2513c498786c8154f0c58db93e119082c374c41081e40c239ac7"
REUSED_R8 = s1b_release_r11.runner.REUSED_R8
REUSED_R9 = s1b_release_r11.runner.REUSED_R9
REUSED_R10 = s1b_release_r11.runner.REUSED_R10
REUSED_R11 = ("s1b_stage0_shadow_on",)
RETAINED = profiles.FAIL2_SCENE
SCHEMA = "SCHEMA 2 C=3 CL=2 TAG=4 IDW=12 DW=62\n"
TOOLS = ["s1b_profile_r12.py", "s1b_release_r12.py", "s1b_seal_r12.py", "test_s1b_profile_r12.py"] \
    + s1b_release_r11.TOOLS
VERDICT = s1b_release_r11.VERDICT
# The only fields the r10 profile may change in the retained probe judgement.
CHANGED = ("status", "problems", "fail2_evidence")


def r11_inputs():
    """R11 seal, its frozen r9 profile and plan, the passing shadow_on run and the retained probe run."""
    if sha(R11 / "manifest.json") != R11_MANIFEST_SHA or \
            (R11 / "manifest.sha256").read_text() != R11_MANIFEST_SHA + "  manifest.json\n":
        raise ValueError("R11 seal changed: STOP_USER")
    sealed_files = json.loads((R11 / "manifest.json").read_text())["files"]
    progress = R11 / "system/progress.json"
    state = json.loads(progress.read_text())
    profile, plan = state["gate_profile"], state["plan"]
    if profile["sha256"] != profiles.R9_PROFILE_SHA or plan["sha256"] != R11_PLAN_SHA:
        raise ValueError("R11 profile or plan binding differs: STOP_USER")
    runs = state["runs"]
    if [run["name"] for run in runs] != list(REUSED_R11) + [RETAINED] or runs[0]["status"] != "PASS":
        raise ValueError("R11 shadow_on is not PASS or the probe run is missing: STOP_USER")
    retained = runs[1]
    if retained["status"] != "FAIL" or retained["problems"] != [profiles.FAIL2_LABEL] \
            or state["failure"] != retained or state["simulation_started"] != 2:
        raise ValueError("R11 probe did not stop at the FAIL2 evidence alone: STOP_USER")
    for run in runs:
        if json.loads((Path(run["directory"]) / "judgement.json").read_text()) != run:
            raise ValueError(f"R11 {run['name']} judgement differs from its progress row: STOP_USER")
    paths = [progress, R11 / "system/result.json", R11 / "system/stop.json", R11 / "system/manifest.json",
             Path(profile["path"]), Path(profile["hash_path"]), Path(plan["path"]), Path(plan["hash_path"])]
    for run in runs:
        paths += [p for p in Path(run["directory"]).rglob("*") if p.is_file()]
    inputs = {}
    for path in paths:
        if sealed_files.get(str(path)) != sha(path):
            raise ValueError(f"Reused R11 input differs from the R11 seal: {path}")
        inputs[str(path)] = sealed_files[str(path)]
    inputs[str(R11 / "manifest.json")] = R11_MANIFEST_SHA
    return dict(profile=profile, plan=plan, runs=runs[:1], retained=retained, inputs=inputs)


def judge_run(name, sealed, bindings, record, profile, directory):
    scene, cfg = sealed["scenes"][name], sealed["predictions"][name]["cfg"]
    bins, = Path(directory).glob("task_*/bin")
    reference = r8.select_reference(bindings, scene, cfg)
    log, uart = (bins / "sim_run.log").read_text(), (bins / "uart_chip_0_0.log").read_text()
    observer = (Path(directory) / "observations.txt").read_text()
    return profiles.judge(name, scene, log, uart, observer, sealed, reference, record, profile), bins, log


def rejudge(sealed, bindings, record, profile, runs):
    """Re-judge every reused run with the R12 judge; no verdict may change."""
    rows = {}
    for run in runs:
        name = run["name"]
        new, _, _ = judge_run(name, sealed, bindings, record, profile, run["directory"])
        new = json.loads(json.dumps(new))
        old = dict(run, injected_task_budget=run.get("injected_task_budget"),
                   release_adjustment=run.get("release_adjustment"))
        # R8/R9 judgements predate the r8 profile: their envelope is the r7 rule, re-derived here.
        if "success_envelope" not in old and new["success_envelope"]["source"] == "r7 success_envelope_us":
            old["success_envelope"] = new["success_envelope"]
        if any(new[key] != old[key] for key in VERDICT + ("release_adjustment",)) or new["status"] != "PASS" \
                or new["fail2_evidence"] is not None:
            raise ValueError(f"R12 judge changes the reused {name} verdict: STOP_USER")
        rows[name] = dict(status=new["status"], problems=new["problems"],
                          injected_task_budget=new["injected_task_budget"],
                          success_envelope=new["success_envelope"], warning_gate=new["warning_gate"],
                          fail2_evidence=None, **s1b_release_r11.release_rows(new),
                          reference=new["release"]["reference"]["sha256"])
    return rows


def preflight():
    sealed, configs, reused8, reused9, reused10, bindings, record, r9_profile = s1b_release_r11.preflight()
    reused11 = r11_inputs()
    frozen_r9 = s1b_profile_r11.load(reused11["profile"])
    profile = profiles.derive(sealed, r9_profile, frozen_r9)
    return sealed, configs, reused8, reused9, reused10, reused11, bindings, record, profile


def freeze_plan(sealed, configs, integrity, reused, bindings, profile_binding, rejudged):
    reused8, reused9, reused10, reused11 = reused
    retained = reused11["retained"]
    path = E / "profile/plan.json"
    plan = dict(
        schema=1, created=datetime.datetime.now().astimezone().isoformat(),
        decision=("User 22:33 CEST: for s1b_invalid_status ONLY, accept completion word 5 as proof of return code "
                  "2 (start.S writes (exit_code << 1) | 1; 5 = (2 << 1) | 1), instead of the UART line 'Offload "
                  "Finish with ret = 2'. Every other check, in this scene and all others, is unchanged. Frozen as "
                  "the r10 gate profile before any judging. The r8 and r9 decisions are unchanged."),
        gate_profile=profile_binding, r9_gate_profile=reused11["profile"], r8_gate_profile=reused10["profile"],
        injected_task_budgets=reused9["budgets"],
        predictions=dict(path=str(s1b_release_r8.PRED), sha256=sha(s1b_release_r8.PRED)),
        references={r8.CONFIGURATIONS[k]: v for k, v in bindings.items()},
        scene_reference=configs,
        reused_runs=dict(R8=list(REUSED_R8), R9=list(REUSED_R9), R10=list(REUSED_R10), R11=list(REUSED_R11)),
        retained_run=dict(
            name=RETAINED, directory=retained["directory"], r11_status=retained["status"],
            r11_problems=retained["problems"], r11_manifest_sha256=R11_MANIFEST_SHA,
            judgement_sha256=sha(Path(retained["directory"]) / "judgement.json"),
            method="re-judge the sealed R11 raw outputs under the frozen r10 profile; no simulation",
            allowed_changes=list(CHANGED)),
        scenes=[], simulations=0,
        rejudged=rejudged,
        gates=dict(release_cap_ps=sealed["release_observation"]["threshold_ps"],
                   criterion=sealed["release_observation"]["criterion"],
                   release_injected_predecessor=dict(
                       scene="s1b_stage0_shadow_on", task=1, injected_task=2, reference_run_ps=15_568_000,
                       cap_ps=sealed["release_observation"]["threshold_ps"]),
                   fail2_completion_word=dict(scene=RETAINED, completion_word=5, return_code=2,
                                              kept_log_pattern=profiles.STATUS5),
                   healthy_envelope_us=sealed["budgets"]["normal_all_healthy_envelope"],
                   scene_envelopes_us={"s1b_stage0_primary_off": 9578},
                   kernel_bounds=sealed["budgets"]["kernel_bounds"],
                   injected_task_budget_us=942, cache_warnings=0,
                   questa_errors=dict(default=0, **{name: "exactly check_finish_rtl.sv:37 and :50"
                                                    for name in ("s1b_stage0_primary_off", RETAINED)})),
        s1b2_integrity=integrity,
        reused_inputs={**reused8["inputs"], **reused9["inputs"], **reused10["inputs"], **reused11["inputs"]},
        tools={str(W / "tools" / name): sha(W / "tools" / name) for name in TOOLS})
    with path.open("x") as stream:
        stream.write(json.dumps(plan, indent=2) + "\n")
    hash_path = E / "profile/plan.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  plan.json\n")
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def integrity(sealed, bindings, budget_binding, record, profile_binding, plan_binding):
    """The R10 runner's integrity checks, plus the frozen r10 profile hash, sidecar and read-only mode."""
    plan = json.loads(Path(plan_binding["path"]).read_text())
    assert sha(plan_binding["path"]) == plan_binding["sha256"], "R12 plan changed"
    assert Path(plan_binding["hash_path"]).read_text() == plan_binding["sha256"] + "  plan.json\n"
    assert not any(Path(p).stat().st_mode & 0o222 for p in (plan_binding["path"], plan_binding["hash_path"]))
    assert sha(plan["predictions"]["path"]) == plan["predictions"]["sha256"]
    for path, digest in plan["tools"].items():
        assert sha(path) == digest, ("R12 tool changed", path)
    for path, digest in plan["reused_inputs"].items():
        assert sha(path) == digest, ("Reused input changed", path)
    for path, digest in sealed["bindings"].items():
        assert sha(path) == digest, ("Frozen input changed", path)
    assert all(sha(W / path) == digest for path, digest in sealed["source_hashes"].items())
    assert budget.load(budget_binding) == record
    context = reference_context(sealed)
    for binding in bindings.values():
        r8.load_reference(binding, context)
    return profiles.load(profile_binding)


def rejudge_retained(sealed, bindings, record, profile_binding, plan_binding, budget_binding, retained, seeds):
    """The R11 runner's per-scene steps, offline, on the sealed raw outputs of the probe run."""
    profile = integrity(sealed, bindings, budget_binding, record, profile_binding, plan_binding)
    name, scene, cfg = RETAINED, sealed["scenes"][RETAINED], sealed["predictions"][RETAINED]["cfg"]
    output = Path(retained["directory"])
    plan = json.loads(Path(plan_binding["path"]).read_text())
    files = [p for p in output.rglob("*") if p.is_file()]
    if not files or any(plan["reused_inputs"].get(str(p)) != sha(p) for p in files):
        raise ValueError("Retained probe output differs from the R11 seal: STOP_USER")
    reference = r8.select_reference(bindings, scene, cfg)
    r8.require_reference_tasks(reference, reference_context(sealed), planned_dispatch(sealed, name))
    if reference["sha256"] != retained["release"]["reference"]["sha256"]:
        raise ValueError("Retained probe was judged against another reference: STOP_USER")
    bins, = output.glob("task_*/bin")
    if (bins / "observer.tcl").read_text() != tcl_observer(sealed, True):
        raise ValueError("Retained probe observer differs from the frozen read-only observer: STOP_USER")
    log = (bins / "sim_run.log").read_text()
    profiles.log_gate(name, log, profile)
    if not (output / "observations.txt").read_text().startswith(SCHEMA):
        raise ValueError("Corrected 62-bit observer schema missing")
    new, _, _ = judge_run(name, sealed, bindings, record, profile, output)
    new["finite_trace"] = finite_trace(scene, log, bins, sealed)
    edges = [int(re.match(r"(\d+)", line).group(1))
             for line in (bins / "clock_observations.txt").read_text().splitlines()]
    assert len(edges) == 4
    assert all(b - a == sealed["clocks"]["manager_period_ps"] for a, b in zip(edges, edges[1:])), edges
    new["actual_manager_edges_ps"] = edges
    assert budget.injected_site(scene) is None, "The negative probe has no injected task"
    test_record = json.loads((output / "test_cfg.json").read_text())
    assert test_record["configuration"] == cfg
    product = Path(json.loads(Path(sealed["preparation_gate"]["path"]).read_text())["product_build_copy"])
    module = runpy.run_path(str(product / "target/sim/automation/test/3_start_bingo_watchdog_sim.py"))
    module["check_t1_family"](seeds + [test_record])
    new.update(name=name, directory=str(output), image_id=test_record["image_id"],
               configuration=r8.CONFIGURATIONS[r8.scene_configuration(scene, cfg)])
    new = json.loads(json.dumps(new))
    changed = sorted(key for key in set(new) | set(retained) if key not in CHANGED and new.get(key) != retained.get(key))
    if changed:
        raise ValueError(f"Re-judge changes more than the FAIL2 evidence: {changed}: STOP_USER")
    if new["problems"] not in ([], retained["problems"]):
        raise ValueError(f"Re-judge problems {new['problems']} are not the R11 FAIL2 problem or none: STOP_USER")
    assert profiles.load(profile_binding) == profile
    integrity(sealed, bindings, budget_binding, record, profile_binding, plan_binding)
    return new


def main(check_only=False):
    sealed, configs, reused8, reused9, reused10, reused11, bindings, record, profile = preflight()
    s1b2 = s1b_release_r8.s1b2_integrity(sealed)
    reused_runs = reused8["runs"] + reused9["runs"] + reused10["runs"] + reused11["runs"]
    rejudged = rejudge(sealed, bindings, record, profile, reused_runs)
    seeds = s1b_release_r9.seed_records(reused_runs)
    if check_only:
        dry, _, _ = judge_run(RETAINED, sealed, bindings, record, profile, reused11["retained"]["directory"])
        print(json.dumps(dict(status="PASS_OFFLINE_PREFLIGHT", simulations=0, scene_reference=configs,
                              fail2=profile["fail2_completion_word"]["rule"],
                              rejudged={n: (r["status"], r["problems"]) for n, r in rejudged.items()},
                              dry_probe=(dry["status"], dry["problems"], dry["fail2_evidence"]),
                              s1b2=s1b2["status"], image_part_files=s1b2["image_part_files_checked"])))
        return 0
    E.mkdir(exist_ok=True)  # May already hold the offline suite; subdirectories are exclusive.
    with (E / "s1b2_integrity.json").open("x") as stream:
        stream.write(json.dumps(s1b2, indent=2) + "\n")
    # The r10 profile is frozen first, then the plan; the probe is judged only after both exist.
    profile_binding = profiles.freeze(E / "profile", profile)
    print("r10 profile", profile_binding["sha256"], flush=True)
    plan = freeze_plan(sealed, configs, s1b2, (reused8, reused9, reused10, reused11), bindings,
                       profile_binding, rejudged)
    print("plan", plan["sha256"], flush=True)
    system = E / "system"
    system.mkdir()
    state = dict(status="RUNNING", pid=os.getpid(), started=datetime.datetime.now().astimezone().isoformat(),
                 predictions_sha256=sha(s1b_release_r8.PRED), plan=plan, budgets=reused9["budgets"],
                 gate_profile=profile_binding, r9_gate_profile=reused11["profile"],
                 references={str(k): v for k, v in bindings.items()}, method="re-judge", simulation_started=0,
                 retained=dict(name=RETAINED, directory=reused11["retained"]["directory"],
                               r11_manifest_sha256=R11_MANIFEST_SHA), runs=[])
    write(system / "progress.json", state)
    try:
        assert shutil.disk_usage(W).free >= 10 * 1024**3
        new = rejudge_retained(sealed, bindings, record, profile_binding, plan, reused9["budgets"],
                               reused11["retained"], seeds)
        write(system / (RETAINED + ".judgement.json"), new)
        state["runs"].append(new)
        if new["status"] != "PASS":
            raise RuntimeError(("Real recovery gate failure", RETAINED, new["problems"]))
        print(RETAINED + ": PASS (re-judged)", flush=True)
        state.update(status="PASS")
    except BaseException as error:
        state.update(status="STOP_USER", reason=repr(error), traceback=traceback.format_exc())
        write(system / "stop.json", state)
    state["finished"] = datetime.datetime.now().astimezone().isoformat()
    write(system / "progress.json", state)
    write(system / "result.json", dict(status=state["status"], runs=len(state["runs"]), simulations_started=0,
                                       method="re-judge", references=state["references"],
                                       budgets=reused9["budgets"], gate_profile=profile_binding,
                                       reason=state.get("reason"), predictions_sha256=state["predictions_sha256"],
                                       plan_sha256=plan["sha256"]))
    if state["status"] != "PASS":
        print("STOP_USER", state.get("reason"))
        return 1
    after = s1b_release_r8.s1b2_integrity(sealed)
    write(E / "post_run_s1b2_integrity.json", after)
    # S1b-4 over all seven scenes in frozen order: R8 pair, R9 scenes 3-4, R10 scene 5, R11 scene 6, R12 scene 7.
    inputs = E / "s1b4_inputs"
    inputs.mkdir()
    runs = reused_runs + state["runs"]
    if [run["name"] for run in runs] != list(sealed["scenes"]):
        raise ValueError("S1b-4 inputs are not the seven frozen scenes in order: STOP_USER")
    write(inputs / "progress.json", dict(
        runs=runs,
        sources={**{run["name"]: str(R8 / "system/progress.json") for run in reused8["runs"]},
                 **{run["name"]: str(R9 / "system/progress.json") for run in reused9["runs"]},
                 **{run["name"]: str(R10 / "system/progress.json") for run in reused10["runs"]},
                 **{run["name"]: str(R11 / "system/progress.json") for run in reused11["runs"]},
                 RETAINED: str(system / "progress.json")}))
    descriptor = W / "evidence/chain_s1b_r7/descriptor_metrics/result.json"
    summary = metrics(sealed, inputs, descriptor)
    for run in runs:
        summary["runs"][run["name"]].update(s1b_release_r11.release_rows(run))
    summary["injected_task_budgets"] = {
        run["name"]: dict(run["injected_task_budget"], hook_trace=run["hook_trace"])
        for run in reused9["runs"] + reused10["runs"] + reused11["runs"] if run["injected_task_budget"] is not None}
    summary["gate_profile"] = dict(profile_binding, rejudged=rejudged,
                                   r12=[dict(name=run["name"], warning_gate=run["warning_gate"],
                                             success_envelope=run["success_envelope"],
                                             release_adjustment=run["release_adjustment"],
                                             fail2_evidence=run["fail2_evidence"]) for run in state["runs"]])
    summary["stop_conditions"] = s1b_release_r9.stop_conditions(summary)
    write(E / "metrics.json", summary)
    print("S1b-4", summary["status"], summary["recovery_rate"], summary["false_positive_rate"],
          summary["unrecoverable_rate"], summary["stop_conditions"])
    return int(summary["status"] != "PASS" or bool(summary["stop_conditions"]) or after["status"] != "PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main(parser.parse_args().check_only))
