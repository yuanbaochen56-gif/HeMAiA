"""Check recovery records against a sealed graph and independent bytes.

The caller must provide simulator-observed eligibility, never inferred skip
DONE. Complete image/clock/ABI binding and prediction checks belong to the
experiment envelope, in addition to these checks.
"""
import json
from pathlib import Path
import re

from bingo_c3 import classify
from bingo_sdc_check import INJECT, MISMATCH, REPORT

RECORD = re.compile(r"\[Host\] SDC_RECOVER ([^\r\n]+)")
BUFFER = re.compile(r"\[Host\] SDC_BUFFER name=(\w+) data=([0-9a-f]{128})(?:\r?\n|$)")
EVENT = re.compile(r"\[BINGO_(DISPATCH|DONE)\]\s+(\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
FIELDS = ("tx enabled code detect_valid third selected commit reports outcome failure phase "
          "status_addr rstart status_cc third_start third_end vote_end publish_end").split()


def recovery_records(uart):
    records = []
    for match in RECORD.finditer(uart):
        fields = match[1].split()
        row = {}
        for field in fields:
            key, sep, value = field.partition("=")
            if not sep or key in row or key not in FIELDS:
                raise ValueError("Invalid recovery record field")
            row[key] = int(value, 16 if key == "status_addr" else 10)
        if set(row) != set(FIELDS):
            raise ValueError("Incomplete recovery record")
        records.append(row)
    return records


def evaluate_recovery(scene, log, uart):
    problems = []
    try:
        rows = recovery_records(uart)
        graph = scene["recovery_graph"]
        stages = graph["stages"]
        if len(stages) != 2 or graph["status_contract"]["indices"] != [0, 1]:
            raise ValueError("Invalid sealed status contract")
        addresses = graph["status_addresses"]
        if len(addresses) != 2 or addresses[1] != addresses[0] + 4 or addresses[0] % 4:
            raise ValueError("Invalid sealed status addresses")
        negative = scene.get("recovery_negative", False)
        if [r["tx"] for r in rows] != ([0] if negative else [0, 1]):
            problems.append("Missing, duplicated or unordered recovery transactions")
        events = {}
        for kind, stamp, chip, task, core, cluster in EVENT.findall(log):
            if int(chip) != 0:
                problems.append("Unexpected chip in recovery events")
            key = kind, int(task)
            role = graph['roles'].get(task)
            expected_slot = ((1,1) if role and role.startswith(('S','compare')) else
                             (0,1) if role and role.startswith('P') else (0,2))
            if role and (int(cluster),int(core)) != expected_slot:
                problems.append("Recovery task ran on an unbound slot")
            if key in events:
                problems.append("Duplicate recovery dispatch/DONE")
            events[key] = int(stamp)
        fault_tx = scene.get("recovery_fault_tx")
        shadow_fault = scene.get("recovery_fault_shadow", False)
        enabled = bool(scene["sdc_enable"])
        for r in rows:
            if r["tx"] not in (0, 1):
                problems.append("Unbound recovery transaction")
                continue
            tx = r["tx"]; stage = stages[tx]
            mismatch = enabled and tx == fault_tx
            wanted = dict(enabled=int(enabled), code=(18 if mismatch else 0) if enabled else 0xFFFFFFFF,
                          detect_valid=int(enabled), third=int(mismatch),
                          selected=2 if mismatch and not shadow_fault else 1,
                          commit=1, reports=1, outcome=3 if mismatch else 2 if enabled else 1,
                          failure=0, phase=2, status_addr=addresses[tx])
            if negative:
                wanted.update(code=0xFFFFFFFF, third=0, selected=0, commit=0,
                              outcome=4, failure=2, phase=3)
            for key, value in wanted.items():
                if r[key] != value:
                    problems.append(f"tx{tx} {key} {r[key]} != {value}")
            dispatch = events.get(("DISPATCH", stage["resolver"]))
            if dispatch is None:
                problems.append("Resolver report without DISPATCH")
            if enabled:
                done = events.get(("DONE", stage["compare"]))
                if done is None or dispatch is None or done >= dispatch:
                    problems.append("Resolver started before compare completion")
                compare_dispatch = events.get(("DISPATCH",stage["compare"]))
                for producer in (stage['primary'],stage['shadow']):
                    producer_done = events.get(('DONE',producer))
                    if producer_done is None or compare_dispatch is None or producer_done >= compare_dispatch:
                        problems.append("Compare did not wait for both private outputs")
            if negative:
                if ("DONE", stage["resolver"]) in events:
                    problems.append("Failed resolver wrote DONE")
            else:
                resolver_done = events.get(("DONE", stage["resolver"]))
                if resolver_done is None:
                    problems.append("Published resolver has no DONE")
                readers = [stages[1]["primary"], stages[1]["shadow"]] if tx == 0 else [
                    int(next(k for k,v in graph["roles"].items() if v == "consumer"))]
                for reader in readers:
                    at = events.get(("DISPATCH", reader))
                    if reader == stages[1]["shadow"] and not enabled:
                        continue
                    if at is None or resolver_done is None or at <= resolver_done:
                        problems.append("Canonical reader did not wait for publication DONE")
                times = [r[k] for k in ("rstart", "status_cc", "vote_end", "publish_end")]
                if any(b < a for a,b in zip(times,times[1:])) or not times[0]:
                    problems.append("Invalid recovery clock ordering")
                if mismatch and not (r["status_cc"] <= r["third_start"] < r["third_end"] <= r["vote_end"]):
                    problems.append("Invalid third/vote timing")
                if not mismatch and (r["third_start"] or r["third_end"]):
                    problems.append("Unexpected third execution timestamps")
        if not enabled:
            for stage in stages:
                for task in (stage["shadow"], stage["compare"]):
                    if any((kind,task) in events for kind in ("DISPATCH","DONE")):
                        problems.append("Disabled shadow/compare executed or wrote DONE")
        if negative:
            prohibited = {stages[1][k] for k in ("primary","shadow","compare","resolver")}
            prohibited.add(int(next(k for k,v in graph["roles"].items() if v == "consumer")))
            if any(("DISPATCH",task) in events for task in prohibited):
                problems.append("Consumer released after failure")
            if REPORT.search(uart) or "SDC_RECOVER_sink" in uart:
                problems.append("Failure did not bypass post_execute")
            if "Offload Finish with ret = 2" not in uart or not re.search(r"finished with status 5\b",log):
                problems.append("Missing finite FAIL2/completion5 evidence")
            if 'finished with errors' not in log or 'All chips finished successfully' in log:
                problems.append("Invalid failure simulation completion")
        else:
            raw = [tuple(map(int,x)) for x in MISMATCH.findall(uart)]
            wanted_raw = []
            if enabled and fault_tx is not None:
                st = stages[fault_tx]
                wanted_raw = [(st["primary"],st["shadow"],st["compare"],18)]
            if raw != wanted_raw or list(map(int,REPORT.findall(uart))) != [len(wanted_raw)]:
                problems.append("Original detection report differs or was erased")
            pairs = BUFFER.findall(uart)
            buffers = {k:bytes.fromhex(v) for k,v in pairs}
            names = {"input","P0","S0","T0","C0","P1","S1","T1","C1","sink"}
            if len(pairs)!=10 or set(buffers)!=names:
                problems.append("Missing or duplicated full buffer observations")
            else:
                expected = bytes(((i*37+11)^(i>>2))&255 for i in range(64))
                if buffers["input"] != expected:
                    problems.append("Stable input changed")
                src = expected
                for tx in (0,1):
                    p,s = bytearray(src),bytearray(src)
                    if tx == fault_tx:
                        if shadow_fault and enabled: s[17] ^= 4
                        elif not shadow_fault: p[17] ^= 4
                    canonical = src if enabled else bytes(p)
                    if buffers[f"P{tx}"] != bytes(p) or buffers[f"C{tx}"] != canonical:
                        problems.append("Primary/canonical bytes differ from independent oracle")
                    if enabled and buffers[f"S{tx}"] != bytes(s):
                        problems.append("Shadow bytes differ from independent oracle")
                    if enabled and tx == fault_tx and buffers[f"T{tx}"] != src:
                        problems.append("Third execution did not use stable input")
                    src = canonical
                if buffers["sink"] != src:
                    problems.append("Final consumer read different canonical bytes")
            base = classify(log,uart)
            if base["checks_pass"] != scene["recovery_correct"] or not base["eoc"]:
                problems.append("Final golden/completion differs from frozen scene")
        base = classify(log,uart)
        if any(base[key] for key in ("suspect","fence","replay","cerf","stuck","timeout")):
            problems.append("Unexpected fail-stop/control event")
        fault = [] if fault_tx is None else [(scene["fault_sdc_gid"],scene["fault_sdc_cluster"],
            scene["fault_sdc_core"],17,4)]
        if [tuple(map(int,x)) for x in INJECT.findall(uart)] != fault or "[BINGO_SDC_WARN]" in uart:
            problems.append("Injection activation differs from frozen scene")
        if "[BINGO_SDC]" in uart:
            problems.append("QUIET compare printed a device report")
        eligibility = scene.get("recovery_eligibility")
        if eligibility is None and scene.get("recovery_eligibility_path"):
            eligibility = json.loads(Path(scene["recovery_eligibility_path"]).read_text())
        if eligibility is None:
            problems.append("Missing read-only readiness observations for G-release")
        else:
            executing = {str(task):stamp for (kind,task),stamp in events.items() if kind=="DISPATCH"}
            if set(eligibility) != set(executing):
                problems.append("Readiness observations do not cover the exact executing set")
            for task, stamp in executing.items():
                ready = eligibility.get(task)
                if type(ready) is not int or not 0 <= stamp-ready <= 1000000:
                    problems.append("G-release exceeds 1us or invalid readiness")
    except (KeyError, ValueError, TypeError, OSError, StopIteration) as error:
        problems.append(f"Incomplete recovery evidence: {error}")
    return problems
