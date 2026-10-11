"""R11: the r9 gate profile, the r8 profile plus the user decision of 2026-10-11 21:59 CEST.

3. G-release, only for a task whose waited-on predecessor is the injected task:
   subtract the injected task's extra run time over its run in the frozen L_ref
   capture from that task's release wait, then apply the unchanged 1 us cap.
   The injected task itself stays bound by its frozen R9 budget (942 us).

The rows the decision covers are derived before any simulation from the frozen
references: task T waits on injected task X when X's whole capture run lies in
T's capture release window and T is the first DISPATCH after X's DONE. Across the
injected family that is one row, s1b_stage0_shadow_on task 1 (P0) waiting on
task 2 (S0, 15.568 us in the capture). Decisions 1 and 2 are the frozen r8
profile, re-derived and compared with it. The r10 judge runs unchanged; only the
declared release row is replaced, and only after the r1 rows are shown to agree
with the r1 G-release problem.
"""
import ast
import datetime
import json
import os
from pathlib import Path

from bingo_sdc_recover_reference import CAP_PS
from s1b_system_r1 import sha
import s1b_injected_budget_r9 as budget
import s1b_profile_r10 as r10
import s1b_reference_r8 as r8

DECISION = "2026-10-11 21:59 CEST"
LABEL = "r9_gate_profile"
R8_PROFILE_SHA = "531ae1bb833cc81007e39aca5bd50886c09fa4b3f8ec94e032ed4d6ff0137236"
RELEASE_LABEL = "G-release extra exceeds 1us: "
# The single (scene, waiting task, injected task, capture run of the injected task) the frozen references yield.
EXPECTED_ROWS = {"s1b_stage0_shadow_on": (1, 2, 15_568_000)}
ProfileError = r10.ProfileError
log_gate = r10.log_gate


def capture_events(reference):
    """DISPATCH/DONE of the frozen reference capture, checked against the reference's own hashes."""
    provenance = reference["provenance"]
    logs = list(Path(provenance["directory"]).glob("task_*/bin/sim_run.log"))
    if len(logs) != 1 or sha(logs[0]) != provenance["log_sha256"]:
        raise ProfileError("Reference capture log differs from its frozen provenance")
    dispatch, done, slot = budget.events(logs[0].read_text())
    if {int(task): row["dispatch_ps"] for task, row in reference["tasks"].items()} != dispatch:
        raise ProfileError("Reference capture DISPATCH differs from the frozen L_ref rows")
    return dict(path=str(logs[0]), sha256=provenance["log_sha256"]), dispatch, done, slot


def first_dispatch_after(dispatch, stamp):
    later = [value for value in dispatch.values() if value > stamp]
    if not later:
        return []
    return sorted(task for task, value in dispatch.items() if value == min(later))


def waiting_rows(sealed, bindings, context, record):
    """Every (scene, T, X) in which T's frozen capture release window holds injected X's whole run."""
    rows = {}
    for name, scene in sealed["scenes"].items():
        site = budget.injected_site(scene)
        if site is None:
            continue
        injected, cluster, core = site[:3]
        declared = record["scenes"][name]
        if (declared["task"], declared["cluster"], declared["core"]) != (injected, cluster, core):
            raise ProfileError(f"{name}: injected task differs from the frozen R9 budget")
        binding = r8.select_reference(bindings, scene, sealed["predictions"][name]["cfg"])
        reference = r8.load_reference(binding, context)
        log, dispatch, done, slot = capture_events(reference)
        if slot[injected] != (cluster, core):
            raise ProfileError(f"{name}: injected task ran on another slot in the reference capture")
        for task, row in reference["tasks"].items():
            task = int(task)
            if task == injected or not (row["eligible_ps"] <= dispatch[injected] and done[injected] <= row["dispatch_ps"]):
                continue
            if first_dispatch_after(dispatch, done[injected]) != [task]:
                raise ProfileError(f"{name}: task {task} holds the injected run but is not dispatched next")
            if name in rows:
                raise ProfileError(f"{name}: more than one task waits on the injected task")
            rows[name] = dict(
                task=task, task_role=sealed["graph"]["roles"][str(task)],
                injected_task=injected, injected_role=sealed["graph"]["roles"][str(injected)],
                injected_cluster=cluster, injected_core=core,
                injected_budget_us=declared["budget_us"], injected_budget_ps=declared["budget_ps"],
                reference_run_ps=done[injected] - dispatch[injected],
                reference_eligible_ps=row["eligible_ps"], reference_dispatch_ps=row["dispatch_ps"],
                reference_L_ref_ps=row["latency_ps"],
                reference_injected_dispatch_ps=dispatch[injected], reference_injected_done_ps=done[injected],
                reference_done_to_dispatch_ps=row["dispatch_ps"] - done[injected],
                reference=dict(binding), reference_capture_log=log)
    return rows


