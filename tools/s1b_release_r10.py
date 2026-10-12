"""R10: frozen r8 gate profile, then scenes 5 to 7, S1b-2 recheck and S1b-4 metrics.

R9 stopped at s1b_stage0_primary_off: the zero-Questa-error gate cannot pass a
predeclared word-5 ending, and the 8700 us envelope does not cover the injected
producer on the serial SDC-off path. The user decided both at 21:22 CEST. R10
freezes those two decisions as the r8 profile before any simulation and keeps
every other gate, both per-configuration references and the R9 budgets.

The R8 healthy pair and the two passing R9 injected scenes are reused: checked
against their seals and re-judged offline with the R10 judge, which must not
change any verdict. --check-only runs all of that without writing anything.
"""
import argparse
import datetime
import json
import os
from pathlib import Path

from bingo_sdc_recover_reference import reference_context
from s1b_system_r1 import sha, write
import s1b_reference_r8 as r8
import s1b_release_r8
import s1b_release_r9
import s1b_injected_budget_r9 as budget
import s1b_profile_r10 as profiles
import s1b_system_r10 as runner
from s1b_metrics_r8 import metrics

W = Path(__file__).resolve().parents[1]
E = W / "evidence/GateS1bR10"
RESULTS = W / "results/GateS1bR10"
R8 = W / "evidence/GateS1bR8"
R9 = W / "evidence/GateS1bR9"
R9_MANIFEST_SHA = "5f6ee71e37847ce8136013299cfde17135f43d9ece2bf8c52f5cb83bfa631d7b"
R9_BUDGET_SHA = "38e6bfff8dd498e6948272c539fa700b9f848f340f1798a142d31f3f56f73572"
R9_PLAN_SHA = "82b1996760fcc5623a85d41515d0c951a43a910d5ae984b401899a3534235796"
TOOLS = ["s1b_profile_r10.py", "s1b_system_r10.py", "s1b_release_r10.py", "s1b_seal_r10.py",
         "test_s1b_profile_r10.py", "s1b_injected_budget_r9.py", "s1b_release_r9.py", "s1b_seal_r9.py",
         "s1b_system_r9.py", "s1b_system_r1.py", "s1b_system_r8.py", "s1b_reference_r8.py",
         "s1b_release_r8.py", "s1b_metrics_r8.py", "s1b_release_r7.py", "s1b_envelope_r7.py"]
VERDICT = ("status", "problems", "release", "dispatch", "done", "injected_task_budget")


def r9_inputs():
    """R9 seal, frozen budgets and the two passing injected runs, checked against the R9 manifest."""
    if sha(R9 / "manifest.json") != R9_MANIFEST_SHA or \
            (R9 / "manifest.sha256").read_text() != R9_MANIFEST_SHA + "  manifest.json\n":
        raise ValueError("R9 seal changed: STOP_USER")
    sealed_files = json.loads((R9 / "manifest.json").read_text())["files"]
    progress = R9 / "system/progress.json"
    state = json.loads(progress.read_text())
    budgets, plan = state["budgets"], state["plan"]
    if budgets["sha256"] != R9_BUDGET_SHA or plan["sha256"] != R9_PLAN_SHA:
        raise ValueError("R9 budget or plan binding differs: STOP_USER")
    runs = [run for run in state["runs"] if run["name"] in runner.REUSED_R9]
    if [run["name"] for run in runs] != list(runner.REUSED_R9) or any(run["status"] != "PASS" for run in runs):
        raise ValueError("R9 injected scenes 3 and 4 are not PASS: STOP_USER")
    paths = [progress, Path(budgets["path"]), Path(budgets["hash_path"]),
             Path(plan["path"]), Path(plan["hash_path"])]
    for run in runs:
        paths += [p for p in Path(run["directory"]).rglob("*") if p.is_file()]
    inputs = {}
    for path in paths:
        if sealed_files.get(str(path)) != sha(path):
            raise ValueError(f"Reused R9 input differs from the R9 seal: {path}")
        inputs[str(path)] = sealed_files[str(path)]
    inputs[str(R9 / "manifest.json")] = R9_MANIFEST_SHA
    return dict(budgets=budgets, runs=runs, inputs=inputs)


