"""P9 diagnostic parsing and strict healthy/exact-ps gates."""
from copy import deepcopy
import re

LATE = re.compile(
    r"\[BINGO_ADAPT_LATE\] (\d+) slot=(\d+) core=(\d+) cluster=(\d+) "
    r"type=(\d+) class=(\d+) interval=(\d+) threshold=(\d+) source=\s*(static|adaptive)")
TABLE = re.compile(
    r"\[TRACE\] P9_TABLE (\d+) type=(\d+) class=(\d+) max=(\d+) n=(\d+) load=(\d+)")
UNHEALTHY = re.compile(r"\[BINGO_(?:ADAPT_LATE|WD|FENCE|LATE|RISK[^]]*|PARK[^]]*)\]")


def late_events(log):
    """Reject malformed diagnostic rows rather than hiding an event."""
    events = []
    for line in log.splitlines():
        if "[BINGO_ADAPT_LATE]" not in line:
            continue
        match = LATE.search(line)
        if not match:
            raise ValueError(f"Malformed P9 late event: {line}")
        values = list(map(int, match.groups()[:-1]))
        event = dict(zip(("time_ps", "slot", "core", "cluster", "type", "class",
                          "interval", "threshold"), values))
        event["source"] = match[9]
        if event["threshold"] < 1 or event["interval"] < event["threshold"]:
            raise ValueError(f"Invalid P9 late interval: {line}")
        events.append(event)
    return events


def table_at(log, before_ps=None):
    """P9_EN/log/trace must be on; absent entries retain reset zero.

    At a fence use updates strictly before its edge, not updates at the same
    timestamp. Unlike sparse LEARN rows, P9_TABLE prints every actual change.
    """
    result = {}
    for line in log.splitlines():
        if "[TRACE] P9_TABLE" not in line:
            continue
        match = TABLE.search(line)
        if not match:
            raise ValueError(f"Malformed P9 table event: {line}")
        time, typ, cls, maximum, samples, load = map(int, match.groups())
        if before_ps is not None and time >= before_ps:
            continue
        key = f"{typ}:{cls}"
        result[key] = dict(M=maximum, N=samples, L=load)
    return deepcopy(result)


def healthy_problems(log):
    return [f"P9 healthy control event: {line}" for line in log.splitlines()
            if UNHEALTHY.search(line)]


def exact_ps_observations(log):
    """Compare all product events/EOC, not optional history diagnostics."""
    return [line for line in log.splitlines()
            if re.search(r"\[BINGO_|\[EVLOG|finished successfully", line)]


def healthy_pair_problems(off_log, off_uart, on_log, on_uart):
    problems = healthy_problems(off_log) + healthy_problems(on_log)
    if exact_ps_observations(off_log) != exact_ps_observations(on_log):
        problems.append("P9 off/on product events or EOC differ at exact ps")
    if off_uart != on_uart:
        problems.append("P9 off/on UART bytes differ")
    return problems
