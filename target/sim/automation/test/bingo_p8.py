"""P8 gates: recovery windows, inherited wake bounds and access-only levels."""
from bisect import bisect_right
from collections import defaultdict
import csv
from pathlib import Path
import re

PERIOD_PS = 28000
UPDATE_CYCLES = 5  # IDLE selection, frequency AW/W, clock-valid AW/W.
ACTIVE_DOMAINS = 2  # hemaia_ci: cluster + 1; host/tied-off slots are unmapped.
PM = re.compile(r"\[BINGO_PM\] (\d+) domain=(\d+) level=(\d+)")
HOLD = re.compile(r"\[BINGO_RECOVERY_HOLD\] (\d+) core=(\d+) cluster=(\d+) active=([01])")
DISPATCH = re.compile(r"\[BINGO_DISPATCH\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
DONE = re.compile(r"\[BINGO_DONE\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
FENCE = re.compile(r"\[BINGO_WD\] (\d+) chip=0 core=1 cluster=0 dead_suspect=1 fenced=1")


def recovery_hold_config(log):
    fences = [int(t) for t in FENCE.findall(log)]
    times = [int(t) for t, chip, task, core, cluster in DISPATCH.findall(log)
             if (int(chip), int(core), int(cluster)) == (0, 1, 1)]
    if len(fences) != 1 or not times or max(times) <= fences[0]:
        raise ValueError("cannot measure fence to substitute's last dispatch")
    span = max(times) - fences[0]
    if span % PERIOD_PS:
        raise ValueError("dispatch span is not an integral top-clock count")
    cycles = span // PERIOD_PS
    return dict(fence_ps=fences[0], last_substitute_dispatch_ps=max(times),
                dispatch_span_cycles=cycles, W_cycles=(cycles * 11 + 9) // 10)


def recovery_window(log, cycles):
    edges = [tuple(map(int, row)) for row in HOLD.findall(log)]
    if not cycles:
        if edges:
            raise ValueError("W=0 emitted RECOVERY_HOLD")
        return {}
    if len(edges) != 2 or [row[1:] for row in edges] != [(1, 1, 1), (1, 1, 0)]:
        raise ValueError("expected exactly substitute RECOVERY_HOLD start/end")
    start, end = edges[0][0], edges[1][0]
    fences = [int(t) for t in FENCE.findall(log)]
    if len(fences) != 1 or start != fences[0] + PERIOD_PS:
        raise ValueError("hold did not load on the post-SMT edge")
    if end - start != cycles * PERIOD_PS:
        raise ValueError("hold end did not match counter zero")
    rows = [(int(t), int(level)) for t, domain, level in PM.findall(log) if int(domain) == 2]
    before = [level for t, level in rows if t < start]
    if not before:
        raise ValueError("missing initial applied domain level")
    updates = [(t, level) for t, level in rows if start <= t < end]
    cold = before[-1] == 25
    wake = 0
    if cold:
        if not updates or updates[0][1] == 25:
            raise ValueError("first update after cold hold load is not non-idle")
        wake = (updates[0][0] - start) // PERIOD_PS
        if updates[0][0] - start > ACTIVE_DOMAINS * UPDATE_CYCLES * PERIOD_PS:
            raise ValueError("cold hold update exceeded L_wake")
    if any(level == 25 for _, level in updates):
        raise ValueError("applied idle update inside recovery window")
    return dict(start_ps=start, end_ps=end, cold=cold,
                inherited_wake_cycles=wake, L_wake_cycles=ACTIVE_DOMAINS * UPDATE_CYCLES)


def check_recovery_hold(log, uart, cycles):
    try:
        recovery_window(log, cycles)
        # The full A6 checker verifies code, slot, argument and exact timestamp.
        items = re.findall(r"\[EVLOG\].*?code=0x0[cC]\b", uart)
        if len(items) != (2 if cycles else 0):
            raise ValueError("wrong RECOVERY_HOLD event count in host log")
    except ValueError as error:
        return [str(error)]
    return []


def access_windows(log, graph_csv):
    """Split host checks by domain activity, PM updates and wake allowances."""
    with Path(graph_csv).open(newline="") as stream:
        checks = [int(row["ID"]) for row in csv.DictReader(stream)
                  if row["Kernel"] == "__host_bingo_kernel_check_result"]
    if len(checks) != 2 or len(set(checks)) != 2:
        raise ValueError("expected two generated host checks")
    host_slot = (0, 2, 0)
    events = sorted([(int(m[1]), m.start(), kind, tuple(map(int, m.groups())))
                     for kind, pattern in (("PM", PM), ("DISPATCH", DISPATCH), ("DONE", DONE))
                     for m in pattern.finditer(log)])
    starts, ends = defaultdict(list), defaultdict(list)
    domain_events = defaultdict(lambda: defaultdict(list))
    for time, _, kind, row in events:
        if kind == "PM":
            domain_events[row[1]][time].append((kind, row[2:]))
        else:
            _, chip, task, core, cluster = row
            if (chip, core, cluster) == host_slot:
                (starts if kind == "DISPATCH" else ends)[task].append(time)
            elif chip == 0:
                # hemaia_ci maps cluster slots to cluster + 1, not the host.
                domain_events[cluster + 1][time].append((kind, (core, task)))
    windows = []
    for task, domain in zip(checks, (1, 2)):
        if len(starts[task]) != 1 or len(ends[task]) != 1 or ends[task][0] <= starts[task][0]:
            raise ValueError(f"missing or ambiguous host-check span for task {task}")
        start, end = starts[task][0], ends[task][0]
        active, level, idle_since = {}, None, None
        states = []
        for time, updates in sorted(domain_events[domain].items()):
            if time > end:
                break
            was_busy = bool(active)
            for kind, values in updates:
                if kind == "PM":
                    level = values[0]
                else:
                    core, dispatched_task = values
                    if kind == "DISPATCH":
                        if core in active:
                            raise ValueError(f"overlapping domain {domain} core {core} dispatch")
                        active[core] = dispatched_task
                    else:
                        if active.get(core) != dispatched_task:
                            raise ValueError(f"unmatched domain {domain} core {core} DONE")
                        del active[core]
            if was_busy and not active:
                idle_since = time
            check_from = (idle_since + ACTIVE_DOMAINS * UPDATE_CYCLES * PERIOD_PS
                          if idle_since is not None else 0)
            states.append((time, bool(active), level, check_from))
        times = [state[0] for state in states]

        def sample(time):
            index = bisect_right(times, time) - 1
            if index < 0 or states[index][2] is None:
                raise ValueError(f"missing applied level for host check {task} domain {domain}")
            _, busy, applied, check_from = states[index]
            return dict(busy=busy, checked=not busy and time >= check_from, level=applied)

        boundaries = sorted({start, end} |
                            {time for time in times if start < time < end} |
                            {state[3] for state in states if start < state[3] < end})
        segments = []
        for left, right in zip(boundaries, boundaries[1:]):
            state = sample(left)
            if segments and all(segments[-1][key] == value for key, value in state.items()):
                segments[-1]["end_ps"] = right
            else:
                segments.append(dict(start_ps=left, end_ps=right, **state))
        windows.append(dict(task=task, domain=domain, dispatch_ps=start, done_ps=end,
                            duration_ps=end-start, segments=segments, done_state=sample(end),
                            L_wake_cycles=ACTIVE_DOMAINS * UPDATE_CYCLES))
    return windows


def check_access_level(log, level, graph_csv):
    """Apply the §9.23 servo gate only to non-busy, post-wake segments."""
    if level not in (6, 12, 25):
        return [f"unsupported P8 access gate level {level}"]
    try:
        windows = access_windows(log, graph_csv)
    except (OSError, ValueError, KeyError) as error:
        return [str(error)]
    problems = []
    for window in windows:
        values = [state["level"] for state in [*window["segments"], window["done_state"]]
                  if state["checked"]]
        bad = sorted(set(values) - {25, level})
        label = f"host check {window['task']} domain {window['domain']}"
        if bad:
            problems.append(f"{label} non-busy levels {bad}, allowed {sorted({25, level})}")
        if level != 25 and level not in values:
            problems.append(f"{label} has no non-busy access level {level}")
    return problems


def control_streams(log):
    """G2 per-slot/task/global comparison, excluding the intentionally changed PM."""
    streams = defaultdict(list)
    for kind, time, tail in re.findall(r"\[BINGO_([A-Z0-9_]+)\] (\d+)([^\n]*)", log):
        if kind in ("PM", "RECOVERY_HOLD"):
            continue
        core = re.search(r"\bcore=(\d+)", tail)
        cluster = re.search(r"\bcluster=(\d+)", tail)
        chip = re.search(r"\bchip=(\d+)", tail)
        task = re.search(r"\btask=(\d+)", tail)
        if kind in ("REPLAY", "REMAP") and task:
            key = f"task:{task[1]}"
        elif core and cluster:
            key = f"slot:{chip[1] if chip else 0}:{core[1]}:{cluster[1]}"
        else:
            key = "global"
        streams[key].append(f"[BINGO_{kind}]{tail}")
    return dict(streams)