def rejudge(sealed, bindings, record, profile, runs):
    """Re-judge every reused run with the R10 judge; no verdict may change."""
    rows = {}
    for run in runs:
        name = run["name"]
        scene, cfg = sealed["scenes"][name], sealed["predictions"][name]["cfg"]
        bins, = Path(run["directory"]).glob("task_*/bin")
        reference = r8.select_reference(bindings, scene, cfg)
        new = runner.judged_run(name, scene, (bins / "sim_run.log").read_text(),
                                (bins / "uart_chip_0_0.log").read_text(),
                                (Path(run["directory"]) / "observations.txt").read_text(),
                                sealed, reference, record, profile)
        new = json.loads(json.dumps(new))
        old = dict(run, injected_task_budget=run.get("injected_task_budget"))
        if any(new[key] != old[key] for key in VERDICT) or new["status"] != "PASS" \
                or new["warning_gate"] != run["warning_gate"] or new["success_envelope"]["source"] != "r7 success_envelope_us":
            raise ValueError(f"R10 judge changes the reused {name} verdict: STOP_USER")
        rows[name] = dict(status=new["status"], problems=new["problems"],
                          injected_task_budget=new["injected_task_budget"],
                          success_envelope=new["success_envelope"], warning_gate=new["warning_gate"],
                          release_max_extra_ps=max(r["extra_ps"] for r in new["release"]["tasks"].values()),
                          reference=new["release"]["reference"]["sha256"])
    return rows


def preflight():
    sealed, configs, reused8, bindings, derived = s1b_release_r9.preflight()
    reused9 = r9_inputs()
    record = budget.load(reused9["budgets"])
    # The R9 budgets were frozen from the same derivation; only frozen_at may differ.
    if {k: v for k, v in record.items() if k != "frozen_at"} != json.loads(json.dumps(derived)):
        raise ValueError("R9 frozen budgets differ from their derivation: STOP_USER")
    predictions = dict(path=str(s1b_release_r8.PRED), sha256=sha(s1b_release_r8.PRED))
    profile = profiles.derive(sealed, predictions, reused9["budgets"], record)
    return sealed, configs, reused8, reused9, bindings, record, profile


