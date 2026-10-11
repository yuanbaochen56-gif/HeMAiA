"""R12: the r10 gate profile, the r9 profile plus the user decision of 2026-10-11 22:33 CEST.

4. s1b_invalid_status only: the recovery checker's FAIL2 evidence
   (bingo_sdc_recover_check.py:137) takes the observed completion word 5 as the
   proof of return code 2, in place of the UART line "Offload Finish with ret = 2".
   The sealed image never prints that line: the app prints it through
   OFFLOAD_BINGO_HW_DEBUG_PRINT_SAFE, which is empty without OFFLOAD_BINGO_HW_DEBUG.
   start.S _exit stores (exit code << 1) | 1, so 5 = (2 << 1) | 1. The other half
   of the check, "finished with status 5" in the Questa log, stays.

Every other check, in this scene and in all others, is unchanged. Decisions 1-3
are the frozen r9 profile, re-derived and compared with it. The r11 judge runs
unchanged; only the FAIL2 problem of the declared scene is replaced, and only
after the r1 problem is shown to agree with the r1 line-137 condition.
"""
import datetime
import json
import os
from pathlib import Path
import re

from s1b_system_r1 import sha
import s1b_profile_r11 as r11

W = Path(__file__).resolve().parents[1]
DECISION = "2026-10-11 22:33 CEST"
LABEL = "r10_gate_profile"
R9_PROFILE_SHA = "9f1eff271db1644fd6d743e5dc3caffe26022650a1240bdfc7caed09fe802990"
FAIL2_SCENE = "s1b_invalid_status"
FAIL2_LABEL = "Missing finite FAIL2/completion5 evidence"
FAIL2_UART = "Offload Finish with ret = 2"
STATUS5 = r"finished with status 5\b"
COMPLETION = r"S1B_COMPLETION_WRITTEN time=(\d+).*word=(\d+)"
RETURN_CODE = 2
CHECKER = "target/sim/automation/test/bingo_sdc_recover_check.py"
CHECKER_SHA = "dd287b2cf9e14728c0170e477ee001a0e9c90229ff3092b282a881fd65349f50"
CHECKER_LINES = (
    (137, 'if "Offload Finish with ret = 2" not in uart or not re.search(r"finished with status 5\\b",log):'),
    (138, 'problems.append("Missing finite FAIL2/completion5 evidence")'))
START_S = "target/sw/host/runtime/start.S"
START_S_SHA = "c49f286d1a071136a0a9b26c4cba425067978c08d720593de8a5de097e8d7fb5"
START_S_LINES = ((21, "_exit:"), (23, "slli        a0, a0, 1"),
                 (24, "ori         a0, a0, 1   # (exit code << 1) | 1"))
ProfileError = r11.ProfileError
log_gate = r11.log_gate


def encode(code):
    """start.S _exit: the completion word of return code `code`."""
    return (code << 1) | 1


def pinned_source(sealed, relative, digest, lines, product=None):
    """The sealed tracked source (and its product build copy, if given) with the named statements."""
    if sealed["source_hashes"].get(relative) != digest:
        raise ProfileError(f"{relative} is not sealed with the pinned SHA256")
    paths = [W / relative] + ([Path(product) / relative] if product else [])
    if any(sha(path) != digest for path in paths):
        raise ProfileError(f"{relative} differs from its sealed SHA256")
    text = paths[0].read_text().splitlines()
    for number, statement in lines:
        if text[number - 1].strip() != statement:
            raise ProfileError(f"{relative} line {number} differs")
    return dict(path=relative, sha256=digest, copies=[str(path) for path in paths],
                lines={str(number): statement for number, statement in lines})


