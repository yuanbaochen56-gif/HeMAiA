"""R10 runner: scenes 5 to 7 under the frozen r8 gate profile.

Runs s1b_stage0_primary_off, s1b_stage0_shadow_on and s1b_invalid_status in
frozen order. Both per-configuration references and the R9 injected-task
budgets are loaded, never captured or re-derived. The judge is the unchanged r1
judge plus the R9 budget; the r8 profile replaces only the zero-error check of
the two word-5 scenes and the success envelope of s1b_stage0_primary_off.
First failure stops.
"""
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
import traceback

W = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(W / "tools"), str(W / "util/automation_scripts"),
               str(W / "target/sim/automation/test")]
import bingo_sdc_recover_check
import s1b_system_r1
from s1b_system_r1 import sha, write, finite_trace, tcl_observer
from bingo_sdc_recover_reference import reference_context, ReferenceError
import s1b_reference_r8 as r8
import s1b_injected_budget_r9 as budget
import s1b_profile_r10 as profiles
from s1b_system_r8 import planned_dispatch

E = None
ROOT = None
PRED = None
REUSED_R8 = ("s1b_healthy_off", "s1b_healthy_on")
REUSED_R9 = ("s1b_stage0_primary_on", "s1b_stage1_primary_on")
REUSED = REUSED_R8 + REUSED_R9
PLANNED = ("s1b_stage0_primary_off", "s1b_stage0_shadow_on", "s1b_invalid_status")


def scene_plan(sealed):
    names = list(sealed["scenes"])
    if names != list(REUSED + PLANNED) or not sealed["scenes"][names[-1]].get("recovery_negative"):
        raise ReferenceError("Frozen scenario order changed")
    return [(name, sealed["scenes"][name]) for name in PLANNED]


def judged_run(name, scene, log, uart, observer, sealed, reference, record, profile):
    return profiles.judge(name, scene, log, uart, observer, sealed, reference, record, profile)


