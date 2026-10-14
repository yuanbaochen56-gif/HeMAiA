"""Check of the bingo power-domain counters (BINGO_PM_COUNT) in a system run.

A pm_count scene builds its host image with -DBINGO_PM_COUNT_LOG: after the
event log the host prints the configured levels and, for the domain of each
cluster c (domain 1 + c), the five counters ([BINGO_PM_COUNT] UART lines):
manager cycles at the idle, normal and boost level, at any other level, and
with an active core. The simulation log has every level the PM applied
([BINGO_PM] t domain=d level=L: from the edge after t on, the domain runs at
L), and the dispatches and dones (+bingo_dispatch_log).

Level counters. With the manager clock period P (from the log's own grid), the
counter of a level class equals the time the domain spent at levels of that
class, divided by P, up to the moment the host read it. That moment is not in
the log; the check finds it: the host read time t_r of a domain is the one
point on the clock grid at which all four level counters equal the residency
of the log, and it must lie before the end of the run. Every class but the
current one at t_r is then an exact match, and the current one fixes t_r.

Active counter. A core is active from its dispatch until it asks for its next
task, so the counter is at least the union of the dispatch-to-done intervals of
the domain's cores (not the extra slot of a cluster, which libbingo leaves out
of every domain) and at most the time from reset to t_r.

Energy. The relative energy proxy of the evaluation (bingo_c5: dynamic 6 / level
per normal-level time, voltage fixed) from the counters and the configured
levels equals the one from the log over [first level write, t_r] exactly
(idle, normal and boost; the other class has no single level).
"""
from fractions import Fraction
from math import gcd
import re

from bingo_log import DISPATCH_RE, DONE_RE, EOC_RE, PM_RE

PM_COUNT_LEVELS_RE = re.compile(r"\[BINGO_PM_COUNT\] levels idle=(\d+) normal=(\d+) boost=(\d+)")
PM_COUNT_RE = re.compile(r"\[BINGO_PM_COUNT\] cluster=(\d+) idle=(\d+) normal=(\d+) "
                         r"boost=(\d+) other=(\d+) active=(\d+)")
CLASSES = ("idle", "normal", "boost", "other")
ANY_EVENT_RE = re.compile(r"\[BINGO_[A-Z_]+\] (\d+) ")


def level_class(level, idle, normal, boost):
    """bingo_hw_manager_pm_counters: idle, normal, boost (if not 0), other; level 0 none."""
    if level == 0:
        return None
    if level == idle:
        return "idle"
    if level == normal:
        return "normal"
    if boost and level == boost:
        return "boost"
    return "other"


def clock_period(log):
    """The manager clock period: the greatest common divisor of the distances
    between the [BINGO_*] events of the log (all on manager clock edges)."""
    times = sorted({int(t) for t in ANY_EVENT_RE.findall(log)})
    period = 0
    for a, b in zip(times, times[1:]):
        period = gcd(period, b - a)
    return period


def read_time(events, counts, levels, period):
    """The grid point t at which the level counters equal the residency of the
    (time, level) events up to t; None if there is none."""
    base = {k: 0 for k in CLASSES}
    for i, (time, level) in enumerate(events):
        cls = level_class(level, *levels)
        end = events[i + 1][0] if i + 1 < len(events) else None
        others_match = all(counts[k] == base[k] for k in CLASSES if k != cls)
        if others_match:
            if cls is None:
                if counts == base:
                    return time
            else:
                n = counts[cls] - base[cls]
                if n >= 0 and (end is None or time + n * period <= end):
                    return time + n * period
        if end is not None and cls is not None:
            if (end - time) % period:
                raise ValueError(f"level interval {time}..{end} is not on the {period} ps grid")
            base[cls] += (end - time) // period
    return None


