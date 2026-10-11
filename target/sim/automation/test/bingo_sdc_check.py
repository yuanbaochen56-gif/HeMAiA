"""S1a text classifier; the frozen C3 classifier remains unchanged."""
import re

from bingo_c3 import classify

MISMATCH = re.compile(r"\[Host\] SDC mismatch: primary=(\d+) shadow=(\d+) compare=(\d+) code=(\d+)")
REPORT = re.compile(r"\[Host\] SDC report: (\d+) mismatches")
INJECT = re.compile(r"\[BINGO_SDC_INJECT\] gid=(\d+) cluster=(\d+) core=(\d+) offset=(\d+) mask=(\d+)")


def classify_sdc(log, uart, *, enabled, fault=None):
    result = classify(log, uart)
    mismatches = [dict(zip(("primary", "shadow", "compare", "code"), map(int, row)))
                  for row in MISMATCH.findall(uart)]
    reports = list(map(int, REPORT.findall(uart)))
    reasons = []
    if reports != [len(mismatches)] or any(r["code"] == 0 for r in mismatches):
        reasons.append("SDC_REPORT")
    if len({r["primary"] for r in mismatches}) != len(mismatches):
        reasons.append("SDC_DUPLICATE")
    if not enabled and mismatches:
        reasons.append("SDC_DISABLED_MISMATCH")
    injections = [tuple(map(int, row)) for row in INJECT.findall(uart)]
    if fault is None:
        if injections:
            reasons.append("SDC_UNEXPECTED_INJECTION")
    elif injections != [tuple(fault)]:
        reasons.append("SDC_NOT_ACTIVATED")
    if "[BINGO_SDC_WARN]" in uart:
        reasons.append("SDC_WARNING")
    category = result["class"]
    if reasons or category in ("hang", "unclassified") or not result["eoc"]:
        category = "unclassified"
    elif mismatches:
        if category == "wrong" and enabled:
            category = "detected-sdc"
        elif category == "recovered" and enabled:
            category = "detected-sdc-shadow"
        else:
            category = "unclassified"
            reasons.append("SDC_CONTROL_CONFLICT")
    result.update({"class": category, "mismatches": mismatches,
                   "sdc_reasons": reasons, "injections": injections})
    return result


def evaluate_sdc(scene, log, uart):
    fault = None
    if scene.get("fault_sdc_gid", 0xFFFFFFFF) != 0xFFFFFFFF:
        fault = (scene["fault_sdc_gid"], scene["fault_sdc_cluster"], scene["fault_sdc_core"],
                 scene["fault_sdc_offset"], scene["fault_sdc_mask"])
    result = classify_sdc(log, uart, enabled=bool(scene["sdc_enable"]), fault=fault)
    problems = list(result["sdc_reasons"])
    if result["class"] != scene["sdc_expect_class"]:
        problems.append(f"SDC category {result['class']} != {scene['sdc_expect_class']}")
    if result["mismatches"] != scene["sdc_expect_mismatches"]:
        problems.append("SDC mismatch records differ from the scene expectation")
    if result["checks_pass"] != scene["sdc_expect_checks_pass"]:
        problems.append("SDC host correctness result differs from the scene expectation")
    if any(result[key] for key in ("suspect", "fence", "replay", "cerf", "stuck", "timeout")):
        problems.append("Unexpected fail-stop/control event in an SDC-only scenario")
    return problems
