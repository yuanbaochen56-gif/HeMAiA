"""R10: the r8 gate profile, the two user decisions of 2026-10-11 21:22 CEST.

1. s1b_stage0_primary_off and s1b_invalid_status only: the Questa log carries
   exactly the testbench's own report of the predeclared completion-word-5
   ending, check_finish_rtl.sv line 37 ($error, status 5) then line 50
   ($fatal), and nothing else. Every other scene keeps zero Questa errors.
2. s1b_stage0_primary_off only: success envelope 9578 us, the 8700 us healthy
   limit plus the injected task's frozen extra allowance (942 - 64 us). Every
   other envelope is unchanged.

The profile is frozen write-once with SHA256 before any simulation. It binds the
r7 predictions, the R9 injected-task budgets and the check_finish source. The
unchanged r1 judge still runs; only these two checks are replaced, and only
after the r1 result is shown to agree with the frozen r7 rule.
"""
import contextlib
import datetime
import json
import os
from pathlib import Path
import re

import bingo_sdc_recover_check
from bingo_sdc_recover_reference import ReferenceError
import s1b_system_r1
from s1b_system_r1 import sha
from s1b_envelope_r7 import success_envelope_us
import s1b_injected_budget_r9 as budget
import s1b_reference_r8 as r8

W = Path(__file__).resolve().parents[1]
DECISION = "2026-10-11 21:22 CEST"
LABEL = "r8_gate_profile"
CHECK_FINISH = "target/sim/testharness/util/check_finish_rtl.sv"
CHECK_FINISH_SHA = "abb6263172cbfa9f16572f52e14f77c55ab88e79ed5160ae1811049c9793935c"
TERMINAL_SCENES = ("s1b_stage0_primary_off", "s1b_invalid_status")
ENVELOPE_SCENE = "s1b_stage0_primary_off"
ENVELOPE_US = 9578
ENVELOPE_LABEL = "Frozen success envelope exceeded"
# (source line, Questa severity, source statement, rendered message) in log order.
TERMINAL_LINES = (
    (37, "Error", '$error("Simulation of chip_0_0 finished with status %0d at %0t",',
     r"Simulation of chip_0_0 finished with status 5 at (\d+)"),
    (50, "Fatal", '$fatal(1, "All chips finished with errors at %0t", $time);',
     r"All chips finished with errors at (\d+)"))
QUESTA = re.compile(r"\*\*\s*(?:Error|Fatal)\b", re.IGNORECASE)
LOCATION = re.compile(r"#\s+Time: (\d+) ns\s+Scope: testharness\.check_finish File: (\S+) Line: (\d+)")


class ProfileError(ValueError):
    pass


def check_finish_source(sealed):
    """The sealed check_finish copy and the two statements the allowance names."""
    paths = [path for path in sealed["bindings"] if path.endswith("/" + CHECK_FINISH)]
    if len(paths) != 1 or sealed["bindings"][paths[0]] != CHECK_FINISH_SHA or sha(paths[0]) != CHECK_FINISH_SHA:
        raise ProfileError("Sealed check_finish_rtl.sv differs")
    lines = Path(paths[0]).read_text().splitlines()
    for number, _, statement, _ in TERMINAL_LINES:
        if lines[number - 1].strip() != statement:
            raise ProfileError(f"check_finish_rtl.sv line {number} differs")
    return dict(path=paths[0], sha256=CHECK_FINISH_SHA,
                lines={str(number): lines[number - 1].strip() for number, *_ in TERMINAL_LINES})


