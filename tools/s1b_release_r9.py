"""R9: frozen injected-task budget, then scene 3 onward, S1b-2 recheck and S1b-4 metrics.

R8 stopped at s1b_stage0_primary_on because the S1a injection hook runs inside
the injected producer's DISPATCH->DONE window. R9 declares one budget per
injected scene for that single task, frozen before any simulation, and keeps
every other gate, both per-configuration references and the R8 healthy pair.

--check-only runs the preflight, the reused-input checks and the offline
re-judgement of the R8 healthy pair without writing anything.
"""
import argparse
import datetime
import json
import os
from pathlib import Path

from bingo_sdc_recover_reference import reference_context
import bingo_sdc_recover_check
import s1b_system_r1
from s1b_system_r1 import sha, write
import s1b_reference_r8 as r8
import s1b_release_r8
import s1b_injected_budget_r9 as budget
import s1b_system_r9 as runner
from s1b_metrics_r8 import metrics

W = Path(__file__).resolve().parents[1]
E = W / "evidence/GateS1bR9"
RESULTS = W / "results/GateS1bR9"
R8 = W / "evidence/GateS1bR8"
R8_MANIFEST_SHA = "773dd22a611a2176354e034d88106138aac06d215271891ee2b0a886ed264348"
SDC_OFF_SHA = "d9f71e7e7a526663f7812eb142beb96614576fc403c227057c459a112fae5735"
TOOLS = ["s1b_injected_budget_r9.py", "s1b_system_r9.py", "s1b_release_r9.py", "s1b_seal_r9.py",
         "test_s1b_injected_budget_r9.py", "s1b_system_r1.py", "s1b_system_r8.py", "s1b_reference_r8.py",
         "s1b_release_r8.py", "s1b_metrics_r8.py", "s1b_release_r7.py", "s1b_envelope_r7.py"]


def r8_inputs():
    """R8 seal, frozen SDC-off reference and the two healthy runs, checked against the R8 manifest."""
    if sha(R8 / "manifest.json") != R8_MANIFEST_SHA or \
            (R8 / "manifest.sha256").read_text() != R8_MANIFEST_SHA + "  manifest.json\n":
        raise ValueError("R8 seal changed: STOP_USER")
    sealed_files = json.loads((R8 / "manifest.json").read_text())["files"]
    progress = R8 / "system/progress.json"
    state = json.loads(progress.read_text())
    binding = state["references"]["0"]
    if binding["sha256"] != SDC_OFF_SHA or binding["sdc_enable"] != 0:
        raise ValueError("SDC-off reference binding differs from d9f71e7e: STOP_USER")
    runs = [run for run in state["runs"] if run["name"] in runner.REUSED]
    if [run["name"] for run in runs] != list(runner.REUSED) or any(run["status"] != "PASS" for run in runs):
        raise ValueError("R8 healthy pair is not PASS: STOP_USER")
    paths = [progress, R8 / "system/healthy_pair_gates.json", Path(binding["path"]), Path(binding["hash_path"])]
    for run in runs:
        paths += [p for p in Path(run["directory"]).rglob("*") if p.is_file()]
    inputs = {}
    for path in paths:
        if sealed_files.get(str(path)) != sha(path):
            raise ValueError(f"Reused R8 input differs from the R8 seal: {path}")
        inputs[str(path)] = sealed_files[str(path)]
    inputs[str(R8 / "manifest.json")] = R8_MANIFEST_SHA
    return dict(binding=binding, runs=runs, inputs=inputs)


def rejudge_healthy(sealed, bindings, record, runs):
    """Re-judge the retained R8 healthy runs with the R9 judge; verdicts must not change."""
    original = (bingo_sdc_recover_check.compare_release, s1b_system_r1.compare_release)
    bingo_sdc_recover_check.compare_release = s1b_system_r1.compare_release = r8.compare_release
    rows = {}
    try:
        for run in runs:
            name = run["name"]
            scene, cfg = sealed["scenes"][name], sealed["predictions"][name]["cfg"]
            bins, = Path(run["directory"]).glob("task_*/bin")
            reference = r8.select_reference(bindings, scene, cfg)
            new = runner.judged_run(name, scene, (bins / "sim_run.log").read_text(),
                                    (bins / "uart_chip_0_0.log").read_text(),
                                    (Path(run["directory"]) / "observations.txt").read_text(),
                                    sealed, reference, record)
            new = json.loads(json.dumps(new))
            same = all(new[key] == run[key] for key in ("status", "problems", "release", "dispatch", "done"))
            if not same or new["injected_task_budget"] is not None or new["status"] != "PASS":
                raise ValueError(f"R9 judge changes the R8 {name} verdict: STOP_USER")
            rows[name] = dict(status=new["status"], problems=new["problems"], injected_task_budget=None,
                              release_max_extra_ps=max(r["extra_ps"] for r in new["release"]["tasks"].values()),
                              reference=new["release"]["reference"]["sha256"])
    finally:
        bingo_sdc_recover_check.compare_release, s1b_system_r1.compare_release = original
    return rows


def seed_records(runs):
    return [json.loads((Path(run["directory"]) / "test_cfg.json").read_text()) for run in runs]