def model_order(sealed, name, task, injected):
    """The frozen tagged model first fetches T in the tick in which X dispatches and writes DONE."""
    events = sealed["predictions"][name]["model"]["events"]
    tick = lambda kind, t: [e["tick"] for e in events if e["kind"] == kind and e["task"] == t]
    fetch, x_dispatch, x_done = tick("FETCH", task), tick("DISPATCH", injected), tick("DEVICE_DONE_WRITE", injected)
    t_dispatch = tick("DISPATCH", task)
    if not (len(fetch) == len(x_dispatch) == len(x_done) == len(t_dispatch) == 1
            and fetch == x_dispatch == x_done and t_dispatch[0] > fetch[0]):
        raise ProfileError(f"{name}: frozen tagged model does not order task {task} after injected task {injected}")
    return dict(task_fetch_tick=fetch[0], injected_dispatch_and_done_tick=x_dispatch[0],
                task_dispatch_tick=t_dispatch[0])


def derive(sealed, r8_profile, frozen_r8, bindings, context, record):
    """The r9 profile; refuses anything but the r8 profile plus the one approved release row."""
    if {k: v for k, v in frozen_r8.items() if k != "frozen_at"} != json.loads(json.dumps(r8_profile)):
        raise ProfileError("Re-derived r8 decisions differ from the frozen r8 profile")
    if sealed["release_observation"]["threshold_ps"] != CAP_PS or CAP_PS != 1_000_000:
        raise ProfileError("G-release cap differs from 1 us")
    rows = waiting_rows(sealed, bindings, context, record)
    if {name: (row["task"], row["injected_task"], row["reference_run_ps"]) for name, row in rows.items()} \
            != EXPECTED_ROWS:
        raise ProfileError("Rows waiting on the injected task differ from the decision")
    for name, row in rows.items():
        if (row["task_role"], row["injected_role"], row["injected_budget_us"]) != ("P0", "S0", 942):
            raise ProfileError(f"{name}: waiting row is not P0 on the injected S0 with its 942 us budget")
        row["model"] = model_order(sealed, name, row["task"], row["injected_task"])
    decision = ("In the G-release check, subtract from P0's wait the injected S0's extra run time over its "
                "reference (S0 measured run time minus the 15.568 us reference). This applies ONLY where the "
                "waited-on predecessor is the injected task. Keep the 1 us cap unchanged. S0 itself stays bound "
                "by its 942 us injected-task budget.")
    return dict(
        r8_profile, schema=1, label=LABEL, user_decision=DECISION,
        r8_user_decision=r8_profile["user_decision"],
        decisions=dict(r8_profile["decisions"], release_injected_predecessor=decision),
        release_injected_predecessor=dict(
            cap_ps=CAP_PS, rows=rows,
            rule=("Only for a declared row (scene, task T, injected task X) and only when the run shows: the "
                  "R9 budget applied to X (X DONE on the injected slot, exactly that one [BINGO_SDC_INJECT] "
                  "line) and passed (run_X <= 942 us); X's whole run lies in T's release window (eligible_T <= "
                  "DISPATCH_X, DONE_X <= DISPATCH_T); T is the first DISPATCH after DONE_X. Then gate_extra_T = "
                  "max(0, latency_T - max(0, run_X - reference_run_X) - L_ref_T) <= 1 us. Otherwise, and for "
                  "every other task and scene, the unchanged r1 extra = max(0, latency - L_ref) <= 1 us."),
            derivation=("T waits on X when, in the frozen L_ref capture of the scene's configuration, X's whole "
                        "run lies in T's release window and T is the first DISPATCH after X's DONE; the frozen "
                        "tagged model must first fetch T in the tick in which X dispatches and writes DONE.")),
        r8_profile_binding=dict(sha256=R8_PROFILE_SHA))