def freeze_plan(sealed, configs, integrity, reused8, reused9, bindings, profile_binding, rejudged):
    path = E / "profile/plan.json"
    plan = dict(
        schema=1, created=datetime.datetime.now().astimezone().isoformat(),
        decision=("User 21:22 CEST: (1) in s1b_stage0_primary_off and s1b_invalid_status only, allow exactly "
                  "the check_finish_rtl.sv line-37 $error (status 5) and line-50 $fatal; any other error still "
                  "fails and all other scenes keep zero Questa errors. (2) s1b_stage0_primary_off envelope "
                  "9578 us = 8700 + 878 us injected extra budget; all other envelopes unchanged. Frozen as the "
                  "r8 gate profile before any simulation."),
        gate_profile=profile_binding,
        injected_task_budgets=reused9["budgets"],
        predictions=dict(path=str(s1b_release_r8.PRED), sha256=sha(s1b_release_r8.PRED)),
        references={r8.CONFIGURATIONS[k]: v for k, v in bindings.items()},
        scene_reference=configs,
        reused_runs=dict(R8=list(runner.REUSED_R8), R9=list(runner.REUSED_R9)),
        scenes=[name for name, _ in runner.scene_plan(sealed)],
        rejudged=rejudged,
        gates=dict(release_cap_ps=sealed["release_observation"]["threshold_ps"],
                   criterion=sealed["release_observation"]["criterion"],
                   healthy_envelope_us=sealed["budgets"]["normal_all_healthy_envelope"],
                   scene_envelopes_us={profiles.ENVELOPE_SCENE: profiles.ENVELOPE_US},
                   kernel_bounds=sealed["budgets"]["kernel_bounds"],
                   injected_task_budget_us=942, cache_warnings=0,
                   questa_errors=dict(default=0, **{name: "exactly check_finish_rtl.sv:37 and :50"
                                                    for name in profiles.TERMINAL_SCENES})),
        s1b2_integrity=integrity,
        reused_inputs={**reused8["inputs"], **reused9["inputs"]},
        tools={str(W / "tools" / name): sha(W / "tools" / name) for name in TOOLS})
    with path.open("x") as stream:
        stream.write(json.dumps(plan, indent=2) + "\n")
    hash_path = E / "profile/plan.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  plan.json\n")
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def main(check_only=False):
    sealed, configs, reused8, reused9, bindings, record, profile = preflight()
    integrity = s1b_release_r8.s1b2_integrity(sealed)
    reused_runs = reused8["runs"] + reused9["runs"]
    rejudged = rejudge(sealed, bindings, record, profile, reused_runs)
    if check_only:
        print(json.dumps(dict(status="PASS_OFFLINE_PREFLIGHT", simulations=0, scene_reference=configs,
                              scene_envelopes_us=profile["scene_envelopes_us"],
                              terminal_scenes=profile["terminal_scenes"],
                              rejudged={n: (r["status"], r["problems"]) for n, r in rejudged.items()},
                              s1b2=integrity["status"], image_part_files=integrity["image_part_files_checked"])))
        return 0
    E.mkdir(exist_ok=True)  # May already hold the offline suite; subdirectories are exclusive.
    with (E / "s1b2_integrity.json").open("x") as stream:
        stream.write(json.dumps(integrity, indent=2) + "\n")
    # The r8 profile is frozen first; nothing below may start before it exists.
    profile_binding = profiles.freeze(E / "profile", profile)
    print("r8 profile", profile_binding["sha256"], flush=True)
    plan = freeze_plan(sealed, configs, integrity, reused8, reused9, bindings, profile_binding, rejudged)
    runner.E, runner.ROOT, runner.PRED = E / "system", RESULTS, s1b_release_r8.PRED
    code = runner.main(bindings=bindings, budget_binding=reused9["budgets"], profile_binding=profile_binding,
                       plan_binding=plan, seed_records=s1b_release_r9.seed_records(reused_runs))
    result = json.loads((runner.E / "result.json").read_text())
    if code or result["status"] != "PASS" or result["runs"] != 3:
        print("STOP_USER", result.get("reason"))
        return 1
    after = s1b_release_r8.s1b2_integrity(sealed)
    write(E / "post_run_s1b2_integrity.json", after)
    # S1b-4 over all seven scenes in frozen order: R8 healthy pair, R9 scenes 3-4, R10 scenes 5-7.
    inputs = E / "s1b4_inputs"
    inputs.mkdir()
    r10_runs = json.loads((runner.E / "progress.json").read_text())["runs"]
    write(inputs / "progress.json", dict(
        runs=reused_runs + r10_runs,
        sources={**{run["name"]: str(R8 / "system/progress.json") for run in reused8["runs"]},
                 **{run["name"]: str(R9 / "system/progress.json") for run in reused9["runs"]},
                 **{run["name"]: str(runner.E / "progress.json") for run in r10_runs}}))
    descriptor = W / "evidence/chain_s1b_r7/descriptor_metrics/result.json"
    summary = metrics(sealed, inputs, descriptor)
    summary["injected_task_budgets"] = {
        run["name"]: dict(run["injected_task_budget"], hook_trace=run["hook_trace"])
        for run in reused9["runs"] + r10_runs if run["injected_task_budget"] is not None}
    summary["gate_profile"] = dict(profile_binding, rejudged=rejudged,
                                   r10=[dict(name=run["name"], warning_gate=run["warning_gate"],
                                             success_envelope=run["success_envelope"]) for run in r10_runs])
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
