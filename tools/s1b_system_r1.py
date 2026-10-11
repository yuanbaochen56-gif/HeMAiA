"""Run the frozen same-image recovery family, stopping at the first real failure."""
import argparse
from collections import defaultdict
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import subprocess
import sys
import time
import traceback

W = Path(__file__).resolve().parents[1]
E = W / "evidence/SystemS1bR1"
ROOT = W / "results/SystemS1bR1"
PRED = W / "evidence/PredictionsS1bR1/predictions.json"
sys.path[:0] = [str(W / "tools"), str(W / "util/automation_scripts"),
               str(W / "target/sim/automation/test")]
from s1b_observations import readiness
from bingo_sdc_recover_check import evaluate_recovery, recovery_records, EVENT
from bingo_evlog import ITEM, SUMMARY, check_evlog
from bingo_sdc_recover_reference import compare_release, reference_context, ReferenceError
from s1b_envelope_r7 import success_envelope_us


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            h.update(chunk)
    return h.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def require_clean_product_log(log):
    cache_warnings = [line for line in log.splitlines()
                      if "reading invalid data on port" in line.lower()
                      or ("warning" in line.lower() and "cache" in line.lower())]
    errors = re.findall(r"^.*\*\*\s*Error\b.*$", log, re.MULTILINE | re.IGNORECASE)
    errors += [count for count in re.findall(r"\bErrors:\s*(\d+)", log, re.IGNORECASE) if int(count)]
    if cache_warnings:
        raise ReferenceError("Product WT cache warning: STOP_USER; no observer waiver")
    if errors:
        raise ReferenceError("Questa error: STOP_USER; zero-error gate")
    return dict(status="PASS", cache_warnings=0, questa_errors=0)


def tcl_observer(sealed, negative):
    manager = sealed["release_observation"]["manager_hierarchy"]
    # Questa's `now` command prints and returns an empty string. Its documented
    # $now state variable contains ticks in the simulator resolution (-t 1ps).
    text = ('proc s1b_time_ps {} {\n'
            '  if {![info exists ::now] || ![string is entier -strict $::now] || $::now < 0} {\n'
            '    error "Unobservable simulator time at 1ps resolution"\n'
            '  }\n  return $::now\n}\n'
            'set S1B_CLOCK_FILE [open "clock_observations.txt" w]\nset S1B_CLOCK_N 0\n')
    text += ('when -label S1B_CLOCK {' + manager + '/clk_i == 1} {\n'
             '  puts $S1B_CLOCK_FILE "[s1b_time_ps]"\n'
             '  incr S1B_CLOCK_N\n'
             '  if {$S1B_CLOCK_N == 4} {flush $S1B_CLOCK_FILE; nowhen S1B_CLOCK}\n}\n')
    completion = sealed["completion_memory"]
    text += ('when -label S1B_COMPLETION {' + completion + ' == 1 || ' + completion + ' == 5} {\n'
             '  echo "S1B_COMPLETION_WRITTEN time=[s1b_time_ps] word=[examine -radix unsigned {'
             + completion + '}]"\n  nowhen S1B_COMPLETION\n}\n')
    # A source-level safe point after the real compare DONE, before the host can dispatch.
    if negative:
        probe = sealed["negative_probe"]
        text += ('when -label S1B_INVALID {' + probe["trigger"] + ' == 1} {\n'
                 '  set p {' + probe["memory"] + '}\n'
                 '  echo "S1B_INVALID_TRIGGER time=[s1b_time_ps] before=[examine -radix hex $p]"\n'
                 '  force -deposit $p 16#' + probe["deposit_64bit_hex"] + '\n'
                 '  echo "S1B_INVALID_WRITTEN time=[s1b_time_ps] after=[examine -radix hex $p]"\n'
                 '  nowhen S1B_INVALID\n}\n')
    return text


