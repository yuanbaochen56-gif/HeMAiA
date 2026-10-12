"""B2: judge a run with two offloads in one boot and the accelerator reset between.

The simulation and UART logs are cut at the reset. Each offload is then judged
as the single-offload run it repeats (the driver's own checker, the healthy
checks, the event log check, the host status line), and the boundary itself is
checked here: one complete reset sequence, no event while the quadrant is in
reset, no spurious CDC transaction, no simulator error, and the host's own
result lines.
"""
import re
from typing import Callable, Dict, List, Tuple

from bingo_c2 import healthy_problems
from bingo_evlog import check_evlog

RESET_RE = re.compile(r"\[ACC_RESET\] (\d+) (request|reset|release|done)")
EVENT_RE = re.compile(r"\[(?:BINGO_\w+|EVLOG_\w+)\] (\d+)")
DISPATCH_RE = re.compile(r"\[BINGO_DISPATCH\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
DROP_RE = re.compile(r"\[BINGO_FENCE\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) dropped done task=(\d+)")
OFFLOAD_RE = re.compile(r"\[B2\] offload (\d) ret=(-?\d+) wake=(\d+) ready=(\d+) end=(\d+) "
                        r"exit=0x([0-9a-f]+)")
HOST_RESET_RE = re.compile(r"\[B2\] accelerator reset start=(\d+) end=(\d+) restored=(\d+) image=(\d+)")
SIM_ERROR_RE = re.compile(r"^.*\*\* (?:Error|Fatal).*$", re.M)
SEQUENCE = ["request", "reset", "release", "done"]


def split_offloads(log_text: str, uart_text: str) -> Tuple[List[str], List[str], Dict[str, int]]:
    """([log 1, log 2], [uart 1, uart 2], reset marker times); ValueError if the
    run does not hold exactly one complete reset sequence and two offloads."""
    lines = log_text.splitlines()
    marks = [(index, int(m.group(1)), m.group(2)) for index, line in enumerate(lines)
             for m in [RESET_RE.search(line)] if m]
    if [kind for _, _, kind in marks] != SEQUENCE:
        raise ValueError(f"accelerator reset markers {[kind for _, _, kind in marks]}, "
                         f"expected one {SEQUENCE}")
    times = {kind: time for _, time, kind in marks}
    if sorted(times.values()) != [times[kind] for kind in SEQUENCE]:
        raise ValueError(f"accelerator reset marker times out of order: {times}")
    request, reset, done = marks[0][0], marks[1][0], marks[3][0]
    during = [line for line in lines[reset:done] if EVENT_RE.search(line)]
    if during:
        raise ValueError(f"{len(during)} event(s) while the quadrant is in reset: {during[:3]}")
    # The end of the simulation belongs to both offloads
    last_event = max(index for index, line in enumerate(lines) if EVENT_RE.search(line))
    tail = lines[last_event + 1:]
    first = lines[:request] + [line for line in lines[request:reset] if EVENT_RE.search(line)] + tail
    second = lines[done + 1:]
    uart = uart_text.splitlines()
    ends = [index for index, line in enumerate(uart) if "[B2] offload 1 " in line]
    if len(ends) != 1:
        raise ValueError(f"{len(ends)} '[B2] offload 1' lines in the UART log")
    return (["\n".join(first) + "\n", "\n".join(second) + "\n"],
            ["\n".join(uart[:ends[0] + 1]) + "\n", "\n".join(uart[ends[0] + 1:]) + "\n"], times)


def host_status_problems(spec: dict, log_text: str, uart_text: str) -> List[str]:
    """The host's status line against the last [BINGO_STATUS] of the offload
    (the driver's check for a single-offload run)."""
    host = re.search(r"\[Host\] Bingo status: replay_stuck=(\d+) remote_done_mismatch=(\d+) "
                     r"link_error=(\d+) fenced=0x([0-9a-fA-F]+)", uart_text)
    rtl = re.findall(r"\[BINGO_STATUS\] \d+ chip=\d+ .*fenced=0x([0-9a-fA-F]+)", log_text)
    rtl_fenced = int(rtl[-1], 16) if rtl else 0
    if not host:
        return ["no '[Host] Bingo status' line in the UART log"]
    if (int(host.group(4), 16) != rtl_fenced or
            host.group(1) != str(int(spec.get("expect_host_stuck", False))) or host.group(3) != "0"):
        return [f"host status {host.group(0)} (RTL fenced 0x{rtl_fenced:x})"]
    return []


def offload_spec(sc: dict, index: int, scenarios: Dict[str, dict]) -> dict:
    """The single-offload scene that offload `index` repeats: its template with
    the changes the B2 scene lists, on this run's graph and event log setting."""
    entry = sc["b2_offloads"][index]
    spec = dict(scenarios[entry["template"]])
    spec.update(entry.get("changes", {}))
    spec["dispatch_graph_csv"] = sc.get("dispatch_graph_csv")
    spec["evlog_enable"] = sc.get("evlog_enable", 0)
    return spec


def evaluate_b2(name: str, sc: dict, log_text: str, uart_text: str, scenarios: Dict[str, dict],
                evaluate: Callable[[str, dict, str, str], List[str]], slots: int) -> List[str]:
    """Failed expectations of a two-offload run (empty = pass)."""
    problems: List[str] = []
    errors = SIM_ERROR_RE.findall(log_text)
    if errors:
        problems.append(f"{len(errors)} simulator error line(s): {errors[:3]}")
    cdc = [line for line in log_text.splitlines() if "[B2_CDC]" in line and "without a push" in line]
    if cdc:
        problems.append(f"{len(cdc)} spurious CDC transaction(s): {cdc[:3]}")
    try:
        logs, uarts, times = split_offloads(log_text, uart_text)
    except ValueError as error:
        return problems + [str(error)]

    host = {int(m.group(1)): dict(ret=int(m.group(2)), exit=int(m.group(6), 16))
            for m in OFFLOAD_RE.finditer(uart_text)}
    if len(OFFLOAD_RE.findall(uart_text)) != 2 or sorted(host) != [1, 2]:
        problems.append("expected one '[B2] offload 1' and one '[B2] offload 2' result line")
    if len(HOST_RESET_RE.findall(uart_text)) != 1:
        problems.append("expected one '[B2] accelerator reset' line")

    for index in range(2):
        tag = f"offload {index + 1}"
        spec = offload_spec(sc, index, scenarios)
        log, uart = logs[index], uarts[index]
        found = evaluate(f"{name}/{index + 1}", spec, log, uart)
        if spec.get("c2") or spec.get("c4_control"):
            found += healthy_problems(log, uart, spec["dispatch_graph_csv"])
        if spec.get("evlog_enable"):
            found += check_evlog(log, uart, slots, allow_empty=spec.get("evlog_allow_empty", False))
        elif "[EVLOG]" in uart:
            found.append("disabled EVLOG printed items")
        found += host_status_problems(spec, log, uart)
        entry = sc["b2_offloads"][index]
        drops = [tuple(map(int, m.groups()[1:])) for m in DROP_RE.finditer(log)]
        expected_drops = [tuple(d) for d in entry.get("dropped_done", [])]
        if drops != expected_drops:
            found.append(f"dropped done {drops}, expected {expected_drops}")
        result = host.get(index + 1)
        if result and (result["ret"] != 0 or result["exit"] != entry["exit"]):
            found.append(f"host result ret={result['ret']} exit=0x{result['exit']:x}, "
                         f"expected ret=0 exit=0x{entry['exit']:x}")
        problems += [f"{tag}: {problem}" for problem in found]

    # A slot fenced in offload 1 works again in a healthy offload 2
    first, second = offload_spec(sc, 0, scenarios), offload_spec(sc, 1, scenarios)
    if first.get("expect_fence") and not second.get("expect_fence"):
        chip, core, cluster = first["victim"]
        again = [m for m in DISPATCH_RE.finditer(logs[1])
                 if tuple(map(int, m.groups()[1:2] + m.groups()[3:])) == (chip, core, cluster)]
        if not again:
            problems.append(f"slot {tuple(first['victim'])} fenced in offload 1 got no task in offload 2")
    print(f"[{name}] accelerator reset (ps): {times}")
    return problems