def main(*, bindings, budget_binding, profile_binding, plan_binding, seed_records):
    E.mkdir()
    ROOT.mkdir()
    sealed = json.loads(PRED.read_text())
    pred_sha = sha(PRED)
    context = reference_context(sealed)
    record = budget.load(budget_binding)
    profile = profiles.load(profile_binding)
    state = dict(status="RUNNING", pid=os.getpid(),
                 started=datetime.datetime.now().astimezone().isoformat(),
                 predictions_sha256=pred_sha, plan=plan_binding, budgets=budget_binding,
                 gate_profile=profile_binding,
                 references={str(k): v for k, v in bindings.items()}, runs=[], simulation_started=0,
                 current=None)
    active, children = {}, {}
    original_popen = subprocess.Popen
    original_compare = (bingo_sdc_recover_check.compare_release, s1b_system_r1.compare_release)
    stop_guard = threading.Event()
    guard_error = []

    def save():
        write(E / "progress.json", state)

    def integrity():
        assert sha(PRED) == pred_sha
        assert sha(plan_binding["path"]) == plan_binding["sha256"], "R10 plan changed"
        assert Path(plan_binding["hash_path"]).read_text() == plan_binding["sha256"] + "  plan.json\n"
        plan = json.loads(Path(plan_binding["path"]).read_text())
        for path, digest in plan["tools"].items():
            assert sha(path) == digest, ("R10 tool changed", path)
        for path, digest in plan["reused_inputs"].items():
            assert sha(path) == digest, ("Reused R8/R9 input changed", path)
        for path, digest in sealed["bindings"].items():
            assert sha(path) == digest, ("Frozen input changed", path)
        assert all(sha(W / path) == digest for path, digest in sealed["source_hashes"].items())
        assert budget.load(budget_binding) == record
        assert profiles.load(profile_binding) == profile
        assert shutil.disk_usage(W).free >= 10 * 1024**3
        assert not guard_error, guard_error
        for binding in bindings.values():
            r8.load_reference(binding, context)

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
            assert sorted(bindings) == [0, 1]
            integrity()
            bingo_sdc_recover_check.compare_release = r8.compare_release
            s1b_system_r1.compare_release = r8.compare_release
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
                    (task_dir / "bin/observer.tcl").write_text(tcl_observer(
                        sealed, sealed["scenes"][state["current"]].get("recovery_negative", False)))

            def forbidden_container(*args, **kwargs):
                raise RuntimeError("Same-image runner requested unplanned container/build")

            Runner.build_apps_and_stage = cached_stage
            module["HeMAiASimRunner"].stage_simulation_artifacts = runtime
            module["HeMAiASimRunner"]._container.__globals__["run_in_container"] = forbidden_container
            subprocess.Popen = OwnedPopen
            records = list(seed_records)
            module["check_t1_family"](records)
            for name, scene in scene_plan(sealed):
                state["current"] = name
                cfg = sealed["predictions"][name]["cfg"]
                reference = r8.select_reference(bindings, scene, cfg)
                r8.require_reference_tasks(reference, context, planned_dispatch(sealed, name))
                state["current_reference"] = reference
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
                    fail_on_task_failure=False, timeout_seconds=sealed["simulation_guards"]["wall_timeout_seconds"],
                    test_cfg=cfg,
                    plusargs=["+bingo_dispatch_log", "+evlog_check",
                              "+max-cycles=" + str(sealed["simulation_guards"]["master_max_cycles"]),
                              "+S1B_OBSERVATIONS=" + str(observer_path)])
                task = module["make_task"](host_app_type="offload_bingo_hw", chip_type="single_chip",
                                           workload=scene["workload"], dev_app="snax-bingo-offload")
                task["extra_user_flags"] = "-DBINGO_TEST_CFG=1 " + module["t1_image_flags"](scene)
                save()
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
                profiles.log_gate(name, log, profile)
                observer = observer_path.read_text()
                if not observer.startswith("SCHEMA 2 C=3 CL=2 TAG=4 IDW=12 DW=62\n"):
                    raise ReferenceError("Corrected 62-bit observer schema missing")
                active.update(judged_run(name, scene, log, uart, observer, sealed, reference, record, profile))
                active["finite_trace"] = finite_trace(scene, log, bins, sealed)
                edges = [int(re.match(r"(\d+)", line).group(1))
                         for line in (bins / "clock_observations.txt").read_text().splitlines()]
                assert len(edges) == 4
                assert all(b - a == sealed["clocks"]["manager_period_ps"]
                           for a, b in zip(edges, edges[1:])), ("Actual manager clock", edges)
                active["actual_manager_edges_ps"] = edges
                site = budget.injected_site(scene)
                if site is not None:
                    task_id, cluster, core = site[:3]
                    active["hook_trace"] = budget.decompose(
                        bins, task_id, cluster, core, active["dispatch"][task_id], active["done"][task_id], True)
                test_record = json.loads((output / "test_cfg.json").read_text())
                assert test_record["configuration"] == cfg
                records.append(test_record)
                module["check_t1_family"](records)
                active.update(name=name, directory=str(output), image_id=test_record["image_id"],
                              configuration=r8.CONFIGURATIONS[r8.scene_configuration(scene, cfg)])
                write(output / "judgement.json", active)
                state["runs"].append(dict(active))
                save()
                if active["status"] != "PASS":
                    raise RuntimeError(("Real recovery gate failure", name, active["problems"]))
                integrity()
                print(name + ": PASS", flush=True)
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
            bingo_sdc_recover_check.compare_release, s1b_system_r1.compare_release = original_compare
        state["finished"] = datetime.datetime.now().astimezone().isoformat()
        save()
        manifest = {str(path): sha(path) for path in ROOT.rglob("*") if path.is_file()}
        manifest.update({str(path): sha(path) for path in [E / "progress.json", PRED, Path(__file__),
                                                         Path(plan_binding["path"]), Path(budget_binding["path"]),
                                                         Path(profile_binding["path"])]})
        for binding in bindings.values():
            manifest.update({binding[key]: sha(binding[key]) for key in ("path", "hash_path")})
        write(E / "manifest.json", manifest)
        write(E / "result.json", dict(status=state["status"], runs=len(state["runs"]),
                                     simulations_started=state["simulation_started"],
                                     references=state["references"], budgets=budget_binding,
                                     gate_profile=profile_binding,
                                     reason=state.get("reason"), manifest_sha256=sha(E / "manifest.json"),
                                     predictions_sha256=pred_sha, plan_sha256=plan_binding["sha256"]))
        print(state["status"], state.get("reason", ""), flush=True)
        return int(state["status"] != "PASS")