def derive(sealed, predictions, budget_binding, record):
    """The r8 profile; refuses anything but the two approved changes."""
    word5 = sorted(name for name, p in sealed["predictions"].items()
                   if p["model"]["completion_word"] == 5)
    if word5 != sorted(TERMINAL_SCENES) or any(
            sealed["predictions"][name]["model"]["sim_terminal"] != "FATAL" for name in TERMINAL_SCENES):
        raise ProfileError("Predeclared word-5 / SIM_FATAL scenes differ from the decision")
    expected = sealed["negative_probe"]["expected"]
    if expected["completion_word"] != 5 or expected["simulator_fatal"] is not True:
        raise ProfileError("Negative probe no longer predeclares the simulator fatal")
    scene = sealed["scenes"][ENVELOPE_SCENE]
    if scene["sdc_enable"] != 0 or scene.get("recovery_negative") or budget.injected_site(scene) is None:
        raise ProfileError("Envelope scene is not the injected detection-off scene")
    declared = record["scenes"][ENVELOPE_SCENE]
    extra_us = declared["budget_us"] - record["frozen_allowances_us"][budget.BASE_KEY]
    healthy_us = success_envelope_us(sealed["budgets"], 0)
    if (healthy_us, extra_us, healthy_us + extra_us) != (8700, 878, ENVELOPE_US):
        raise ProfileError("Envelope does not follow 8700 + the frozen injected extra allowance")
    unchanged = {str(n): success_envelope_us(sealed["budgets"], n) for n in (0, 1, 2)}
    if unchanged != {"0": 8700, "1": 9616, "2": 10640}:
        raise ProfileError("Frozen mismatch envelopes differ from r7")
    return dict(
        schema=1, label=LABEL, user_decision=DECISION,
        decisions=dict(
            terminal_lines=("In s1b_stage0_primary_off and s1b_invalid_status ONLY, allow exactly the two "
                            "testbench end-of-run error lines from check_finish_rtl.sv line 37 ('** Error: "
                            "Simulation of chip_0_0 finished with status 5') and line 50 ('** Fatal: All chips "
                            "finished with errors'). Any other error still fails. All other scenes keep zero "
                            "Questa errors."),
            envelope=("The s1b_stage0_primary_off envelope is 9,578 us (8,700 + the injected task's 878 us extra "
                      "budget). All other envelopes are unchanged.")),
        terminal_scenes=list(TERMINAL_SCENES),
        terminal_rule=("The log holds exactly two '** Error'/'** Fatal' lines: the line-37 $error with status 5, "
                       "then the line-50 $fatal at the same time, each followed by its Questa location line "
                       "naming testharness.check_finish in a check_finish_rtl.sv with the sealed SHA256. The "
                       "summary is exactly one 'Errors: 2'. Zero cache warnings. Anything else fails."),
        terminal_lines=[dict(line=n, severity=s, statement=st, message=m) for n, s, st, m in TERMINAL_LINES],
        check_finish=check_finish_source(sealed),
        predeclared={name: dict(completion_word=sealed["predictions"][name]["model"]["completion_word"],
                                sim_terminal=sealed["predictions"][name]["model"]["sim_terminal"])
                     for name in TERMINAL_SCENES},
        scene_envelopes_us={ENVELOPE_SCENE: ENVELOPE_US},
        envelope_rule=dict(healthy_limit_us=healthy_us, injected_task=declared["task"],
                           injected_budget_us=declared["budget_us"],
                           copy_allowance_us=record["frozen_allowances_us"][budget.BASE_KEY],
                           extra_us=extra_us, limit_us=ENVELOPE_US,
                           formula="8700 + (942 - 64) = 9578"),
        unchanged_envelopes_us=unchanged,
        predictions=predictions, injected_task_budgets=budget_binding)


def freeze(directory, profile):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "gate_profile_r8.json"
    profile = dict(profile, frozen_at=datetime.datetime.now().astimezone().isoformat())
    data = json.dumps(profile, indent=2, sort_keys=True).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    hash_path = directory / "gate_profile_r8.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  gate_profile_r8.json\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def load(binding):
    path, hash_path = Path(binding["path"]), Path(binding["hash_path"])
    if sha(path) != binding["sha256"] or hash_path.read_text() != binding["sha256"] + "  gate_profile_r8.json\n":
        raise ProfileError("Frozen r8 gate profile SHA256 mismatch")
    if any(p.stat().st_mode & 0o222 for p in (path, hash_path)):
        raise ProfileError("Frozen r8 gate profile must stay read-only")
    profile = json.loads(path.read_text())
    if profile["schema"] != 1 or profile["label"] != LABEL:
        raise ProfileError("Unknown gate profile record")
    return profile


