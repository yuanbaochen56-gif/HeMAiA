"""C3 coverage outcomes, using Claude's 2026-10-05 v3 contract."""
from pathlib import Path
import re

from bingo_c2 import TASK_EVENT
from hemaia_sim_runner import SIM_ERR_MARKER, SIM_OK_MARKER

CLASSES = ("recovered", "degraded", "detected-stuck", "hang", "wrong", "unclassified")
REASONS = ("U1", "U2", "U3", "U4", "U5")
CHECK = re.compile(r"Check \[([^\]]*)\]:\s*(\S+)")
ABNORMAL = re.compile(
    r"\[BINGO_ASSERT\]|\[ASSERT FAILED\]|Assertion[^\n]*failed")
EXIT_STATUS = re.compile(r"Simulation of chip_\d+_\d+ finished with status (\d+)")
KILL_ARTIFACT = re.compile(r"\s*(?:#\s*)?\*\* Fatal: Read failure in vlm process \(\d+,\d+\)\s*")
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
    for key, applies in (("U2", eoc == timeout),
                         ("U5", eoc and checks and stuck and not cerf)):
        if applies and key not in reasons:
            reasons.append(key)
    reasons.sort()
    if reasons:
        return ["unclassified"], reasons
    predicates = {
        "recovered": eoc and checks and not cerf,
        "degraded": eoc and checks and cerf,
        "detected-stuck": not (eoc and checks) and stuck,
        "hang": not eoc and not stuck,
        "wrong": eoc and not checks and not stuck,
    }
    return [name for name, applies in predicates.items() if applies], []


def classify(log, uart, *, fault_gid=None, victim=None, any_core=False):
    """Classify text evidence. None/empty text represents an absent/empty file."""
    log, uart = log or "", uart or ""
    eoc = SIM_OK_MARKER in log or SIM_ERR_MARKER in log
    statuses = EXIT_STATUS.findall(log)
    exit_status = max(map(int, statuses)) if statuses else (0 if SIM_OK_MARKER in log else None)
    checks = CHECK.findall(uart)
    wd = [tuple(map(int, match.groups())) for match in WD.finditer(log)]
    fence_times = [event[0] for event in wd if event[5]]
    # Keep raw events with source and line number for exceptional outcomes.
    events = []
    fatal_lines = []
    for source, text in (("sim_run.log", log), ("uart_chip_0_0.log", uart)):
        for number, line in enumerate(text.splitlines(), 1):
            if "** Fatal" in line:
                fatal_lines.append(dict(
                    source=source, line_number=number, raw=line,
                    chip_end="** Fatal: All chips finished with errors" in line,
                    kill_artifact=False))
            if ("[BINGO_" in line or "Bingo status" in line or CHECK.search(line)
                    or SIM_OK_MARKER in line or SIM_ERR_MARKER in line
                    or EXIT_STATUS.search(line)
                    or "TIMEOUT: simulation exceeded" in line or ABNORMAL.search(line)
                    or "** Fatal" in line):
                events.append(dict(source=source, line_number=number, raw=line))
    signals = dict(
        eoc=eoc, checks_pass=bool(checks) and all(status == "PASS" for _, status in checks),
        suspect=any(event[4] for event in wd), fence=bool(fence_times),
        replay="[BINGO_REPLAY]" in log, cerf="[BINGO_CERF_FB]" in log,
        stuck=("[BINGO_REPLAY_STUCK]" in log or
               bool(re.search(r"Bingo status:[^\n]*\breplay_stuck=1\b", uart))),
        timeout="TIMEOUT: simulation exceeded" in log,
    )
    non_chip_end = [line for line in fatal_lines if not line["chip_end"]]
    last_nonempty = max((number for number, line in enumerate(log.splitlines(), 1)
                         if line.strip()), default=0)
    kill_artifact = bool(
        signals["timeout"] and len(non_chip_end) == 1
        and non_chip_end[0]["source"] == "sim_run.log"
        and non_chip_end[0]["line_number"] == last_nonempty
        and KILL_ARTIFACT.fullmatch(non_chip_end[0]["raw"]))
    if kill_artifact:
        non_chip_end[0]["kill_artifact"] = True
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
        U2=bool(ABNORMAL.search(log + "\n" + uart)) or
           (bool(non_chip_end) and not kill_artifact) or eoc == signals["timeout"],
        U3=eoc and (not checks or "Bingo status:" not in uart or exit_status is None
                    or (exit_status != 0) != any(status == "FAIL" for _, status in checks)),
        U4=not triggered,
        U5=eoc and signals["checks_pass"] and signals["stuck"] and not signals["cerf"],
    )
    candidates, reasons = outcome_candidates(signals, prechecks)
    if len(candidates) != 1:
        raise ValueError(f"C3 partition violation: {signals}, {prechecks}, {candidates}")
    exits = re.findall(r"All chips finished (?:successfully|with errors) at (\d+)", log)
    annot = []
    if signals["replay"]:
        annot.append("after_replay")
    elif signals["fence"]:
        annot.append("after_fence")
    if signals["cerf"]:
        annot.append("after_cerf")
    return dict(signals, **{"class": candidates[0]}, unclassified_reason=";".join(reasons),
                exit_status=exit_status, annot=";".join(annot),
                kill_artifact=kill_artifact, fatal_lines=fatal_lines,
                prechecks=prechecks, fault_triggered=triggered,
                t_fence_ps=min(fence_times) if fence_times else None,
                t_exit_ps=int(exits[-1]) if exits else None, events=events)


def classify_files(log_path, uart_path, **kwargs):
    def read(path):
        path = Path(path)
        return path.read_text(errors="replace") if path.is_file() else None
    return classify(read(log_path), read(uart_path), **kwargs)