def preflight():
    sealed, configs = s1b_release_r8.preflight()
    reused = r8_inputs()
    context = reference_context(sealed)
    bindings = {1: dict(sealed["release_reference"], sdc_enable=1), 0: reused["binding"]}
    for binding in bindings.values():
        r8.load_reference(binding, context)
    for name, scene in sealed["scenes"].items():
        r8.require_reference_tasks(r8.select_reference(bindings, scene, sealed["predictions"][name]["cfg"]),
                                   context, runner.planned_dispatch(sealed, name))
    record = budget.derive(sealed, budget.r8_measurement(), budget.healthy_print_rates())
    return sealed, configs, reused, bindings, record


def freeze_plan(sealed, configs, integrity, reused, bindings, budget_binding, rejudged):
    profile = E / "profile"
    plan = dict(
        schema=1, created=datetime.datetime.now().astimezone().isoformat(),
        decision=("User 19:58 CEST: the single injected task of each injected scene gets its own budget, "
                  "declared and frozen before the run with a rationale from the hook cost; every other DMA "
                  "keeps 64 us. Per-configuration references as in R8. 1 us G-release, 8700 us envelope, "
                  "zero warnings and byte-identical default images unchanged."),
        injected_task_budgets=budget_binding,
        predictions=dict(path=str(s1b_release_r8.PRED), sha256=sha(s1b_release_r8.PRED)),
        references={r8.CONFIGURATIONS[k]: v for k, v in bindings.items()},
        scene_reference=configs, reused_runs=list(runner.REUSED),
        scenes=[name for name, _ in runner.scene_plan(sealed)],
        rejudged_r8_healthy=rejudged,
        gates=dict(release_cap_ps=sealed["release_observation"]["threshold_ps"],
                   criterion=sealed["release_observation"]["criterion"],
                   healthy_envelope_us=sealed["budgets"]["normal_all_healthy_envelope"],
                   kernel_bounds=sealed["budgets"]["kernel_bounds"],
                   cache_warnings=0, questa_errors=0),
        s1b2_integrity=integrity,
        reused_inputs=reused["inputs"],
        tools={str(W / "tools" / name): sha(W / "tools" / name) for name in TOOLS})
    path = profile / "plan.json"
    with path.open("x") as stream:
        stream.write(json.dumps(plan, indent=2) + "\n")
    hash_path = profile / "plan.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  plan.json\n")
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def stop_conditions(summary):
    counts = summary["counts"]
    problems = []
    if counts["healthy_false_positive"]:
        problems.append("healthy false positive")
    if counts["wrong_or_hang"] or counts["unrecoverable"] or counts["recovered"] != counts["injected_on"]:
        problems.append("C3 failure (wrong, hang, unrecoverable or unrecovered injection)")
    return problems


def main(check_only=False):
    sealed, configs, reused, bindings, record = preflight()
    integrity = s1b_release_r8.s1b2_integrity(sealed)
    rejudged = rejudge_healthy(sealed, bindings, record, reused["runs"])
    if check_only:
        print(json.dumps(dict(status="PASS_OFFLINE_PREFLIGHT", simulations=0, scene_reference=configs,
                              budgets={n: s["budget_us"] for n, s in record["scenes"].items()},
                              r8_hook_cost_ps=record["r8_measurement"]["hook_cost_ps"],
                              rejudged_r8_healthy=rejudged, s1b2=integrity["status"],
                              image_part_files=integrity["image_part_files_checked"])))
        return 0
    E.mkdir(exist_ok=True)  # May already hold the offline suite; subdirectories are exclusive.
    with (E / "s1b2_integrity.json").open("x") as stream:
        stream.write(json.dumps(integrity, indent=2) + "\n")
    budget_binding = budget.freeze(E / "profile", record)
    plan = freeze_plan(sealed, configs, integrity, reused, bindings, budget_binding, rejudged)
    runner.E, runner.ROOT, runner.PRED = E / "system", RESULTS, s1b_release_r8.PRED
    code = runner.main(bindings=bindings, budget_binding=budget_binding, plan_binding=plan,
                       seed_records=seed_records(reused["runs"]))
    result = json.loads((runner.E / "result.json").read_text())
    if code or result["status"] != "PASS" or result["runs"] != 5:
        print("STOP_USER", result.get("reason"))
        return 1
    after = s1b_release_r8.s1b2_integrity(sealed)
    write(E / "post_run_s1b2_integrity.json", after)
    # S1b-4 over all seven scenes: the R8 healthy pair plus the five R9 runs.
    inputs = E / "s1b4_inputs"
    inputs.mkdir()
    r9_runs = json.loads((runner.E / "progress.json").read_text())["runs"]
    write(inputs / "progress.json", dict(
        runs=reused["runs"] + r9_runs,
        sources={**{run["name"]: str(R8 / "system/progress.json") for run in reused["runs"]},
                 **{run["name"]: str(runner.E / "progress.json") for run in r9_runs}}))
    descriptor = W / "evidence/chain_s1b_r7/descriptor_metrics/result.json"
    summary = metrics(sealed, inputs, descriptor)
    summary["injected_task_budgets"] = {
        run["name"]: dict(run["injected_task_budget"], hook_trace=run["hook_trace"])
        for run in r9_runs if run["injected_task_budget"] is not None}
    summary["stop_conditions"] = stop_conditions(summary)
    write(E / "metrics.json", summary)
    print("S1b-4", summary["status"], summary["recovery_rate"], summary["false_positive_rate"],
          summary["unrecoverable_rate"], summary["stop_conditions"])
    return int(summary["status"] != "PASS" or bool(summary["stop_conditions"]) or after["status"] != "PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main(parser.parse_args().check_only))