def freeze(directory, profile):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "gate_profile_r9.json"
    profile = dict(profile, frozen_at=datetime.datetime.now().astimezone().isoformat())
    data = json.dumps(profile, indent=2, sort_keys=True).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    hash_path = directory / "gate_profile_r9.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  gate_profile_r9.json\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def load(binding):
    path, hash_path = Path(binding["path"]), Path(binding["hash_path"])
    if sha(path) != binding["sha256"] or hash_path.read_text() != binding["sha256"] + "  gate_profile_r9.json\n":
        raise ProfileError("Frozen r9 gate profile SHA256 mismatch")
    if any(p.stat().st_mode & 0o222 for p in (path, hash_path)):
        raise ProfileError("Frozen r9 gate profile must stay read-only")
    profile = json.loads(path.read_text())
    if profile["schema"] != 1 or profile["label"] != LABEL:
        raise ProfileError("Unknown gate profile record")
    return profile


def get(table, task):
    return table[task] if task in table else table[str(task)]


def release(judged, name, profile):
    """Replace only the declared waiting row's release check; every other row keeps the r1 check."""
    judged = dict(judged, problems=list(judged["problems"]))
    rows = judged["release"]["tasks"]
    flagged = [p for p in judged["problems"] if p.startswith(RELEASE_LABEL)]
    failing = any(not row["pass"] for row in rows.values())
    if len(flagged) > 1 or bool(flagged) != failing or (
            flagged and ast.literal_eval(flagged[0][len(RELEASE_LABEL):]) != json.loads(json.dumps(rows))):
        raise ProfileError(f"{name}: unchanged judge disagrees with its own release rows")
    declared = profile["release_injected_predecessor"]["rows"].get(name)
    if declared is None:
        judged["release_adjustment"] = None
        return judged
    task, injected = declared["task"], declared["injected_task"]
    dispatch, done = judged["dispatch"], judged["done"]
    spent = judged["injected_task_budget"]
    row = dict(rows[str(task)])
    run = get(done, injected) - get(dispatch, injected)
    checks = dict(
        injected_budget_applied=bool(spent and spent["task"] == injected and spent["applied"]),
        injected_budget_passed=bool(spent and spent["passed"] and spent["measured_ps"] == run
                                    and run <= declared["injected_budget_ps"]),
        injected_run_inside_release_window=(row["eligible_ps"] <= get(dispatch, injected)
                                            and get(done, injected) <= row["dispatch_ps"]),
        task_is_first_dispatch_after_injected_done=first_dispatch_after(
            {int(k): v for k, v in dispatch.items()}, get(done, injected)) == [task])
    applied = all(checks.values())
    increase = max(0, run - declared["reference_run_ps"])
    gate_extra = max(0, row["latency_ps"] - increase - row["L_ref_ps"]) if applied else row["extra_ps"]
    row.update(r1_pass=row["pass"], gate_extra_ps=gate_extra,
               injected_run_increase_ps=increase if applied else None)
    row["pass"] = gate_extra <= profile["release_injected_predecessor"]["cap_ps"]
    tasks = dict(rows, **{str(task): row})
    judged["release"] = dict(judged["release"], tasks=tasks,
                             status="PASS" if all(r["pass"] for r in tasks.values()) else "FAIL",
                             rule="r9 profile decision 3")
    judged["release_adjustment"] = dict(
        task=task, injected_task=injected, applied=applied, checks=checks,
        injected_run_ps=run, reference_run_ps=declared["reference_run_ps"], injected_run_increase_ps=increase,
        latency_ps=row["latency_ps"], L_ref_ps=row["L_ref_ps"], extra_ps=row["extra_ps"],
        gate_extra_ps=gate_extra, cap_ps=profile["release_injected_predecessor"]["cap_ps"], passed=row["pass"])
    judged["problems"] = [p for p in judged["problems"] if not p.startswith(RELEASE_LABEL)]
    if judged["release"]["status"] != "PASS":
        judged["problems"].append(RELEASE_LABEL + str(tasks))
    judged["status"] = "FAIL" if judged["problems"] else "PASS"
    return judged


def judge(name, scene, log, uart, observer, sealed, reference, record, profile):
    """The r10 judge (r1, R9 budget, r8 decisions 1-2), then decision 3 on the declared release row."""
    judged = r10.judge(name, scene, log, uart, observer, sealed, reference, record, profile)
    return release(judged, name, profile)