def judge(name, scene, log, uart, observer, sealed, reference=None, *, reference_capture=False):
    warning_gate = require_clean_product_log(log)
    observed = readiness(observer, sealed["descriptors"], sealed["clocks"]["manager_period_ps"])
    native_scene = dict(scene, recovery_eligibility=observed["eligibility"],
                        recovery_reference=reference, recovery_reference_context=reference_context(sealed))
    problems = evaluate_recovery(native_scene, log, uart, reference_capture=reference_capture)
    release = (dict(status="REFERENCE_CAPTURE", absolute_latency_ps=observed["waits_ps"])
               if reference_capture else compare_release(observed["eligibility"], observed["dispatch"],
                                                        reference, reference_context(sealed)))
    actual_done = defaultdict(list)
    product_dispatch, product_done = {}, {}
    for kind, stamp, chip, task, core, cluster in EVENT.findall(log):
        task, stamp = int(task), int(stamp)
        (product_dispatch if kind == "DISPATCH" else product_done)[task] = stamp
        if kind == "DONE":
            # The frozen model's slot is (cluster, core). evaluate_recovery
            # independently rejects every event outside chip0.
            actual_done[f"{int(cluster)}:{int(core)}"].append(task)
    prediction = sealed["reference_prediction"] if reference_capture else sealed["predictions"][name]
    if dict(actual_done) != prediction["done_by_slot"]:
        problems.append("Per-slot DONE sequence differs from frozen tagged model")
    if product_dispatch != observed["dispatch"] or product_done != observed["done"]:
        problems.append("Passive raw hardware DISPATCH/DONE differs from product event stream")
    planned = [event["task"] for event in prediction["model"]["events"]
               if event["kind"] == "DISPATCH"]
    if set(product_dispatch) != set(planned):
        problems.append("Executing set differs from pre-run model")
    rows = recovery_records(uart)
    period = sealed["clocks"]["host_mcycle_period_ps"]
    stages = sealed["graph"]["stages"]
    # Use one observed dispatch and one resident timestamp to anchor host mcycle.
    # Only differences are required for gates; affine offsets are not used to forgive waits.
    for row in rows:
        tx = row["tx"]
        duration = product_done.get(stages[tx]["resolver"])
        dispatch = product_dispatch[stages[tx]["resolver"]]
        budget = sealed["budgets"]["kernel_bounds"][
            "resolver_mismatch_including_report" if row["third"] else "resolver_healthy_including_report"]
        if duration is not None and duration - dispatch > budget * 1000000:
            problems.append(f"Resolver{tx} kernel budget exceeded")
        if duration is not None and (row["publish_end"] - row["rstart"]) * period >= duration - dispatch:
            problems.append("Publication timestamp not strictly before resolver DONE")
    roles = sealed["graph"]["roles"]
    for task, dispatch in product_dispatch.items():
        if task not in product_done:
            continue
        role = roles.get(str(task), "")
        node = next(node for node in sealed["graph"]["nodes"] if node["id"] == task)
        key = ("host_entry" if "__host_bingo_kernel_entry" == node["kernel"] else
               "host_init" if role == "init" else
               "quiet_compare_each" if role.startswith("compare") else
               "device_dma_each" if role.startswith(("P", "S")) else
               "host_sysDMA_consumer" if role == "consumer" else
               "device_exit_each" if node["kernel"] == "__snax_bingo_kernel_exit" else None)
        if key and product_done[task] - dispatch > sealed["budgets"]["kernel_bounds"][key] * 1000000:
            problems.append(f"Task{task} {key} budget exceeded")
    exits = re.findall(r"All chips finished (?:successfully|with errors) at (\d+)", log)
    if len(exits) != 1:
        problems.append("Missing unique finite simulator completion")
    end = int(exits[0]) if exits else None
    entry = next(node["id"] for node in sealed["graph"]["nodes"]
                 if node["kernel"] == "__host_bingo_kernel_entry")
    if end is not None and not scene.get("recovery_negative"):
        mismatch_count = sum(row["third"] for row in rows)
        bound = success_envelope_us(sealed["budgets"], mismatch_count)
        if end - product_dispatch[entry] > bound * 1000000:
            problems.append("Frozen success envelope exceeded")
    summaries = [tuple(map(int, pair)) for pair in SUMMARY.findall(uart)]
    items = [tuple([int(t), int(code, 16), int(cluster), int(core), int(arg, 16)])
             for t, code, cluster, core, arg in ITEM.findall(uart)]
    if len(summaries) != 1 or summaries[0][1] or len(items) != summaries[0][0]:
        problems.append("Missing, dropped or inconsistent resident EVLOG")
    problems += check_evlog(log, uart, 3, period_ps=period, allow_empty=True)
    boundaries = re.findall(r"\[EVLOG_BEGIN\] (\d+)", log)
    if len(boundaries) != 1:
        problems.append("Missing unique inherited pre-print EVLOG boundary")
    t_evlog = int(boundaries[0]) if boundaries else None
    return dict(status="FAIL" if problems else "PASS", problems=problems,
                warning_gate=warning_gate,
                readiness=observed, release=release, recovery=rows, dispatch=product_dispatch, done=product_done,
                completion_ps=end, T_evlog_ps=t_evlog,
                evlog=dict(summaries=summaries, items=items))