def terminal_gate(log, profile):
    """Exactly the two check_finish lines of the predeclared word-5 ending."""
    lines = log.splitlines()
    cache = [line for line in lines if "reading invalid data on port" in line.lower()
             or ("warning" in line.lower() and "cache" in line.lower())]
    if cache:
        raise ReferenceError("Product WT cache warning: STOP_USER; no observer waiver")
    found = [index for index, line in enumerate(lines) if QUESTA.search(line)]
    rows = [(row["line"], row["severity"], row["message"]) for row in profile["terminal_lines"]]
    if len(found) != len(rows):
        raise ReferenceError("Questa error other than the predeclared check_finish ending: STOP_USER")
    times, locations = [], []
    for index, (number, severity, message) in zip(found, rows):
        match = re.fullmatch(rf"# \*\* {severity}: {message}", lines[index])
        location = LOCATION.fullmatch(lines[index + 1]) if index + 1 < len(lines) else None
        if not match or not location or int(location[3]) != number:
            raise ReferenceError("Questa error other than the predeclared check_finish ending: STOP_USER")
        source = Path(location[2])
        if not str(source).endswith("/" + CHECK_FINISH) or not source.is_file() \
                or sha(source) != profile["check_finish"]["sha256"]:
            raise ReferenceError("check_finish source differs from the sealed copy: STOP_USER")
        times.append(int(match[1]))
        locations.append(dict(line=number, file=str(source), time_ns=int(location[1])))
    if len(set(times)) != 1 or any(row["time_ns"] * 1000 != times[0] for row in locations):
        raise ReferenceError("check_finish lines are not one end-of-run report: STOP_USER")
    if re.findall(r"\bErrors:\s*(\d+)", log, re.IGNORECASE) != ["2"]:
        raise ReferenceError("Questa summary is not exactly the two check_finish errors: STOP_USER")
    return dict(status="PASS", cache_warnings=0, questa_errors=2,
                questa_error_lines=[lines[index] for index in found], locations=locations,
                summary=re.findall(r"#\s*Errors:.*", log), rule="r8 profile decision 1")


def log_gate(name, log, profile):
    if name in profile["terminal_scenes"]:
        return terminal_gate(log, profile)
    return s1b_system_r1.require_clean_product_log(log)


@contextlib.contextmanager
def r1_judge(gate):
    """The unchanged r1 judge with per-configuration release and, if given, the r8 log gate."""
    saved = (s1b_system_r1.require_clean_product_log, bingo_sdc_recover_check.compare_release,
             s1b_system_r1.compare_release)
    bingo_sdc_recover_check.compare_release = s1b_system_r1.compare_release = r8.compare_release
    if gate is not None:
        s1b_system_r1.require_clean_product_log = lambda text: gate
    try:
        yield s1b_system_r1.judge
    finally:
        (s1b_system_r1.require_clean_product_log, bingo_sdc_recover_check.compare_release,
         s1b_system_r1.compare_release) = saved


def envelope(judged, name, scene, sealed, profile):
    """Replace only the declared scene's success envelope; every other scene keeps the r7 rule."""
    judged = dict(judged, problems=list(judged["problems"]))
    if scene.get("recovery_negative") or judged["completion_ps"] is None:
        if name in profile["scene_envelopes_us"]:
            raise ProfileError(f"{name}: envelope declared for a scene the judge does not bound")
        judged["success_envelope"] = None
        return judged
    entry = next(node["id"] for node in sealed["graph"]["nodes"]
                 if node["kernel"] == "__host_bingo_kernel_entry")
    mismatches = sum(row["third"] for row in judged["recovery"])
    frozen_us = success_envelope_us(sealed["budgets"], mismatches)
    span = judged["completion_ps"] - judged["dispatch"][entry]
    if (ENVELOPE_LABEL in judged["problems"]) != (span > frozen_us * 1_000_000):
        raise ProfileError(f"{name}: unchanged judge disagrees with the frozen envelope")
    limit_us = profile["scene_envelopes_us"].get(name, frozen_us)
    if name in profile["scene_envelopes_us"]:
        judged["problems"] = [p for p in judged["problems"] if p != ENVELOPE_LABEL]
        if span > limit_us * 1_000_000:
            judged["problems"].append(ENVELOPE_LABEL)
    judged["success_envelope"] = dict(
        limit_us=limit_us, r7_limit_us=frozen_us, mismatches=mismatches, entry_to_end_ps=span,
        passed=span <= limit_us * 1_000_000,
        source="r8 profile decision 2" if name in profile["scene_envelopes_us"] else "r7 success_envelope_us")
    judged["status"] = "FAIL" if judged["problems"] else "PASS"
    return judged


def judge(name, scene, log, uart, observer, sealed, reference, record, profile):
    """r1 judge, R9 injected-task budget, then the r8 terminal-line and envelope decisions."""
    gate = log_gate(name, log, profile)
    with r1_judge(gate if name in profile["terminal_scenes"] else None) as r1:
        judged = r1(name, scene, log, uart, observer, sealed, reference)
    judged = budget.apply(judged, name, scene, log, uart, sealed, record)
    return envelope(judged, name, scene, sealed, profile)
