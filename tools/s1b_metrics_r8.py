"""S1b-4 metrics from the R8 physical judgements. Reads evidence only; no simulation."""
import json
from pathlib import Path
import re

from s1b_system_r1 import sha

GOLDEN = re.compile(r"\[Host\] Check \[SDC_RECOVER_sink\]: (PASS|FAIL)")


def scene_kind(scene):
    if scene.get("recovery_negative"):
        return "negative_probe"
    if scene["recovery_fault_tx"] is None:
        return "healthy"
    return "injected_detection_on" if scene["sdc_enable"] else "injected_detection_off"


def us(ps):
    return None if ps is None else ps / 1_000_000


def stage_timing(run, stage, period_ps, row):
    dispatch, done = run["dispatch"], run["done"]
    get = lambda table, task: table.get(str(task), table.get(task))
    p_done, c_done = get(done, stage["primary"]), get(done, stage["compare"])
    r_dispatch, r_done = get(dispatch, stage["resolver"]), get(done, stage["resolver"])
    return dict(
        compare_done_minus_primary_done_us=us(c_done - p_done) if c_done and p_done else None,
        resolver_dispatch_minus_compare_done_us=us(r_dispatch - c_done) if r_dispatch and c_done else None,
        resolver_done_minus_compare_done_us=us(r_done - c_done) if r_done and c_done else None,
        resolver_duration_us=us(r_done - r_dispatch) if r_done and r_dispatch else None,
        third_execution_us=us((row["third_end"] - row["third_start"]) * period_ps) if row and row["third"] else 0.0,
        vote_after_status_us=us((row["vote_end"] - row["status_cc"]) * period_ps) if row else None,
        publish_us=us((row["publish_end"] - row["vote_end"]) * period_ps) if row else None)


def metrics(sealed, system_dir, descriptor_result):
    progress = json.loads((Path(system_dir) / "progress.json").read_text())
    stages = sealed["graph"]["stages"]
    period = sealed["clocks"]["host_mcycle_period_ps"]
    runs, counts = {}, dict(injected_on=0, recovered=0, unrecoverable=0, healthy=0,
                            healthy_false_positive=0, wrong_or_hang=0)
    for run in progress["runs"]:
        name = run["name"]
        scene = sealed["scenes"][name]
        kind = scene_kind(scene)
        bins, = Path(run["directory"]).glob("task_*/bin")
        uart = (bins / "uart_chip_0_0.log").read_text()
        golden = GOLDEN.findall(uart)
        rows = {row["tx"]: row for row in run["recovery"]}
        mismatches = sum(row["third"] for row in rows.values())
        detected = [row["tx"] for row in rows.values() if row["detect_valid"] and row["code"] not in (0, 0xFFFFFFFF)]
        release = run["release"]["tasks"]
        entry = run["dispatch"]["10"] if "10" in run["dispatch"] else run["dispatch"][10]
        item = dict(kind=kind, configuration=run["configuration"], status=run["status"],
                    golden=golden[0] if len(golden) == 1 else None,
                    completion_word=run["finite_trace"]["completion_word"],
                    third_executions=mismatches, detected_tx=detected,
                    outcomes={tx: row["outcome"] for tx, row in rows.items()},
                    commits={tx: row["commit"] for tx, row in rows.items()},
                    entry_to_end_us=us(run["completion_ps"] - entry),
                    T_evlog_ps=run["T_evlog_ps"],
                    release_max_extra_ps=max(r["extra_ps"] for r in release.values()),
                    release_max_absolute_latency_ps=max(r["latency_ps"] for r in release.values()),
                    release_reference=run["release"]["reference"]["sha256"],
                    stages={tx: stage_timing(run, stages[tx], period, rows.get(tx)) for tx in (0, 1)})
        if kind == "healthy":
            counts["healthy"] += 1
            if mismatches or detected or any(row["outcome"] not in (1, 2) for row in rows.values()) \
                    or item["golden"] != "PASS":
                counts["healthy_false_positive"] += 1
        if kind == "injected_detection_on":
            counts["injected_on"] += 1
            tx = scene["recovery_fault_tx"]
            if rows[tx]["outcome"] == 3 and rows[tx]["commit"] == 1 and item["golden"] == "PASS":
                counts["recovered"] += 1
            if any(row["outcome"] == 4 for row in rows.values()):
                counts["unrecoverable"] += 1
        # A wrong final result is predeclared only for the detection-off baseline;
        # the negative probe must stop before the consumer prints any golden check.
        expected_golden = {"healthy": ["PASS"], "injected_detection_on": ["PASS"],
                           "injected_detection_off": ["FAIL"], "negative_probe": []}[kind]
        if golden != expected_golden or run["status"] != "PASS":
            counts["wrong_or_hang"] += 1
        runs[name] = item
    on, off = runs.get("s1b_healthy_on"), runs.get("s1b_healthy_off")
    return dict(
        status="PASS" if len(runs) == 7 and counts["healthy_false_positive"] == 0
        and counts["wrong_or_hang"] == 0 and counts["recovered"] == counts["injected_on"] == 3
        and counts["unrecoverable"] == 0 else "FAIL",
        counts=counts,
        recovery_rate=f'{counts["recovered"]}/{counts["injected_on"]}',
        false_positive_rate=f'{counts["healthy_false_positive"]}/{counts["healthy"]}',
        unrecoverable_rate=f'{counts["unrecoverable"]}/{counts["injected_on"]}',
        healthy_on_minus_off_T_evlog_us=us(on["T_evlog_ps"] - off["T_evlog_ps"]) if on and off else None,
        healthy_on_minus_off_entry_to_end_us=(on["entry_to_end_us"] - off["entry_to_end_us"]) if on and off else None,
        descriptor_and_buffer_increment=dict(
            path=str(descriptor_result), sha256=sha(descriptor_result),
            **{key: json.loads(Path(descriptor_result).read_text())[key]
               for key in ("normal_tasks", "descriptors", "application_payload_bytes")}),
        scope=("Empirical over the seven sealed scenes only: one fault site per injected scene "
               "(offset 17, mask 4). Rates are counts, not a statistical coverage estimate."),
        runs=runs)