def finite_trace(scene, log, bins, sealed):
    statuses = re.findall(r"S1B_COMPLETION_WRITTEN time=(\d+).*word=(\d+)", log)
    assert len(statuses) == 1, ("Unique physical completion word observation", statuses)
    word_time, word = map(int, statuses[0])
    expected = 5 if scene.get("recovery_negative") or not scene["recovery_correct"] else 1
    assert word == expected, ("Completion word", word, expected)
    end, = re.findall(r"All chips finished (?:successfully|with errors) at (\d+)", log)
    assert 0 <= int(end) - word_time <= sealed["completion_word_to_sim_end_ps"]
    polls = re.findall(r"Simulation of chip_0_0 finished successfully at (\d+)", log)
    errors = re.findall(r"Simulation of chip_0_0 finished with status (\d+) at (\d+)", log)
    assert (len(polls), len(errors)) == ((1, 0) if expected == 1 else (0, 1))
    if errors:
        assert int(errors[0][0]) == expected
    pcs = set(sealed["host_resolver_return_pcs"])
    trace, = bins.glob("logs/trace_chip_00_hart_00000.log")
    returns = []
    with trace.open() as stream:
        for line in stream:
            match = re.match(r"\s*(\d+)ns\s+\d+\s+\w+\s+([0-9a-fA-F]+)\s", line)
            if match and int(match[2], 16) in pcs:
                returns.append(int(match[1]) * 1000)
    assert len(returns) == (1 if scene.get("recovery_negative") else 2), ("Actual resolver returns", returns)
    if scene.get("recovery_negative"):
        assert 0 <= word_time - returns[0] <= 2048 * 1000000, "FAIL return-to-word finite budget"
        assert len(re.findall("S1B_INVALID_TRIGGER", log)) == 1
        written, = re.findall(r"S1B_INVALID_WRITTEN time=(\d+).*after=([0-9a-fA-F]+)", log)
        assert int(written[1], 16) == 0xFFFFFFFF
        events = {(kind, int(task)): int(stamp) for kind, stamp, _, task, _, _ in EVENT.findall(log)}
        assert events["DONE", 3] <= int(written[0]) < events["DISPATCH", 4]
    return dict(completion_word=word, completion_word_ps=word_time, simulator_end_ps=int(end),
                resolver_return_ps=returns, word_to_end_ps=int(end) - word_time,
                fail_return_to_word_ps=word_time - returns[0] if scene.get("recovery_negative") else None)


