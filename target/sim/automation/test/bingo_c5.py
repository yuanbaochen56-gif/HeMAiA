"""C5 power / recovery trade-off (DESIGN 9.24.7).

Energy proxy: voltage fixed, PM level = clock divider d, dynamic power 6/d
relative to the normal level 6, plus a constant leakage L per domain:
E = sum over cluster domains of integral (6/d(t) + L) dt, in normal-domain ms.
Host, quadrant and interconnect power are not modelled.
"""
from collections import defaultdict
from fractions import Fraction
import re

from bingo_c2 import PM_EVENT, TASK_EVENT
from bingo_c4 import run_boundaries

NORMAL_LEVEL = 6
LEVELS = {3, 6, 12, 25}  # boost, normal, access servo, idle in the C5 configurations
LEAKAGE = (Fraction(0), Fraction(1, 10))
FENCE = re.compile(r"\[BINGO_WD\] (\d+) chip=\d+ core=\d+ cluster=\d+ dead_suspect=1 fenced=1")
PS_PER_MS = 10 ** 9


def pm_timeline(log):
    """{domain: [(time_ps, level), ...]} in log order."""
    timeline = defaultdict(list)
    for match in PM_EVENT.finditer(log):
        time, domain, level = map(int, match.groups())
        if level not in LEVELS:
            raise ValueError(f"unexpected PM level {level} at {time}")
        if timeline[domain] and time < timeline[domain][-1][0]:
            raise ValueError(f"PM events of domain {domain} out of order at {time}")
        timeline[domain].append((time, level))
    return dict(timeline)


def pm_start(timeline):
    """First time every domain has a known level (the PM initialisation)."""
    if not timeline:
        raise ValueError("no PM events")
    return max(events[0][0] for events in timeline.values())


def residency(events, start, end):
    """{level: ps} of one domain over [start, end); the level at start must be known."""
    if end < start:
        raise ValueError("window ends before it starts")
    if not events or events[0][0] > start:
        raise ValueError("domain level unknown at the window start")
    shares = defaultdict(int)
    for (time, level), following in zip(events, events[1:] + [(None, None)]):
        segment_end = end if following[0] is None else min(following[0], end)
        segment_start = max(time, start)
        if segment_end > segment_start:
            shares[level] += segment_end - segment_start
    return dict(shares)


def energy(timeline, start, end, leakage):
    """Proxy energy over [start, end) in normal-domain ms (exact fraction)."""
    total = Fraction(0)
    for events in timeline.values():
        for level, ps in residency(events, start, end).items():
            total += (Fraction(NORMAL_LEVEL, level) + leakage) * ps
    return total / PS_PER_MS


def dispatch_signature(log):
    return [(int(task), int(core), int(cluster))
            for kind, _, _, task, core, cluster in TASK_EVENT.findall(log) if kind == "DISPATCH"]


def run_metrics(log, uart, graph_csv, fault):
    run = run_boundaries(log, uart, graph_csv)
    timeline = pm_timeline(log)
    start, end = pm_start(timeline), run["T_evlog_ps"]
    fences = [int(t) for t in FENCE.findall(log)]
    if fault != bool(fences):
        raise ValueError(f"fault={fault} but {len(fences)} fence events")
    result = dict(T0_ps=run["T0_ps"], T_pm0_ps=start, T_exit_ps=run["T_exit_ps"],
                  T_evlog_ps=end, T_eoc_ps=run["T_eoc_ps"],
                  makespan_ps=end - run["T0_ps"],
                  T_fence_ps=fences[0] if fences else None,
                  recovery_ps=end - fences[0] if fences else None,
                  pm_events=sum(len(v) for v in timeline.values()),
                  levels_seen=sorted({level for v in timeline.values() for _, level in v}),
                  recovery_hold_events=log.count("[BINGO_RECOVERY_HOLD]"),
                  dispatches=dispatch_signature(log))
    for domain, events in sorted(timeline.items()):
        for level, ps in sorted(residency(events, start, end).items()):
            result[f"d{domain}_l{level}_ps"] = ps
    for leakage in LEAKAGE:
        tag = f"L{float(leakage):g}"
        result[f"E_{tag}"] = float(energy(timeline, start, end, leakage))
        if fences:
            result[f"E_{tag}_before_fence"] = float(energy(timeline, start, fences[0], leakage))
            result[f"E_{tag}_after_fence"] = float(energy(timeline, fences[0], end, leakage))
    return result


def knob_problems(scene, metrics):
    """G4: every configured knob visibly acted (or, for W without a fence, did not)."""
    knobs, fault, problems = scene["c5_knobs"], scene["c5_fault"], []
    if fault and knobs["W"] and not metrics["recovery_hold_events"]:
        problems.append("W > 0 but no RECOVERY_HOLD event")
    if not knobs["W"] and metrics["recovery_hold_events"]:
        problems.append("RECOVERY_HOLD event with W = 0")
    if knobs["boost"] and fault and 3 not in metrics["levels_seen"]:
        problems.append("boost on but no level-3 interval in a fault run")
    if (not knobs["boost"] or not fault) and 3 in metrics["levels_seen"]:
        problems.append("level 3 without boost or in a healthy run")
    if knobs["S"] and knobs["S"] not in metrics["levels_seen"]:
        problems.append(f"servo level {knobs['S']} never used")
    if not knobs["S"] and 12 in metrics["levels_seen"]:
        problems.append("level 12 without servo")
    return problems


def group_problems(rows):
    """G3 and G5 over all runs: same dispatch mapping per fault/healthy, same PM start."""
    problems = []
    for fault in (True, False):
        signatures = {tuple(map(tuple, row["dispatches"])) for row in rows if row["fault"] == fault}
        if len(signatures) > 1:
            problems.append(f"{'fault' if fault else 'healthy'} runs dispatch differently")
    if len({row["T_pm0_ps"] for row in rows}) > 1:
        problems.append("PM initialisation time differs between runs of one image")
    return problems


def pairs(rows):
    """Fault minus healthy with the same knobs."""
    healthy = {tuple(sorted(row["knobs"].items())): row for row in rows if not row["fault"]}
    result = []
    for row in rows:
        if not row["fault"]:
            continue
        match = healthy.get(tuple(sorted(row["knobs"].items())))
        if match is None:
            continue
        record = dict(fault=row["scenario"], healthy=match["scenario"], **row["knobs"])
        for key in ("T_evlog_ps", "makespan_ps", "E_L0", "E_L0.1"):
            record["delta_" + key] = row[key] - match[key]
        result.append(record)
    return result
