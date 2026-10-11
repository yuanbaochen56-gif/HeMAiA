"""Graph-bound S1 checker for QUIET and device/host report agreement.

The original host-only classifier remains compatible with its frozen callers.
System S1 runs add this cross-check using their sealed pair/slot metadata.
"""
import re
import shlex

from bingo_sdc_check import classify_sdc

DEVICE = re.compile(r"\[BINGO_SDC\] cluster=(\d+) core=(\d+) status_lo=0x([0-9a-fA-F]+) result=(\d+)")


def report_problems(scene, log, uart, pairs, graph):
    quiet = "-DBINGO_SDC_QUIET" in shlex.split(scene.get("image_flags", ""))
    host = classify_sdc(log, uart, enabled=bool(scene["sdc_enable"]))
    device = DEVICE.findall(uart)
    if quiet:
        return ["QUIET unexpectedly emitted device compare reports"] if device else []
    selected = [p for p in pairs if p["selected"]] if scene["sdc_enable"] else []
    if len(device) != len(selected):
        return ["Device compare report count differs from sealed pairs"]
    if not selected:
        return []
    # status slots are a contiguous uint32 array. Infer only its base address;
    # status_index and physical slot come from the sealed compiler/graph data.
    base = min(int(r[2], 16) for r in device)
    indices = {p["status_index"]: p for p in selected}
    expected = {r["compare"]: r["code"] for r in host["mismatches"]}
    found, problems = set(), []
    for cluster, core, address, code in device:
        offset = int(address, 16) - base
        pair = indices.get(offset // 4) if offset % 4 == 0 else None
        if pair is None or pair["compare_id"] in found:
            problems.append("Unbound or duplicate device compare status")
            continue
        task = pair["compare_id"]
        found.add(task)
        node = graph["nodes"][str(task)]
        if (int(cluster), int(core)) != (node["cluster"], node["core"]):
            problems.append("Device compare physical slot differs from sealed graph")
        if int(code) != expected.get(task, 0):
            problems.append(f"Device and host SDC reports disagree for compare={task}")
    if len(found) != len(selected):
        problems.append("Device compare status set differs from sealed pairs")
    return problems
