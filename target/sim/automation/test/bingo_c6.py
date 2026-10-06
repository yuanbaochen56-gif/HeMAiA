"""C6 hardware vs software CERF degradation vs none (DESIGN 9.24.8).

Per run: the C3 v3 class, the stuck report, the manager's CERF update (hardware
only), the first backup dispatch, T_evlog and EOC. Per workload: one image, the
same per-slot dispatch sequences in every variant that recovers, and the
differences that C6 reports (stuck -> first backup dispatch, T_evlog).
The software variant's CERF write has no print of its own; it is bounded by the
stuck report and the first backup dispatch, which C6 reports for every variant.
"""
import json
import re

from bingo_c2 import TASK_EVENT
from bingo_c3 import classify, outcome_candidates

C6_LINE = re.compile(r"\[Host\] C6 cerf_fb_mode=(\d+) poll_cycles=(\d+) "
                     r"sw_writes=(\d+) sw_evt=0x([0-9a-fA-F]+)")
STUCK = re.compile(r"\[BINGO_REPLAY_STUCK\] (\d+) ")
CERF_FB = re.compile(r"\[BINGO_CERF_FB\] (\d+) ")
EVLOG_BEGIN = re.compile(r"\[EVLOG_BEGIN\] (\d+)")
EOC = re.compile(r"All chips finished successfully at (\d+)")
EXPECTED_CLASS = {"hw": "degraded", "sw": "degraded", "none": "detected-stuck"}
SIGNALS = ("eoc", "checks_pass", "cerf", "stuck", "fence", "timeout")


def host_writes(uart):
    """CERF writes the host reports (C6 images only; 0 without the line)."""
    lines = C6_LINE.findall(uart)
    return int(lines[-1][2]) if lines else 0


def classify_c6(log, uart, **kwargs):
    """The C3 classifier sees only the manager's [BINGO_CERF_FB]. A recovery by the
    host's own CERF write reads as U5 there (stuck, checks pass, no CERF). Only in
    that case, and only if the host reports the write, the write is the CERF signal.
    """
    result = classify(log, uart, **kwargs)
    result["cerf_source"] = "manager" if result["cerf"] else None
    if result["class"] == "unclassified" and result["unclassified_reason"] == "U5" and host_writes(uart):
        signals = {key: result[key] for key in SIGNALS}
        signals["cerf"] = True
        candidates, reasons = outcome_candidates(signals, dict(result["prechecks"], U5=False))
        if len(candidates) != 1:
            raise ValueError(f"C6 partition violation: {signals}, {candidates}")
        result.update(cerf=True, cerf_source="host", unclassified_reason=";".join(reasons),
                      annot=";".join(filter(None, [result["annot"], "after_host_cerf"])),
                      **{"class": candidates[0]})
    return result


def dispatches(log):
    """[(time_ps, task, core, cluster)] of chip 0, in log order."""
    return [(int(time), int(task), int(core), int(cluster))
            for kind, time, chip, task, core, cluster in TASK_EVENT.findall(log)
            if kind == "DISPATCH" and int(chip) == 0]


def run_metrics(log, uart, backup_tasks):
    events = dispatches(log)
    stuck = [int(t) for t in STUCK.findall(log)]
    fb = [int(t) for t in CERF_FB.findall(log)]
    backup = [time for time, task, _, _ in events if task in backup_tasks]
    evlog = [int(t) for t in EVLOG_BEGIN.findall(log)]
    eoc = [int(t) for t in EOC.findall(log)]
    slots = {}
    for _, task, core, cluster in events:
        slots.setdefault(f"{core},{cluster}", []).append(task)
    first = lambda values: values[0] if values else None  # noqa: E731
    t_stuck, t_fb, t_backup = first(stuck), first(fb), min(backup) if backup else None
    return dict(t_stuck_ps=t_stuck, t_cerf_fb_ps=t_fb, t_backup_ps=t_backup,
                stuck_to_cerf_ps=t_fb - t_stuck if t_fb is not None and t_stuck is not None else None,
                stuck_to_backup_ps=t_backup - t_stuck if t_backup is not None and t_stuck is not None else None,
                T_evlog_ps=first(evlog), T_eoc_ps=first(eoc), host_writes=host_writes(uart),
                n_stuck=len(stuck), n_cerf_fb=len(fb), n_backup=len(backup), slots=slots)


def group_problems(rows):
    """One workload's runs: dicts with mode, fault, image_id and run_metrics()."""
    problems = []
    if len({row["image_id"] for row in rows}) != 1:
        problems.append("the variants use more than one image")
    for fault in (True, False):
        group = [row for row in rows if row["fault"] == fault and row["mode"] != "none"]
        if len({json.dumps(row["slots"], sort_keys=True) for row in group}) > 1:
            problems.append(f"{'fault' if fault else 'healthy'} variants differ in per-slot dispatches")
    for row in rows:
        if row["mode"] == "none" and (row["n_backup"] or row["n_cerf_fb"] or row["host_writes"]
                                      or not row["n_stuck"] or row["T_eoc_ps"] is not None):
            problems.append(f"{row['scenario']}: fail-stop must report stuck and never degrade or end")
    return problems


def summary(rows):
    """Per fault variant: stuck -> first backup and T_evlog; the cost against the
    healthy control of the same variant; the difference to the hardware variant."""
    key = {(row["mode"], row["poll"], row["fault"]): row for row in rows}
    hw, hw_ok = key.get(("hw", 0, True)), key.get(("hw", 0, False))

    def minus(a, b, field):
        return a[field] - b[field] if a and b and a[field] is not None and b[field] is not None else None

    result = []
    for (mode, poll, fault), row in sorted(key.items()):
        if not fault:
            continue
        control = key.get((mode, poll, False))
        result.append(dict(
            variant=mode if mode != "sw" else f"sw{poll}", cls=row["cls"],
            stuck_to_cerf_ps=row["stuck_to_cerf_ps"], stuck_to_backup_ps=row["stuck_to_backup_ps"],
            T_evlog_ps=row["T_evlog_ps"], fault_cost_ps=minus(row, control, "T_evlog_ps"),
            stuck_to_backup_vs_hw_ps=minus(row, hw, "stuck_to_backup_ps"),
            T_evlog_vs_hw_ps=minus(row, hw, "T_evlog_ps"),
            healthy_overhead_vs_hw_ps=minus(control, hw_ok, "T_evlog_ps")))
    return result
