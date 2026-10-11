"""Capture and freeze L_ref before any recovery scenario; fail closed on unobservable tasks."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import runpy
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback

W = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(W / "tools"), str(W / "util/automation_scripts"),
               str(W / "target/sim/automation/test")]
from s1b_system_r1 import sha, write, judge, finite_trace, tcl_observer

from bingo_sdc_recover_reference import (freeze_reference, load_reference, reference_context,
                                         require_reference_tasks, ReferenceError)
from s1b_reference_control import (reference_scene, reference_prediction, reference_control_tcl,
                                   check_reference_control)

E = W / "evidence/SystemS1bR5"
ROOT = W / "results/SystemS1bR5"
PRED = W / "evidence/PredictionsS1bR5/predictions.json"


def scene_plan(sealed):
    names = list(sealed["scenes"])
    if names[:2] != ["s1b_healthy_off", "s1b_healthy_on"]:
        raise ReferenceError("Frozen healthy-pair scenario order changed")
    return [("s1b_reference_recovery_off", "s1b_healthy_on", reference_scene(sealed), True)] + [
        (name, name, scene, False) for name, scene in sealed["scenes"].items()]


def require_clean_product_log(log):
    if "reading invalid data on port" in log:
        raise ReferenceError("Product WT cache invalid-read warning: STOP_USER; no observer waiver")


def main():
    E.mkdir()
    ROOT.mkdir()
    sealed = json.loads(PRED.read_text())
    pred_sha = sha(PRED)
    state = dict(status="RUNNING", pid=os.getpid(),
                 started=datetime.datetime.now().astimezone().isoformat(),
                 predictions_sha256=pred_sha, runs=[], simulation_started=0, current=None,
                 retained_import_failure=str(W / "evidence/system_s1b_r1.console.log"))
    active, children = {}, {}
    reference = None
    context = reference_context(sealed)
    original_popen = subprocess.Popen
    stop_guard = threading.Event()
    guard_error = []

    def save():
        write(E / "progress.json", state)

    def integrity():
        assert sha(PRED) == pred_sha
        for path, digest in sealed["bindings"].items():
            assert sha(path) == digest, ("Frozen input changed", path)
        assert all(sha(W / path) == digest for path, digest in sealed["source_hashes"].items())
        assert shutil.disk_usage(W).free >= 10 * 1024**3
        assert not guard_error, guard_error
        if reference is not None:
            load_reference(reference, context)

    class OwnedPopen(original_popen):
        def __init__(self, *args, **kwargs):
            kwargs["start_new_session"] = True
            super().__init__(*args, **kwargs)
            children[self.pid] = self

    def disk_guard():
        while not stop_guard.wait(1):
            if shutil.disk_usage(W).free < 10 * 1024**3:
                guard_error.append("Running free space below 10 GiB")
                for child in list(children.values()):
                    if child.poll() is None:
                        os.killpg(child.pid, signal.SIGTERM)
                return

    with (E / "runner.lock").open("x") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        guard = threading.Thread(target=disk_guard, daemon=True)
        guard.start()
        try:
            assert os.environ["MTI_VCO_MODE"] == os.environ["QSIM_VCO_MODE"] == "64"
            assert sealed["simulations_before_seal"] == 0
            assert sealed["reference_prediction"] == reference_prediction(sealed), "Reference prediction changed"
            H = Path(json.loads(Path(sealed["preparation_gate"]["path"]).read_text())["product_build_copy"])
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

            original_stage = module["HeMAiASimRunner"].stage_simulation_artifacts

            def runtime(runner, tasks_info):
                original_stage(runner, tasks_info)
                for task_dir, _ in tasks_info:
                    observer = tcl_observer(sealed, sealed["scenes"][state["current"]].get("recovery_negative", False))
                    if state["reference_capture"]:
                        observer += reference_control_tcl(sealed["reference_prediction"]["control"])
                    (task_dir / "bin/observer.tcl").write_text(observer)

            def forbidden_container(*args, **kwargs):
                raise RuntimeError("Same-image runner requested unplanned container/build")

            Runner.build_apps_and_stage = cached_stage
            module["HeMAiASimRunner"].stage_simulation_artifacts = runtime
            module["HeMAiASimRunner"]._container.__globals__["run_in_container"] = forbidden_container
            subprocess.Popen = OwnedPopen
            records = []
            for output_name, name, scene, capture in scene_plan(sealed):
                state["current"] = name
                state["reference_capture"] = capture
                if not capture:
                    if reference is None:
                        raise ReferenceError("Reference not frozen before scenario launch")
                    planned = [event["task"] for event in sealed["predictions"][name]["model"]["events"]
                               if event["kind"] == "DISPATCH"]
                    require_reference_tasks(reference, context, planned)
                integrity()
                assert shutil.disk_usage(W).free >= int(10.5 * 1024**3)
                active.clear()
                output = ROOT / output_name
                output.mkdir()
                observer_path = output / "observations.txt"
                runner = Runner(
                    repo_root=H, output_dir=output, engine="vsim", with_waveform=False,
                    cfg="target/rtl/cfg/hemaia_ci.hjson", sim_cfg="target/sim/cfg/sim_rtl.hjson",
                    with_macro=False, with_d2d=False, with_pll=False, max_jobs=1,
                    skip_setup=True, skip_build=True, skip_compile=True, build_sw_fleet=False,
                    fail_on_task_failure=False, timeout_seconds=sealed["simulation_guards"]["wall_timeout_seconds"],
                    test_cfg=sealed["predictions"][name]["cfg"],
                    plusargs=["+bingo_dispatch_log", "+evlog_check",
                              "+max-cycles=" + str(sealed["simulation_guards"]["master_max_cycles"]),
                              "+S1B_OBSERVATIONS=" + str(observer_path)])
                task = module["make_task"](host_app_type="offload_bingo_hw", chip_type="single_chip",
                                           workload=scene["workload"], dev_app="snax-bingo-offload")
                task["extra_user_flags"] = "-DBINGO_TEST_CFG=1 " + module["t1_image_flags"](scene)
                save()
                # Count at the real simulation call, not during app staging.
                original_simulations = runner.run_simulations

                def counted(tasks_info):
                    state["simulation_started"] += 1
                    save()
                    return original_simulations(tasks_info)

                runner.run_simulations = counted
                runner.run([task])
                bins, = output.glob("task_*/bin")
                log = (bins / "sim_run.log").read_text()
                uart = (bins / "uart_chip_0_0.log").read_text()
                require_clean_product_log(log)
                observer = observer_path.read_text()
                if not observer.startswith("SCHEMA 2 C=3 CL=2 TAG=4 IDW=12 DW=62\n"):
                    raise ReferenceError("Corrected 62-bit observer schema missing")
                active.update(judge(name, scene, log, uart, observer, sealed, reference,
                                    reference_capture=capture))
                active["finite_trace"] = finite_trace(scene, log, bins, sealed)
                edges = [int(re.match(r"(\d+)", line).group(1))
                         for line in (bins / "clock_observations.txt").read_text().splitlines()]
                assert len(edges) == 4
                assert all(b - a == sealed["clocks"]["manager_period_ps"]
                           for a, b in zip(edges, edges[1:])), ("Actual manager clock", edges)
                active["actual_manager_edges_ps"] = edges
                record = json.loads((output / "test_cfg.json").read_text())
                assert record["configuration"] == sealed["predictions"][name]["cfg"]
                if not capture:
                    records.append(record)
                    module["check_t1_family"](records)
                active.update(name=output_name, directory=str(output), image_id=record["image_id"])
                write(output / "judgement.json", active)
                if not capture:
                    state["runs"].append(dict(active))
                save()
                if active["status"] != "PASS":
                    raise RuntimeError(("Real recovery gate failure", name, active["problems"]))
                if capture:
                    active["reference_control"] = check_reference_control(
                        log, sealed, active["dispatch"], active["done"])
                    write(output / "judgement.json", active)
                    reference = freeze_reference(E / "reference", active["readiness"]["eligibility"],
                        active["dispatch"], context,
                        dict(sdc_enable=1, recovery_enable=0, fault_tx=None, negative=False, gates_status="PASS",
                             recovery_runs_started=len(state["runs"]), directory=str(output),
                             reference_control=active["reference_control"],
                             observations_sha256=sha(observer_path), log_sha256=sha(bins / "sim_run.log"),
                             uart_sha256=sha(bins / "uart_chip_0_0.log"),
                             judgement_sha256=sha(output / "judgement.json"),
                             predictions_sha256=pred_sha, configuration=record["configuration"]))
                    state["reference"] = reference
                    save()  # File + pinned hash persisted before any scenario launch.
                    # Fail before any recovery scenario if its same task was skipped.
                    tasks = [event["task"] for prediction in sealed["predictions"].values()
                             for event in prediction["model"]["events"] if event["kind"] == "DISPATCH"]
                    require_reference_tasks(reference, context, tasks)
                if len(state["runs"]) == 2:
                    write(E / "healthy_pair_gates.json", dict(
                        status="PASS", gates=["G-consume", "G-vote", "G-once", "G-status",
                                             "G-release", "G-finite"],
                        runs=[run["name"] for run in state["runs"]],
                        predictions_sha256=pred_sha))
                integrity()
                print(output_name + ": PASS", flush=True)
            state.update(status="PASS", current=None)
        except BaseException as error:
            state.update(status="STOP_USER", reason=repr(error), traceback=traceback.format_exc(), failure=active)
            write(E / "stop.json", state)
        finally:
            stop_guard.set()
            guard.join(timeout=5)
            for child in list(children.values()):
                if child.poll() is None:
                    os.killpg(child.pid, signal.SIGTERM)
                    original_popen.wait(child, timeout=15)
            subprocess.Popen = original_popen
        state["finished"] = datetime.datetime.now().astimezone().isoformat()
        save()
        manifest = {str(path): sha(path) for path in ROOT.rglob("*") if path.is_file()}
        manifest.update({str(path): sha(path) for path in [E / "progress.json", PRED, Path(__file__)]})
        if reference is not None:
            manifest.update({reference[key]: sha(reference[key]) for key in ("path", "hash_path")})
        write(E / "manifest.json", manifest)
        write(E / "result.json", dict(status=state["status"], runs=len(state["runs"]),
                                     simulations_started=state["simulation_started"],
                                     reason=state.get("reason"), manifest_sha256=sha(E / "manifest.json"),
                                     predictions_sha256=pred_sha))
        print(state["status"], state.get("reason", ""), flush=True)
        return int(state["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
