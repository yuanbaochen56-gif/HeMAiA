"""R11: frozen r9 gate profile, then scenes 6 and 7, S1b-2 recheck and S1b-4 metrics.

R10 stopped at s1b_stage0_shadow_on: P0 (task 1) is dispatched only after the
injected S0 (task 2) is DONE, so its release wait holds the S1a hook time. The
user decided at 21:59 CEST to subtract the injected task's extra run time over
its frozen capture run, for that waiting row only, keeping the 1 us cap. R11
freezes this as the r9 profile before any simulation and keeps every other gate,
both per-configuration references, the R9 budgets and the r8 decisions.

The R8 healthy pair, R9 scenes 3-4 and the R10 s1b_stage0_primary_off run are
reused: checked against their seals and re-judged offline with the R11 judge,
which must not change any verdict. --check-only runs all of that without writing.
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
import s1b_release_r10
import s1b_profile_r10
import s1b_profile_r11 as profiles
import s1b_system_r11 as runner
from s1b_metrics_r8 import metrics

W = Path(__file__).resolve().parents[1]
E = W / "evidence/GateS1bR11"
RESULTS = W / "results/GateS1bR11"
R8 = W / "evidence/GateS1bR8"
R9 = W / "evidence/GateS1bR9"
R10 = W / "evidence/GateS1bR10"
R10_MANIFEST_SHA = "6ed68838a1e2625deb6a41ce68ec91b6066b375f3e28fc12861307e55413335f"
R10_PLAN_SHA = "141efb9f269e43fd2074eaf519301972c30d896c354c31d45638f844915ee5db"
TOOLS = ["s1b_profile_r11.py", "s1b_system_r11.py", "s1b_release_r11.py", "s1b_seal_r11.py",
         "test_s1b_profile_r11.py", "s1b_profile_r10.py", "s1b_system_r10.py", "s1b_release_r10.py",
         "s1b_seal_r10.py", "s1b_injected_budget_r9.py", "s1b_release_r9.py", "s1b_seal_r9.py",
         "s1b_system_r9.py", "s1b_system_r1.py", "s1b_system_r8.py", "s1b_reference_r8.py",
         "s1b_release_r8.py", "s1b_metrics_r8.py", "s1b_release_r7.py", "s1b_envelope_r7.py"]
VERDICT = s1b_release_r10.VERDICT + ("warning_gate", "success_envelope")


def r10_inputs():
    """R10 seal, its frozen r8 profile and plan, and the passing s1b_stage0_primary_off run."""
    if sha(R10 / "manifest.json") != R10_MANIFEST_SHA or \
            (R10 / "manifest.sha256").read_text() != R10_MANIFEST_SHA + "  manifest.json\n":
        raise ValueError("R10 seal changed: STOP_USER")
    sealed_files = json.loads((R10 / "manifest.json").read_text())["files"]
    progress = R10 / "system/progress.json"
    state = json.loads(progress.read_text())
    profile, plan = state["gate_profile"], state["plan"]
    if profile["sha256"] != profiles.R8_PROFILE_SHA or plan["sha256"] != R10_PLAN_SHA:
        raise ValueError("R10 profile or plan binding differs: STOP_USER")
    runs = [run for run in state["runs"] if run["name"] in runner.REUSED_R10]
    if [run["name"] for run in runs] != list(runner.REUSED_R10) or any(run["status"] != "PASS" for run in runs):
        raise ValueError("R10 s1b_stage0_primary_off is not PASS: STOP_USER")
    paths = [progress, Path(profile["path"]), Path(profile["hash_path"]), Path(plan["path"]), Path(plan["hash_path"])]
    for run in runs:
        paths += [p for p in Path(run["directory"]).rglob("*") if p.is_file()]
    inputs = {}
    for path in paths:
        if sealed_files.get(str(path)) != sha(path):
            raise ValueError(f"Reused R10 input differs from the R10 seal: {path}")
        inputs[str(path)] = sealed_files[str(path)]
    inputs[str(R10 / "manifest.json")] = R10_MANIFEST_SHA
    return dict(profile=profile, plan=plan, runs=runs, inputs=inputs)


def rejudge(sealed, bindings, record, profile, runs):
    """Re-judge every reused run with the R11 judge; no verdict may change and no release row is adjusted."""
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
        # R8/R9 judgements predate the r8 profile: their envelope is the r7 rule, re-derived here.
        if "success_envelope" not in old and new["success_envelope"]["source"] == "r7 success_envelope_us":
            old["success_envelope"] = new["success_envelope"]
        if any(new[key] != old[key] for key in VERDICT) or new["status"] != "PASS" \
                or new["release_adjustment"] is not None:
            raise ValueError(f"R11 judge changes the reused {name} verdict: STOP_USER")
        rows[name] = dict(status=new["status"], problems=new["problems"],
                          injected_task_budget=new["injected_task_budget"],
                          success_envelope=new["success_envelope"], warning_gate=new["warning_gate"],
                          release_adjustment=None,
                          release_max_extra_ps=max(r["extra_ps"] for r in new["release"]["tasks"].values()),
                          reference=new["release"]["reference"]["sha256"])
    return rows


def preflight():
    sealed, configs, reused8, reused9, bindings, record, r8_profile = s1b_release_r10.preflight()
    reused10 = r10_inputs()
    frozen_r8 = s1b_profile_r10.load(reused10["profile"])
    profile = profiles.derive(sealed, r8_profile, frozen_r8, bindings, reference_context(sealed), record)
    return sealed, configs, reused8, reused9, reused10, bindings, record, profile


def freeze_plan(sealed, configs, integrity, reused8, reused9, reused10, bindings, profile_binding, rejudged):
    path = E / "profile/plan.json"
    row = profile_binding["rows"]["s1b_stage0_shadow_on"]
    plan = dict(
        schema=1, created=datetime.datetime.now().astimezone().isoformat(),
        decision=("User 21:59 CEST: in the G-release check, subtract from P0's wait the injected S0's extra run "
                  "time over its reference (S0 measured run time minus the 15.568 us reference); only where the "
                  "waited-on predecessor is the injected task; 1 us cap unchanged; S0 stays bound by its 942 us "
                  "injected-task budget. Frozen as the r9 gate profile before any simulation. The r8 decisions "
                  "(terminal lines, 9578 us stage0_primary_off envelope) are unchanged."),
        gate_profile={k: v for k, v in profile_binding.items() if k != "rows"},
        r8_gate_profile=reused10["profile"],
        injected_task_budgets=reused9["budgets"],
        predictions=dict(path=str(s1b_release_r8.PRED), sha256=sha(s1b_release_r8.PRED)),
        references={r8.CONFIGURATIONS[k]: v for k, v in bindings.items()},
        scene_reference=configs,
        reused_runs=dict(R8=list(runner.REUSED_R8), R9=list(runner.REUSED_R9), R10=list(runner.REUSED_R10)),
        scenes=[name for name, _ in runner.scene_plan(sealed)],
        rejudged=rejudged,
        gates=dict(release_cap_ps=sealed["release_observation"]["threshold_ps"],
                   criterion=sealed["release_observation"]["criterion"],
                   release_injected_predecessor=dict(
                       scene="s1b_stage0_shadow_on", task=row["task"], injected_task=row["injected_task"],
                       reference_run_ps=row["reference_run_ps"], cap_ps=sealed["release_observation"]["threshold_ps"]),
                   healthy_envelope_us=sealed["budgets"]["normal_all_healthy_envelope"],
                   scene_envelopes_us={s1b_profile_r10.ENVELOPE_SCENE: s1b_profile_r10.ENVELOPE_US},
                   kernel_bounds=sealed["budgets"]["kernel_bounds"],
                   injected_task_budget_us=942, cache_warnings=0,
                   questa_errors=dict(default=0, **{name: "exactly check_finish_rtl.sv:37 and :50"
                                                    for name in s1b_profile_r10.TERMINAL_SCENES})),
        s1b2_integrity=integrity,
        reused_inputs={**reused8["inputs"], **reused9["inputs"], **reused10["inputs"]},
        tools={str(W / "tools" / name): sha(W / "tools" / name) for name in TOOLS})
    with path.open("x") as stream:
        stream.write(json.dumps(plan, indent=2) + "\n")
    hash_path = E / "profile/plan.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  plan.json\n")
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def release_rows(run):
    return dict(release_max_extra_ps=max(r["extra_ps"] for r in run["release"]["tasks"].values()),
                release_max_gate_extra_ps=max(r.get("gate_extra_ps", r["extra_ps"])
                                              for r in run["release"]["tasks"].values()),
                release_adjustment=run.get("release_adjustment"))


def main(check_only=False):
    sealed, configs, reused8, reused9, reused10, bindings, record, profile = preflight()
    integrity = s1b_release_r8.s1b2_integrity(sealed)
    reused_runs = reused8["runs"] + reused9["runs"] + reused10["runs"]
    rejudged = rejudge(sealed, bindings, record, profile, reused_runs)
    if check_only:
        print(json.dumps(dict(status="PASS_OFFLINE_PREFLIGHT", simulations=0, scene_reference=configs,
                              release_rows={n: (r["task"], r["injected_task"], r["reference_run_ps"])
                                            for n, r in profile["release_injected_predecessor"]["rows"].items()},
                              scene_envelopes_us=profile["scene_envelopes_us"],
                              terminal_scenes=profile["terminal_scenes"],
                              rejudged={n: (r["status"], r["problems"]) for n, r in rejudged.items()},
                              s1b2=integrity["status"], image_part_files=integrity["image_part_files_checked"])))
        return 0
    E.mkdir(exist_ok=True)  # May already hold the offline suite; subdirectories are exclusive.
    with (E / "s1b2_integrity.json").open("x") as stream:
        stream.write(json.dumps(integrity, indent=2) + "\n")
    # The r9 profile is frozen first; nothing below may start before it exists.
    profile_binding = profiles.freeze(E / "profile", profile)
    print("r9 profile", profile_binding["sha256"], flush=True)
    plan = freeze_plan(sealed, configs, integrity, reused8, reused9, reused10, bindings,
                       dict(profile_binding, rows=profile["release_injected_predecessor"]["rows"]), rejudged)
    runner.configure(E / "system", RESULTS, s1b_release_r8.PRED)
    code = runner.main(bindings=bindings, budget_binding=reused9["budgets"], profile_binding=profile_binding,
                       plan_binding=plan, seed_records=s1b_release_r9.seed_records(reused_runs))
    result = json.loads((E / "system/result.json").read_text())
    if code or result["status"] != "PASS" or result["runs"] != len(runner.PLANNED):
        print("STOP_USER", result.get("reason"))
        return 1
    after = s1b_release_r8.s1b2_integrity(sealed)
    write(E / "post_run_s1b2_integrity.json", after)
    # S1b-4 over all seven scenes in frozen order: R8 pair, R9 scenes 3-4, R10 scene 5, R11 scenes 6-7.
    inputs = E / "s1b4_inputs"
    inputs.mkdir()
    r11_runs = json.loads((E / "system/progress.json").read_text())["runs"]
    write(inputs / "progress.json", dict(
        runs=reused_runs + r11_runs,
        sources={**{run["name"]: str(R8 / "system/progress.json") for run in reused8["runs"]},
                 **{run["name"]: str(R9 / "system/progress.json") for run in reused9["runs"]},
                 **{run["name"]: str(R10 / "system/progress.json") for run in reused10["runs"]},
                 **{run["name"]: str(E / "system/progress.json") for run in r11_runs}}))
    descriptor = W / "evidence/chain_s1b_r7/descriptor_metrics/result.json"
    summary = metrics(sealed, inputs, descriptor)
    for run in reused_runs + r11_runs:
        summary["runs"][run["name"]].update(release_rows(run))
    summary["injected_task_budgets"] = {
        run["name"]: dict(run["injected_task_budget"], hook_trace=run["hook_trace"])
        for run in reused9["runs"] + reused10["runs"] + r11_runs if run["injected_task_budget"] is not None}
    summary["gate_profile"] = dict(profile_binding, rejudged=rejudged,
                                   r11=[dict(name=run["name"], warning_gate=run["warning_gate"],
                                             success_envelope=run["success_envelope"],
                                             release_adjustment=run["release_adjustment"]) for run in r11_runs])
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