def main():
    E.mkdir()
    ROOT.mkdir()
    sealed = json.loads(PRED.read_text())
    pred_sha = sha(PRED)
    state = dict(status="RUNNING", pid=os.getpid(), started=datetime.datetime.now().astimezone().isoformat(),
                 predictions_sha256=pred_sha, runs=[], simulation_started=0, current=None)
    active = {}
    original_popen = subprocess.Popen
    original_run = subprocess.run
    children = {}

    def save():
        write(E / "progress.json", state)

    def integrity():
        assert sha(PRED) == pred_sha
        for path, digest in sealed["bindings"].items():
            assert sha(path) == digest, ("Frozen input mutated", path)
        assert all(sha(W / path) == digest for path, digest in sealed["source_hashes"].items())
        assert shutil.disk_usage(W).free >= 10 * 1024**3, "Running disk threshold"

    class GuardedPopen(original_popen):
        def __init__(self, *args, **kwargs):
            kwargs["start_new_session"] = True
            super().__init__(*args, **kwargs)
            self.deadline = time.monotonic() + 3600
            children[self.pid] = self

        def wait(self, timeout=None):
            deadline = self.deadline if timeout is None else min(
                self.deadline, time.monotonic() + timeout)
            while self.poll() is None:
                if shutil.disk_usage(W).free < 10 * 1024**3 or time.monotonic() >= deadline:
                    os.killpg(self.pid, signal.SIGTERM)
                    super().wait(timeout=15)
                    raise RuntimeError("Owned simulation wall/disk guard")
                time.sleep(0.5)
            return super().wait(timeout=0)

    with (E / "runner.lock").open("x") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert shutil.disk_usage(W).free >= int(10.5 * 1024**3)
            assert os.environ["MTI_VCO_MODE"] == os.environ["QSIM_VCO_MODE"] == "64"
            assert sealed["simulations_before_seal"] == 0
            prep = json.loads(Path(sealed["preparation_gate"]["path"]).read_text())
            H = Path(prep["product_build_copy"])
            sys.path[:0] = [str(H / "util/automation_scripts")]
            module = runpy.run_path(str(H / "target/sim/automation/test/3_start_bingo_watchdog_sim.py"))
            Runner = module["TestCfgSimRunner"]
            row = sealed["image"]

            def cached_stage(runner, tasks):
                assert len(tasks) == 1
                ref = Path(row["output"])
                old = json.loads((ref / "test_cfg.json").read_text())
                source_task, = ref.glob("task_*")
                task_dir = runner.output_dir / module["task_dir_name"](0, tasks[0]["ci_name"])
                dest = task_dir / "bin"
                dest.mkdir(parents=True)
                for part in ("app_chip_0_0", "mempool"):
                    shutil.copytree(source_task / "bin" / part, dest / part)
                    assert {str(p.relative_to(dest / part)): sha(p) for p in (dest / part).rglob("*")
                            if p.is_file()} == row["part_hashes"][part]
                for filename in ("test_cfg_host.elf", "test_cfg_unpatched.bin"):
                    shutil.copyfile(ref / filename, runner.output_dir / filename)
                defaults = module["test_cfg_image_defaults"](
                    3, module["t1_image_flags"](sealed["scenes"][state["current"]]))
                module["patch_test_cfg"](dest / "app_chip_0_0", old["location"], defaults)
                assert module["read_bank_image"](dest / "app_chip_0_0") == (ref / "test_cfg_unpatched.bin").read_bytes()
                runner.test_cfg_record = module["patch_test_cfg"](
                    dest / "app_chip_0_0", old["location"], runner.test_cfg)
                assert runner.test_cfg_record["image_id"] == row["image_id"]
                write(runner.output_dir / "test_cfg.json", runner.test_cfg_record)
                return [(task_dir, tasks[0]["ci_name"])]

            original_stage = module["HeMAiASimRunner"]._stage_sim_runtime

            def runtime(runner, task_dir):
                original_stage(runner, task_dir)
                (task_dir / "bin/observer.tcl").write_text(
                    tcl_observer(sealed, sealed["scenes"][state["current"]].get("recovery_negative", False)))

            def forbidden_container(*args, **kwargs):
                raise RuntimeError("Same-image runner attempted an unplanned container/build")

            Runner.build_apps_and_stage = cached_stage
            module["HeMAiASimRunner"]._stage_sim_runtime = runtime
            module["HeMAiASimRunner"]._container.__globals__["run_in_container"] = forbidden_container
            subprocess.Popen = GuardedPopen
            records = []
            for name, scene in sealed["scenes"].items():
                state["current"] = name
                integrity()
                assert shutil.disk_usage(W).free >= int(10.5 * 1024**3)
                active.clear()
                output = ROOT / name
                output.mkdir()
                observer_path = output / "observations.txt"
                runner = Runner(
                    repo_root=H, output_dir=output, engine="vsim", with_waveform=False,
                    cfg="target/rtl/cfg/hemaia_ci.hjson", sim_cfg="target/sim/cfg/sim_rtl.hjson",
                    with_macro=False, with_d2d=False, with_pll=False, max_jobs=1,
                    skip_setup=True, skip_build=True, skip_compile=True, build_sw_fleet=False,
                    fail_on_task_failure=False, timeout_seconds=3600,
                    test_cfg=sealed["predictions"][name]["cfg"],
                    plusargs=["+bingo_dispatch_log", "+evlog_check", "+max-cycles=15000000",
                              "+S1B_OBSERVATIONS=" + str(observer_path)])
                state["simulation_started"] += 1
                save()
                task = module["make_task"](host_app_type="offload_bingo_hw", chip_type="single_chip",
                                           workload=scene["workload"], dev_app="snax-bingo-offload")
                task["extra_user_flags"] = "-DBINGO_TEST_CFG=1 " + module["t1_image_flags"](scene)
                runner.run([task])
                bins, = output.glob("task_*/bin")
                log = (bins / "sim_run.log").read_text()
                uart = (bins / "uart_chip_0_0.log").read_text()
                active.update(judge(name, scene, log, uart, observer_path.read_text(), sealed))
                active["finite_trace"] = finite_trace(scene, log, bins, sealed)
                clock_text = (bins / "clock_observations.txt").read_text()
                edges = [int(re.match(r"(\d+)", line).group(1))
                         for line in clock_text.splitlines()]
                assert len(edges) == 4
                assert all(b - a == 28000 for a, b in zip(edges, edges[1:])), ("Actual manager clock", edges)
                active["actual_manager_edges_ps"] = edges
                record = json.loads((output / "test_cfg.json").read_text())
                assert record["configuration"] == sealed["predictions"][name]["cfg"]
                records.append(record)
                module["check_t1_family"](records)
                active.update(name=name, directory=str(output), image_id=record["image_id"])
                write(output / "judgement.json", active)
                state["runs"].append(dict(active))
                save()
                if active["status"] != "PASS":
                    raise RuntimeError(("Real recovery gate failure", name, active["problems"]))
                if len(state["runs"]) == 2:
                    assert all(run["status"] == "PASS" for run in state["runs"])
                    write(E / "healthy_pair_gates.json", dict(
                        status="PASS", gates=["G-consume", "G-vote", "G-once", "G-status",
                                             "G-release", "G-finite"],
                        runs=[run["name"] for run in state["runs"]],
                        predictions_sha256=pred_sha))
                integrity()
                print(name + ": PASS", flush=True)
            state.update(status="PASS", current=None)
        except BaseException as error:
            state.update(status="STOP_USER", reason=repr(error), traceback=traceback.format_exc(),
                         failure=active)
            write(E / "stop.json", state)
        finally:
            for child in children.values():
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    original_popen.wait(child, timeout=15)
            subprocess.Popen, subprocess.run = original_popen, original_run
        state["finished"] = datetime.datetime.now().astimezone().isoformat()
        save()
        manifest = {str(path): sha(path) for path in ROOT.rglob("*") if path.is_file()}
        manifest.update({str(path): sha(path) for path in [E / "progress.json", PRED, Path(__file__)]})
        write(E / "manifest.json", manifest)
        write(E / "result.json", dict(status=state["status"], runs=len(state["runs"]),
                                     simulations_started=state["simulation_started"],
                                     reason=state.get("reason"), manifest_sha256=sha(E / "manifest.json"),
                                     predictions_sha256=pred_sha))
        print(state["status"], state.get("reason", ""), flush=True)
        return int(state["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