def derive(sealed, r9_profile, frozen_r9):
    """The r10 profile; refuses anything but the r9 profile plus the one approved FAIL2 evidence."""
    if {k: v for k, v in frozen_r9.items() if k != "frozen_at"} != json.loads(json.dumps(r9_profile)):
        raise ProfileError("Re-derived r9 decisions differ from the frozen r9 profile")
    negative = sorted(name for name, scene in sealed["scenes"].items() if scene.get("recovery_negative"))
    if negative != [FAIL2_SCENE] or FAIL2_SCENE not in r9_profile["terminal_scenes"]:
        raise ProfileError("The negative probe is not the one declared scene")
    expected = sealed["negative_probe"]["expected"]
    model = sealed["predictions"][FAIL2_SCENE]["model"]
    if (expected["return_code"], expected["completion_word"], model["app_exit_code"], model["completion_word"],
            model["terminal"], model["sim_terminal"]) != (RETURN_CODE, 5, RETURN_CODE, 5, "FAIL", "FATAL") \
            or encode(RETURN_CODE) != 5:
        raise ProfileError("Predeclared FAIL2 return code / completion word differ from the decision")
    product = json.loads(Path(sealed["preparation_gate"]["path"]).read_text())["product_build_copy"]
    start = pinned_source(sealed, START_S, START_S_SHA, START_S_LINES, product)
    if sealed["bindings"].get(str(W / CHECKER)) != CHECKER_SHA:
        raise ProfileError("Recovery checker is not bound with the pinned SHA256")
    checker = pinned_source(sealed, CHECKER, CHECKER_SHA, CHECKER_LINES)
    decision = ("For s1b_invalid_status ONLY, accept completion word 5 as proof of return code 2 (start.S writes "
                "(exit_code << 1) | 1; 5 = (2 << 1) | 1), instead of the UART line 'Offload Finish with ret = "
                "2'. Every other check, in this scene and all others, is unchanged.")
    return dict(
        r9_profile, schema=1, label=LABEL, user_decision=DECISION,
        r9_user_decision=r9_profile["user_decision"],
        decisions=dict(r9_profile["decisions"], fail2_completion_word=decision),
        fail2_completion_word=dict(
            scene=FAIL2_SCENE, problem=FAIL2_LABEL, return_code=RETURN_CODE, completion_word=encode(RETURN_CODE),
            replaced_uart_line=FAIL2_UART, kept_log_pattern=STATUS5, completion_pattern=COMPLETION,
            rule=("Only in s1b_invalid_status: the problem is raised unless the Questa log holds exactly one "
                  "read-only observer line 'S1B_COMPLETION_WRITTEN time=T word=W' with W = 5, decoded by "
                  "start.S as (W >> 1) = 2 with (W & 1) = 1, and the log matches 'finished with status 5\\b'. "
                  "The UART line is no longer required there. Every other check, and every other scene, is "
                  "the unchanged r9 judge."),
            start_s=start, checker=checker,
            predeclared=dict(negative_probe_return_code=expected["return_code"],
                             negative_probe_completion_word=expected["completion_word"],
                             app_exit_code=model["app_exit_code"], completion_word=model["completion_word"])),
        r9_profile_binding=dict(sha256=R9_PROFILE_SHA))


def freeze(directory, profile):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "gate_profile_r10.json"
    profile = dict(profile, frozen_at=datetime.datetime.now().astimezone().isoformat())
    data = json.dumps(profile, indent=2, sort_keys=True).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    hash_path = directory / "gate_profile_r10.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  gate_profile_r10.json\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def load(binding):
    path, hash_path = Path(binding["path"]), Path(binding["hash_path"])
    if sha(path) != binding["sha256"] or hash_path.read_text() != binding["sha256"] + "  gate_profile_r10.json\n":
        raise ProfileError("Frozen r10 gate profile SHA256 mismatch")
    if any(p.stat().st_mode & 0o222 for p in (path, hash_path)):
        raise ProfileError("Frozen r10 gate profile must stay read-only")
    profile = json.loads(path.read_text())
    if profile["schema"] != 1 or profile["label"] != LABEL:
        raise ProfileError("Unknown gate profile record")
    return profile


def fail2(judged, name, log, uart, profile):
    """Replace only the declared scene's FAIL2 return evidence; every other check is untouched."""
    judged = dict(judged, problems=list(judged["problems"]))
    rule = profile["fail2_completion_word"]
    if name != rule["scene"]:
        judged["fail2_evidence"] = None
        return judged
    uart_line = rule["replaced_uart_line"] in uart
    status5 = re.search(rule["kept_log_pattern"], log) is not None
    if (rule["problem"] in judged["problems"]) != (not uart_line or not status5) \
            or judged["problems"].count(rule["problem"]) > 1:
        raise ProfileError(f"{name}: unchanged judge disagrees with its own FAIL2 check")
    words = [(int(stamp), int(word)) for stamp, word in re.findall(rule["completion_pattern"], log)]
    word = words[0][1] if len(words) == 1 else None
    decoded = word >> 1 if word is not None and word & 1 else None
    observed = word == rule["completion_word"] and decoded == rule["return_code"]
    passed = observed and status5
    if passed:
        judged["problems"] = [p for p in judged["problems"] if p != rule["problem"]]
    elif rule["problem"] not in judged["problems"]:
        judged["problems"].append(rule["problem"])
    judged["fail2_evidence"] = dict(
        rule="r10 profile decision 4", completion_word_lines=words, completion_word=word,
        completion_word_ps=words[0][0] if len(words) == 1 else None, decoded_return_code=decoded,
        expected_return_code=rule["return_code"], completion_word_observed=observed,
        status5_in_log=status5, uart_line_present=uart_line, r1_problem=not uart_line or not status5,
        passed=passed)
    judged["status"] = "FAIL" if judged["problems"] else "PASS"
    return judged


def judge(name, scene, log, uart, observer, sealed, reference, record, profile):
    """The r11 judge (r1, R9 budget, r8 decisions 1-2, r9 decision 3), then decision 4 on the probe."""
    judged = r11.judge(name, scene, log, uart, observer, sealed, reference, record, profile)
    return fail2(judged, name, log, uart, profile)
