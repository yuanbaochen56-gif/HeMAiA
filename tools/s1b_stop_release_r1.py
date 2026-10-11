"""Seal a real G-release counterexample using only correctly sized observed signals."""
import json
from pathlib import Path
import re
from s1b_freeze_r1 import sha, write

W = Path(__file__).resolve().parents[1]
E = W / "evidence/ReleaseStopS1bR1"


def main():
    E.mkdir()
    root = W / "results/SystemS1bR4/s1b_healthy_off"
    observer = root / "observations.txt"
    bins, = root.glob("task_*/bin")
    log = (bins / "sim_run.log").read_text()
    uart = (bins / "uart_chip_0_0.log").read_text()
    raw = observer.read_text().splitlines()
    sealed_path = W / "evidence/PredictionsS1bR4/predictions.json"
    sealed = json.loads(sealed_path.read_text())
    events = []
    for line in raw:
        if line.startswith("SCHEMA "):
            continue
        values = line.split()
        events.append((int(values[0]), values[1], list(map(int, values[2:]))))
    dispatch = {data[0]: stamp for stamp, kind, data in events if kind == "DISPATCH"}
    done = {data[0]: stamp for stamp, kind, data in events if kind == "DONE"}
    start, = [stamp for stamp, kind, _ in events if kind == "START"]
    product_dispatch = {int(task): int(stamp) for stamp, task in re.findall(
        r"\[BINGO_DISPATCH\] (\d+) chip=0 task=(\d+)", log)}
    product_done = {int(task): int(stamp) for stamp, task in re.findall(
        r"\[BINGO_DONE\] (\d+) chip=0 task=(\d+)", log)}
    assert dispatch == product_dispatch and done == product_done
    consumer, = [int(task) for task, role in sealed["graph"]["roles"].items() if role == "consumer"]
    descriptor, = [node for node in sealed["descriptors"] if node["task"] == consumer]
    assert (descriptor["cluster"], descriptor["core"], descriptor["check_code"], descriptor["check_tag"]) == (0, 2, 4, 0)
    last_done = done[8]
    signal_set, = [stamp for stamp, kind, data in events
                  if kind == "SET" and data == [0, 2, 4, 0]
                  and last_done < stamp < dispatch[consumer]]
    checks = [stamp for stamp, kind, data in events
              if kind == "CHECK" and data[1:] == [0, 2, 4, 0]
              and signal_set < stamp < dispatch[consumer]]
    assert len(checks) == 1
    entry, = [node["id"] for node in sealed["graph"]["nodes"]
              if node["kernel"] == "__host_bingo_kernel_entry"]
    entry_descriptor, = [node for node in sealed["descriptors"] if node["task"] == entry]
    assert not entry_descriptor["check_en"]
    cases = [
        dict(task=entry, role="host_entry", eligible_ps=start, dispatch_ps=dispatch[entry],
             delay_ps=dispatch[entry] - start,
             evidence="Frozen sole-no-dependency START anchor; no older executing task"),
        dict(task=consumer, role="final_consumer", eligible_ps=max(signal_set, last_done),
             dispatch_ps=dispatch[consumer], previous_same_slot_done_ps=last_done,
             real_registered_tag_set_ps=signal_set, later_check_ps=checks[0],
             delay_ps=dispatch[consumer] - max(signal_set, last_done),
             cell=dict(cluster=0, row=2, col=2, tag=0),
             evidence="Real dep_set_valid/code/tag at manager posedge; actual scoreboard applies directly at that edge; target slot's resolver8 DONE already observed. No checkout task-ID or inferred skip DONE used."),
    ]
    assert sealed["release_observation"]["threshold_ps"] == 1000000
    assert all(case["delay_ps"] > 1000000 for case in cases)
    matrix = W.parent / "s1_20261010/bingo_hw_manager/src/bingo_hw_manager_dep_matrix.sv"
    assert "sb_q[r][c][t] <= sb_d[r][c][t]" in matrix.read_text()
    ends = re.findall(r"All chips finished successfully at (\d+)", log)
    assert len(ends) == 1
    result = dict(status="STOP_USER_REAL_G_RELEASE_FAILURE", frozen_cap_ps=1000000,
        counterexamples=cases, completed_application_runs=1, later_scenes=0, invalid_status_probes=0,
        semantics_seen=dict(golden_sink="PASS" if "PASS (0 / 64 bytes)" in uart else "NOT_CONFIRMED",
            simulator_success=True, return_word=1,
            recovery_records=re.findall(r"\[Host\] SDC_RECOVER [^\r\n]+", uart),
            success_envelope_ps=int(ends[0]) - dispatch[entry],
            # Historical failed R4 run: its unmeasured R6 limit was 8592 us.
            # Read that run's seal; the active R7 healthy gate is 8700 us.
            frozen_envelope_ps=sealed["budgets"]["normal_all_healthy_envelope"] * 1000000),
        observer_plumbing=dict(
            overall_parser="R4 stopped with KeyError640 before declaring a PASS",
            cause="Observer used legacy TaskDescWidth59 instead of full internal WdTaskDescWidth62. Packed-array resizing corrupted WAIT/CHECKOUT task fields. START, fixed32-bit DISPATCH/DONE, and direct SET/CHECK code/tag ports are correctly sized and enough for the independent consumer counterexample.",
            clock_completion_tcl="Tcl now printed but returned empty text; clock file has blank lines and completion timestamp is absent. No finite timestamp PASS claimed.",
            runtime_warnings="10 WT cache invalid-read warnings during diagnostics, retained and not reclassified"),
        action="Stop. No healthy_on, injections, invalid-status probe, recovery-rate calculation or S1b completion. Do not raise1us cap or retry this failed graph/image without new authority.",
        bindings={str(path): sha(path) for path in [
            observer, bins / "sim_run.log", bins / "uart_chip_0_0.log", matrix,
            sealed_path, W / "evidence/SystemS1bR4/result.json",
            W / "tools/s1b_readonly_observer_r3.sv", Path(__file__)]})
    write(E / "result.json", result)
    write(E / "manifest.json", {str(path): sha(path) for path in E.iterdir() if path.is_file()})
    print(json.dumps(dict(status=result["status"],
                          failures=[dict(task=case["task"], delay_ps=case["delay_ps"]) for case in cases])))


if __name__ == "__main__":
    main()