def busy_union(log, cluster, cores, upto):
    """Union of the dispatch-to-done intervals of cores 0 .. cores - 1 of one
    cluster (ps): the cores of its domain (libbingo leaves the extra slot, the
    host or a tied-off one, out of every domain)."""
    start = {}
    intervals = []
    events = ([(int(t), "D", int(core)) for t, _, _, core, cl in DISPATCH_RE.findall(log)
               if int(cl) == cluster and int(core) < cores] +
              [(int(t), "E", int(core)) for t, _, _, core, cl in DONE_RE.findall(log)
               if int(cl) == cluster and int(core) < cores])
    for time, kind, core in sorted(events):
        if kind == "D":
            start[core] = time
        elif core in start:
            intervals.append((start.pop(core), min(time, upto)))
    total, current = 0, None
    for a, b in sorted(intervals):
        if current is None or a > current[1]:
            if current:
                total += current[1] - current[0]
            current = [a, b]
        else:
            current[1] = max(current[1], b)
    if current:
        total += current[1] - current[0]
    return total


def pm_count_problems(log, uart, clusters, cores, period=None, normal_reference=6):
    """Problems of a pm_count scene, and the per-domain results. cores: the
    cores per cluster in its domain (the slots before the extra one). period: the
    manager clock period in ps (default: the grid of the log's events; a grid
    coarser than the clock makes the counters match nothing, never pass)."""
    problems, results = [], {}
    levels = PM_COUNT_LEVELS_RE.findall(uart)
    rows = {int(m[0]): dict(zip(CLASSES + ("active",), map(int, m[1:]))) for m in PM_COUNT_RE.findall(uart)}
    if len(levels) != 1 or sorted(rows) != list(range(clusters)):
        return [f"pm_count: expected one levels line and counters of clusters 0..{clusters - 1}, "
                f"got {len(levels)} / {sorted(rows)}"], results
    idle, normal, boost = map(int, levels[0])
    period = period or clock_period(log)
    eoc = EOC_RE.findall(log)
    if not period or len(eoc) != 1:
        return [f"pm_count: clock period {period} or end of run {eoc} not found"], results
    t_eoc = int(eoc[0])
    timeline = {}
    for t, domain, level in PM_RE.findall(log):
        timeline.setdefault(int(domain), []).append((int(t), int(level)))
    for cluster in range(clusters):
        domain, counts = 1 + cluster, rows[cluster]
        events = timeline.get(domain, [])
        if not events:
            problems.append(f"pm_count: domain {domain} has no level events")
            continue
        t_read = read_time(events, {k: counts[k] for k in CLASSES}, (idle, normal, boost), period)
        result = dict(counts=counts, events=len(events), period_ps=period, t_read_ps=t_read)
        results[domain] = result
        if t_read is None:
            problems.append(f"pm_count: domain {domain} counters {counts} match no read time of the level log")
            continue
        if t_read > t_eoc:
            problems.append(f"pm_count: domain {domain} read at {t_read} after the end of the run {t_eoc}")
        # active: between the busy intervals and the time since reset
        busy = busy_union(log, cluster, cores, t_read)
        result["busy_union_cycles"] = busy // period
        if not busy // period <= counts["active"] <= t_read // period:
            problems.append(f"pm_count: domain {domain} active {counts['active']} outside "
                            f"[{busy // period}, {t_read // period}]")
        # energy proxy (dynamic part, normal-level ms): counters vs log
        energy_counters = sum(Fraction(normal_reference, lvl) * counts[cls] * period
                              for cls, lvl in (("idle", idle), ("normal", normal), ("boost", boost))
                              if lvl) / 10 ** 9
        energy_log = Fraction(0)
        for (t, level), following in zip(events, events[1:] + [(t_read, None)]):
            end = min(following[0], t_read)
            if end > t and level_class(level, idle, normal, boost) in ("idle", "normal", "boost"):
                energy_log += Fraction(normal_reference, level) * (end - t)
        energy_log /= 10 ** 9
        result["energy_counters"] = float(energy_counters)
        result["energy_log"] = float(energy_log)
        if energy_counters != energy_log:
            problems.append(f"pm_count: domain {domain} energy {float(energy_counters)} from the counters, "
                            f"{float(energy_log)} from the log")
    return problems, results
