"""Exact A6 event correspondence and observation-only timing gates."""
from collections import defaultdict
import re

ITEM = re.compile(r"\[EVLOG\] ts=(\d+) code=0x([0-9a-fA-F]+) "
                  r"slot=(\d+):(\d+) arg=0x([0-9a-fA-F]+)")
SUMMARY = re.compile(r"\[EVLOG\] count=(\d+) dropped=(\d+)")


def simulation_events(log, cores_per_cluster):
    events = []
    state = {}
    reasons = {}
    exports = {(int(t), int(chip), int(task)) for t, chip, task in re.findall(
        r"\[BINGO_EXPORT\] (\d+) chip=(\d+) task=(\d+).*?\(replay 1\)", log)}
    for line in log.splitlines():
        m = re.search(r"\[BINGO_(?:RISK|TYPE)_CONFIRM\] (\d+) core=(\d+) cluster=(\d+)", line)
        if m:
            _, core, cluster = map(int, m.groups())
            reasons[(cluster, core)] = 1 if "[BINGO_RISK_CONFIRM]" in line else 2
        m = re.search(r"\[BINGO_WD\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) "
                      r"dead_suspect=(\d+) fenced=(\d+)", line)
        if m:
            time, chip, core, cluster, suspect, fenced = map(int, m.groups())
            if chip: raise ValueError("A6 system checker requires one chip")
            old_s, old_f = state.get((cluster, core), (0, 0))
            if suspect and not old_s and not fenced: events.append((time, 1, cluster, core, 0))
            if not suspect and old_s and not fenced: events.append((time, 2, cluster, core, 0))
            if fenced and not old_f:
                events.append((time, 3, cluster, core, reasons.get((cluster, core), 0)))
            state[(cluster, core)] = suspect, fenced
        m = re.search(r"\[BINGO_REPLAY\] (\d+) chip=(\d+) task=(\d+) type=(\d+) "
                      r"logical_core=\d+ from=(\d+) to=(\d+) cluster=(\d+) to_cluster=(\d+)", line)
        if m:
            time, chip, task, typ, src, dst, cluster, target_cluster = map(int, m.groups())
            if chip: raise ValueError("A6 system checker requires one chip")
            remote = ((time, chip, task) in exports or
                      src == dst and cluster == target_cluster)
            destination = 15 if remote else dst + target_cluster * cores_per_cluster
            events.append((time, 13 if typ == 1 else 4, cluster, src, destination * 4096 + task))
        m = re.search(r"\[BINGO_(REPLAY_STUCK|REPLAY_BLOCKED|RETIRED|RISK|PARK)\] (\d+) "
                      r"(?:chip=\d+ )?core=(\d+) cluster=(\d+)(.*)", line)
        if m:
            kind, time, core, cluster, tail = m.groups()
            code = {"REPLAY_STUCK": 5, "REPLAY_BLOCKED": 6, "RISK": 7, "RETIRED": 11}.get(kind)
            if kind == "PARK":
                if re.match(r" PARKED\b", tail): code = 8
                elif re.match(r" FAIL\b", tail): code = 9
            if code: events.append((int(time), code, int(cluster), int(core), 0))
        m = re.search(r"\[BINGO_CERF_FB\] (\d+) type (\d+) clear g(\d+) set g(\d+)", line)
        if m:
            time, typ, clear, set_group = map(int, m.groups())
            events.append((time, 10, typ >> 4, typ & 15, clear | (set_group << 5)))
        m = re.search(r"\[BINGO_RECOVERY_HOLD\] (\d+) core=(\d+) cluster=(\d+) active=([01])", line)
        if m:
            time, core, cluster, active = map(int, m.groups())
            events.append((time, 12, cluster, core, active))
        # Exit retired by the manager (bingo ed16cd7): slot = physical core that
        # would have run it, argument = logical flat slot << 12 | task id.
        m = re.search(r"\[BINGO_EXIT_ABSORB\] (\d+) chip=(\d+) task=(\d+) logical_core=(\d+) "
                      r"logical_cluster=(\d+) core=(\d+) cluster=(\d+)", line)
        if m:
            time, chip, task, logical_core, logical_cluster, core, cluster = map(int, m.groups())
            if chip: raise ValueError("A6 system checker requires one chip")
            logical = logical_core + logical_cluster * cores_per_cluster
            events.append((time, 15, cluster, core, logical * 4096 + task))
    return events


def check_evlog(log, uart, cores_per_cluster, period_ps=28000, *, allow_empty=False):
    """Every event must match, including its exact time; no tolerances."""
    problems = []
    items = [(int(ts), int(code, 16), int(cluster), int(core), int(arg, 16))
             for ts, code, cluster, core, arg in ITEM.findall(uart)]
    summaries = SUMMARY.findall(uart)
    if len(summaries) != 1: return ["expected exactly one EVLOG count/dropped summary"]
    count, dropped = map(int, summaries[0])
    if count != len(items): problems.append("EVLOG summary count differs from item count")
    if dropped: problems.append(f"EVLOG dropped={dropped}")
    if any(code & 128 for _, code, _, _, _ in items): problems.append("EVLOG has imprecise timestamps")
    expected = simulation_events(log, cores_per_cluster)
    if not items and not (allow_empty and not expected):
        problems.append("enabled EVLOG has no items")
    actual_groups, expected_groups = defaultdict(list), defaultdict(list)
    for ts, *signature in items: actual_groups[tuple(signature)].append(ts)
    for time, *signature in expected: expected_groups[tuple(signature)].append(time)
    if set(actual_groups) != set(expected_groups):
        problems.append("EVLOG event code/slot/argument signatures differ from simulation")
    if items:
        ts, *signature = items[0]
        first = expected_groups.get(tuple(signature), [])
        if not first: problems.append("first EVLOG item has no simulation event")
        else:
            offset = min(first) - ts * period_ps
            for signature in set(actual_groups) | set(expected_groups):
                actual = sorted(ts * period_ps + offset for ts in actual_groups[signature])
                expected_times = sorted(expected_groups[signature])
                if actual != expected_times:
                    problems.append(f"EVLOG timestamps/count differ for {signature}: "
                                    f"{actual} != {expected_times}")
    return problems


def check_evlog_pair(disabled_log, enabled_log):
    cutoffs = [re.findall(r"\[EVLOG_BEGIN\] (\d+)", text)
               for text in (disabled_log, enabled_log)]
    if any(len(x) != 1 for x in cutoffs): return ["expected one pre-print EVLOG_BEGIN boundary per run"]
    if cutoffs[0] != cutoffs[1]: return ["EVLOG pre-print boundary times differ"]
    cutoff = int(cutoffs[0][0])
    def before(text):
        rows = []
        for line in text.splitlines():
            match = re.search(r"(\[BINGO_[A-Z0-9_]+\] (\d+).*)", line)
            if match and int(match.group(2)) <= cutoff: rows.append(match.group(1))
        return rows
    return [] if before(disabled_log) == before(enabled_log) else [
        "control events (including PM) differ before EVLOG printing"]
