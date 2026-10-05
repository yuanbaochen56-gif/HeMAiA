"""C3 coverage outcomes, including Claude's 2026-10-05 U1-U5 contract."""
from pathlib import Path
import re

from bingo_c2 import TASK_EVENT
from hemaia_sim_runner import SIM_ERR_MARKER, SIM_OK_MARKER

CLASSES = ("recovered", "degraded", "detected-stuck", "hang", "wrong", "unclassified")
REASONS = ("U1", "U2", "U3", "U4", "U5")
CHECK = re.compile(r"Check \[([^\]]*)\]:\s*(\S+)")
ABNORMAL = re.compile(
    r"finished with errors|\[BINGO_ASSERT\]|\[ASSERT FAILED\]|Assertion[^\n]*failed")
WD = re.compile(
    r"\[BINGO_WD\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) "
    r"dead_suspect=(\d+) fenced=(\d+)")


def outcome_candidates(signals, prechecks):
    """Return the mutually exclusive predicates, also for inconsistent raw inputs.

    U2 and U5 are derived again here: supplied precheck booleans cannot make an
    impossible signal combination pass the preconditions in the truth table.
    """
    eoc, checks, cerf, stuck, fence, timeout = (
        bool(signals[key]) for key in ("eoc", "checks_pass", "cerf", "stuck", "fence", "timeout"))
    reasons = [key for key in REASONS if prechecks.get(key, False)]
    for key, applies in (("U2", not eoc and not timeout),
                         ("U5", eoc and checks and stuck and not cerf)):
        if applies and key not in reasons:
            reasons.append(key)
    reasons.sort()
    if reasons:
        return ["unclassified"], reasons
    predicates = {
        "recovered": eoc and checks and not cerf,
        "degraded": eoc and checks and cerf,
        "detected-stuck": (not eoc or not checks) and (stuck or fence),
        "hang": not eoc and timeout and not stuck and not fence,
        "wrong": eoc and not checks and not stuck and not fence,
    }
    return [name for name, applies in predicates.items() if applies], []


def classify(log, uart, *, fault_gid=None, victim=None, any_core=False):
    """Classify text evidence. None/empty text represents an absent/empty file."""
    log, uart = log or "", uart or ""
    eoc = SIM_OK_MARKER in log and SIM_ERR_MARKER not in log
    checks = CHECK.findall(uart)
    wd = [tuple(map(int, match.groups())) for match in WD.finditer(log)]
    fence_times = [event[0] for event in wd if event[5]]
    # Keep raw events with source and line number for exceptional outcomes.
    events = []
    for source, text in (("sim_run.log", log), ("uart_chip_0_0.log", uart)):
        for number, line in enumerate(text.splitlines(), 1):
            if ("[BINGO_" in line or "Bingo status" in line or CHECK.search(line)
                    or SIM_OK_MARKER in line or SIM_ERR_MARKER in line
                    or "TIMEOUT: simulation exceeded" in line or ABNORMAL.search(line)):
                events.append(dict(source=source, line_number=number, raw=line))
    signals = dict(
        eoc=eoc, checks_pass=bool(checks) and all(status == "PASS" for _, status in checks),
        suspect=any(event[4] for event in wd), fence=bool(fence_times),
        replay="[BINGO_REPLAY]" in log, cerf="[BINGO_CERF_FB]" in log,
        stuck=("[BINGO_REPLAY_STUCK]" in log or
               bool(re.search(r"Bingo status:[^\n]*\breplay_stuck=1\b", uart))),
        timeout="TIMEOUT: simulation exceeded" in log,
    )
    triggered = fault_gid is None
    if fault_gid is not None:
        if victim is None:
            raise ValueError("fault classification requires the victim slot")
        for match in TASK_EVENT.finditer(log):
            kind, _, chip, task, core, cluster = match.groups()
            if (kind == "DISPATCH" and int(task) == fault_gid and
                    (any_core or (int(chip), int(core), int(cluster)) == tuple(victim))):
                triggered = True
    prechecks = dict(
        U1=not log.strip() or not uart.strip(),
        U2=bool(ABNORMAL.search(log + "\n" + uart)) or not eoc and not signals["timeout"],
        U3=eoc and (not checks or "Bingo status:" not in uart),
        U4=not triggered,
        U5=eoc and signals["checks_pass"] and signals["stuck"] and not signals["cerf"],
    )
    candidates, reasons = outcome_candidates(signals, prechecks)
    if len(candidates) != 1:
        raise ValueError(f"C3 partition violation: {signals}, {prechecks}, {candidates}")
    exits = re.findall(r"All chips finished successfully at (\d+)", log)
    return dict(signals, **{"class": candidates[0]}, unclassified_reason=";".join(reasons),
                prechecks=prechecks, fault_triggered=triggered,
                t_fence_ps=min(fence_times) if fence_times else None,
                t_exit_ps=int(exits[-1]) if exits else None, events=events)


def classify_files(log_path, uart_path, **kwargs):
    def read(path):
        path = Path(path)
        return path.read_text(errors="replace") if path.is_file() else None
    return classify(read(log_path), read(uart_path), **kwargs)
