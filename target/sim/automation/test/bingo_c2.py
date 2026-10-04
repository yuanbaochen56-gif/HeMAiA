"""C2 no-fault measurements and comparisons (DESIGN 9.24.2).

PM arithmetic comes from the unchanged evaluation pm_ticks.py on PYTHONPATH.
Timing differences are measurements, never healthy-run gate failures.
"""
from collections import defaultdict
import csv
from fractions import Fraction
from pathlib import Path
import re

from bingo_evlog import ITEM

TASK_EVENT = re.compile(
    r"\[BINGO_(DISPATCH|DONE)\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
RETIRED = re.compile(r"\[BINGO_RETIRED\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+)")
PM_EVENT = re.compile(r"\[BINGO_PM\] (\d+) domain=(\d+) level=(\d+)")
UNHEALTHY = re.compile(
    r"\[BINGO_(?:WD|FENCE|REPLAY[^]]*|REMAP|CERF_FB|LATE|RISK[^]]*|"
    r"TYPE_CONFIRM|PARK|RECOVERY_HOLD|EXPORT|IMPORT|REMOTE_[^]]*|ASSERT)\]")
STATUS_ZERO = (
    "replay_stuck", "remote_done_mismatch", "link_error", "fenced", "dead_suspect",
    "remote_timeout", "park_fail", "cerf_fb_evt", "risk", "replay_blocked",
)


def graph_tasks(graph_csv):
    with Path(graph_csv).open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    tasks = {}
    for row in rows:
        task = int(row["ID"])
        if task in tasks:
            raise ValueError(f"duplicate graph task {task}")
        tasks[task] = dict(task=task, kernel=row["Kernel"], chip=int(row["Chiplet"], 16),
                           cluster=int(row["Cluster"]), core=int(row["Core"]), type=row["Type"])
    return tasks


def _slot(task):
    return task["chip"], task["core"], task["cluster"]


def _event(line, match):
    canonical = line[match.start():]
    time = int(match[2] if match.re is TASK_EVENT else match[1])
    payload = re.sub(r"(\[[A-Z0-9_]+\]) \d+", r"\1 TIME", canonical, count=1)
    return dict(time_ps=time, payload=payload, raw=line)


def _parse(log):
    tasks, streams, pm = [], defaultdict(list), defaultdict(list)
    for line in log.splitlines():
        match = TASK_EVENT.search(line)
        if match:
            kind, time, chip, task, core, cluster = match.groups()
            event = _event(line, match)
            event.update(kind=kind, chip=int(chip), task=int(task), core=int(core),
                         cluster=int(cluster))
            tasks.append(event)
            streams[f"slot:{chip}:{cluster}:{core}:DISPATCH_DONE"].append(event)
        match = RETIRED.search(line)
        if match:
            time, chip, core, cluster = match.groups()
            streams[f"slot:{chip}:{cluster}:{core}:RETIRED"].append(_event(line, match))
        match = PM_EVENT.search(line)
        if match:
            domain = int(match[2])
            event = _event(line, match)
            streams[f"pm:{domain}"].append(event)
            pm[domain].append(line[line.index("[BINGO_PM]"):])
    return tasks, dict(streams), dict(pm)


def healthy_problems(log, uart, graph_csv):
    """The G3 checks, in addition to the driver's existing checker."""
    problems = []
    for line in log.splitlines():
        if UNHEALTHY.search(line):
            problems.append(f"unhealthy control event: {line}")
    statuses = [line for line in uart.splitlines() if "Bingo status:" in line]
    if not statuses:
        problems.append("missing Bingo status line")
    for line in statuses:
        for field in STATUS_ZERO:
            values = re.findall(rf"\b{field}=(0x[0-9a-fA-F]+|\d+)\b", line)
            if len(values) != 1 or int(values[0], 0) != 0:
                problems.append(f"healthy status {field} must be 0: {line}")
    checks = re.findall(r"Check \[([^\]]*)\]:\s*(\S+)", uart)
    for name, status in checks:
        if status != "PASS":
            problems.append(f"Check [{name}]: {status}")
    try:
        graph = graph_tasks(graph_csv)
        events, _, _ = _parse(log)
        exits = defaultdict(list)
        for event in events:
            task = graph.get(event["task"])
            if event["kind"] == "DISPATCH" and task and task["kernel"].endswith("kernel_exit"):
                if _slot(task) == _slot(event):
                    exits[event["cluster"], event["core"]].append(event["time_ps"])
        retirement = defaultdict(list)
        for match in RETIRED.finditer(log):
            time, chip, core, cluster = map(int, match.groups())
            retirement[cluster, core].append(time)
        for ts, code, cluster, core, arg in ITEM.findall(uart):
            slot = int(cluster), int(core)
            if int(code, 16) != 0x0B:
                problems.append(f"healthy EVLOG must be 0x0B RETIRED: code=0x{code} slot={cluster}:{core}")
            elif not any(start <= end for start in exits[slot] for end in retirement[slot]):
                problems.append(f"EVLOG RETIRED without dispatched exit on slot {cluster}:{core}")
    except (OSError, KeyError, ValueError) as error:
        problems.append(f"healthy graph check: {error}")
    return problems


def run_metrics(log, uart, graph_csv, pm_config):
    from pm_ticks import integrate_ticks

    graph = graph_tasks(graph_csv)
    events, streams, pm = _parse(log)
    dispatches = [event for event in events if event["kind"] == "DISPATCH"]
    if not dispatches:
        raise ValueError("no BINGO_DISPATCH")
    t0 = dispatches[0]["time_ps"]
    host_exits = [event for event in dispatches
                  if graph[event["task"]]["kernel"] == "__host_bingo_kernel_exit"]
    if len(host_exits) != 1:
        raise ValueError("expected one dispatched host exit")
    boundaries = [(int(match[1]), line) for line in log.splitlines()
                  for match in [re.search(r"\[EVLOG_BEGIN\] (\d+)", line)] if match]
    if len(boundaries) != 1:
        raise ValueError("expected one EVLOG_BEGIN")
    eocs = re.findall(r"All chips finished successfully at (\d+)", log)
    if len(eocs) != 1:
        raise ValueError("expected one successful EOC")
    streams["global:T_exit"] = [host_exits[0]]
    streams["global:T_evlog"] = [
        dict(time_ps=boundaries[0][0], payload="[EVLOG_BEGIN] TIME", raw=boundaries[0][1])]
    pending, measured = {}, {}
    for event in events:
        task_id, slot = event["task"], _slot(event)
        if task_id not in graph or _slot(graph[task_id]) != slot:
            raise ValueError(f"event task/slot not in graph: {event['raw']}")
        if event["kind"] == "DISPATCH":
            if slot in pending or task_id in measured:
                raise ValueError(f"duplicate/busy dispatch: {event['raw']}")
            pending[slot] = event
            measured[task_id] = dict(dispatch_ps=event["time_ps"], done_ps=None,
                                     busy_ps=None, busy_ticks=None, busy_ticks_exact=None,
                                     rel_dispatch_ps=event["time_ps"] - t0)
        else:
            start = pending.pop(slot, None)
            if start is None or start["task"] != task_id or event["time_ps"] <= start["time_ps"]:
                raise ValueError(f"unmatched DONE: {event['raw']}")
            domain = (pm_config.domain(event["core"], event["cluster"])
                      if (event["core"], event["cluster"]) in pm_config.slot_domains else None)
            integral = integrate_ticks(start["time_ps"], event["time_ps"],
                                       pm.get(domain, []), pm_config)
            measured[task_id].update(done_ps=event["time_ps"],
                                     busy_ps=event["time_ps"] - start["time_ps"],
                                     busy_ticks=integral["ticks"],
                                     busy_ticks_exact=integral["ticks_exact"])
    if list(pending.values()) != [host_exits[0]]:
        raise ValueError("only terminal host exit may lack DONE")
    tasks = []
    for task in graph.values():
        row = dict(task)
        row.pop("type")
        types = getattr(pm_config, "core_types", {})
        row["core_type"] = types.get((task["cluster"], task["core"]), types.get(task["core"], ""))
        row.update(measured.get(task["task"], dict(dispatch_ps=None, done_ps=None, busy_ps=None,
                                                   busy_ticks=None, busy_ticks_exact=None,
                                                   rel_dispatch_ps=None)))
        tasks.append(row)
    return dict(T0_ps=t0, T_exit_ps=host_exits[0]["time_ps"], T_evlog_ps=boundaries[0][0],
                T_eoc_ps=int(eocs[0]), makespan_hw_ps=host_exits[0]["time_ps"] - t0,
                host_view_ps=boundaries[0][0] - t0, n_dispatch=len(dispatches),
                n_done=len(events) - len(dispatches), evlog_items=len(ITEM.findall(uart)),
                tasks=tasks, streams=streams, pm_config=pm_config, graph=graph)


def _compare_streams(a, b, *, relative=False, streams_a=None, streams_b=None):
    left = a["streams"] if streams_a is None else streams_a
    right = b["streams"] if streams_b is None else streams_b
    differences = []
    for name in sorted(left.keys() | right.keys()):
        first, second = left.get(name, []), right.get(name, [])
        for index in range(max(len(first), len(second))):
            x = first[index] if index < len(first) else None
            y = second[index] if index < len(second) else None
            def signature(event, run):
                return None if event is None else (
                    event["time_ps"] - (run["T0_ps"] if relative else 0), event["payload"])
            if signature(x, a) != signature(y, b):
                differences.append(dict(stream=name, index=index,
                                        a=x["raw"] if x else "<missing>",
                                        b=y["raw"] if y else "<missing>"))
                break
    return differences


def _deltas(a, b):
    return dict(t0_delta_ps=b["T0_ps"] - a["T0_ps"],
                d_makespan_hw_ps=b["makespan_hw_ps"] - a["makespan_hw_ps"],
                d_host_view_ps=b["host_view_ps"] - a["host_view_ps"])


def compare_same_image(a, b):
    absolute, relative = _compare_streams(a, b), _compare_streams(a, b, relative=True)
    tasks = compare_cross_image(a, b, task_mapping(a, b))
    return dict(same_image=True, identical_abs=not absolute, identical_rel=not relative,
                first_diffs=absolute, first_diffs_rel=relative,
                max_abs_d_busy_ps=tasks["max_abs_d_busy_ps"],
                max_abs_d_busy_ticks=tasks["max_abs_d_busy_ticks"], **_deltas(a, b))


def task_mapping(a, b, *, allow_fallback_only=False):
    """a is the registered-fallback image; b is its no-fallback counterpart."""
    def grouped(run):
        groups = defaultdict(list)
        for task in run["tasks"]:
            groups[task["kernel"], task["chip"], task["cluster"], task["core"]].append(task["task"])
        return {key: sorted(values) for key, values in groups.items()}
    left, right = grouped(a), grouped(b)
    pairs, fallback = [], []
    for key in sorted(left.keys() | right.keys()):
        x, y = left.get(key, []), right.get(key, [])
        if len(y) > len(x) or len(x) != len(y) and not allow_fallback_only:
            raise ValueError(f"unmatched tasks for {key}: {x} != {y}")
        pairs.extend(zip(x, y))
        fallback.extend(x[len(y):])
    return dict(pairs=pairs, fallback_only=fallback)


def compare_cross_image(a, b, task_map):
    from pm_ticks import strip_short_idle_windows

    left = {task["task"]: task for task in a["tasks"]}
    right = {task["task"]: task for task in b["tasks"]}
    pairs, fallback = task_map["pairs"], task_map.get("fallback_only", [])
    a_ids, b_ids = [x for x, y in pairs], [y for x, y in pairs]
    if (len(a_ids) != len(set(a_ids)) or len(b_ids) != len(set(b_ids))
            or len(fallback) != len(set(fallback)) or set(a_ids) & set(fallback)
            or set(a_ids) | set(fallback) != set(left) or set(b_ids) != set(right)):
        raise ValueError("task mapping is incomplete or not unique")
    deltas = []
    for x, y in pairs:
        first, second = left[x], right[y]
        identity = ("kernel", "chip", "cluster", "core")
        if any(first[field] != second[field] for field in identity):
            raise ValueError(f"task mapping identity mismatch: {x} -> {y}")
        row = dict(task_a=x, task_b=y, kernel=first["kernel"], chip=first["chip"],
                   cluster=first["cluster"], core=first["core"], fallback_only=False)
        if (first["dispatch_ps"] is None) != (second["dispatch_ps"] is None):
            row["dispatch_presence_differs"] = True
        for field in ("busy_ps", "rel_dispatch_ps"):
            row["d_" + field] = (second[field] - first[field]
                                 if first[field] is not None and second[field] is not None else None)
        x_ticks, y_ticks = first["busy_ticks_exact"], second["busy_ticks_exact"]
        delta = Fraction(**y_ticks) - Fraction(**x_ticks) if x_ticks and y_ticks else None
        row.update(d_busy_ticks=float(delta) if delta is not None else None,
                   d_busy_ticks_exact=(dict(numerator=delta.numerator, denominator=delta.denominator)
                                       if delta is not None else None))
        deltas.append(row)
    for task in fallback:
        row = left[task]
        deltas.append(dict(task_a=task, task_b=None, kernel=row["kernel"], chip=row["chip"],
                           cluster=row["cluster"], core=row["core"], fallback_only=True,
                           dispatched=row["dispatch_ps"] is not None,
                           busy_ps=row["busy_ps"], busy_ticks=row["busy_ticks"],
                           busy_ticks_exact=row["busy_ticks_exact"]))
    def pm_streams(run):
        streams, removed = {}, {}
        for name, events in run["streams"].items():
            if not name.startswith("pm:"):
                continue
            _, windows = strip_short_idle_windows([event["raw"] for event in events], run["pm_config"])
            dropped = {window[field] for window in windows for field in ("idle_index", "normal_index")}
            streams[name] = [event for index, event in enumerate(events) if index not in dropped]
            removed[name] = windows
        return streams, removed
    pm_a, removed_a = pm_streams(a)
    pm_b, removed_b = pm_streams(b)
    first_diffs = _compare_streams(a, b, relative=True, streams_a=pm_a, streams_b=pm_b)
    for row in deltas:
        if not row["fallback_only"] and (
                row.get("dispatch_presence_differs") or row["d_busy_ps"] not in (None, 0)
                or row["d_rel_dispatch_ps"] not in (None, 0)):
            x, y = row["task_a"], row["task_b"]
            def raw(run, task):
                return " | ".join(event["raw"] for events in run["streams"].values()
                                  for event in events if event.get("task") == task
                                  and event.get("kind") in ("DISPATCH", "DONE"))
            first_diffs.append(dict(stream=f"task:{x}->{y}", index=0, a=raw(a, x), b=raw(b, y)))
    delta = _deltas(a, b)
    for name, field in (("T_exit", "d_makespan_hw_ps"), ("T_evlog", "d_host_view_ps")):
        if delta[field]:
            first_diffs.append(dict(stream=f"global:{name}", index=0,
                                    a=a["streams"][f"global:{name}"][0]["raw"],
                                    b=b["streams"][f"global:{name}"][0]["raw"]))
    return dict(same_image=False, identical_abs=None, identical_rel=not first_diffs,
                first_diffs=first_diffs, task_deltas=deltas,
                max_abs_d_busy_ps=max((abs(row["d_busy_ps"]) for row in deltas
                                      if row.get("d_busy_ps") is not None), default=0),
                max_abs_d_busy_ticks=max((abs(row["d_busy_ticks"]) for row in deltas
                                         if row.get("d_busy_ticks") is not None), default=0),
                pm_removed_a=removed_a, pm_removed_b=removed_b, join_wait_ps=None, **delta)
