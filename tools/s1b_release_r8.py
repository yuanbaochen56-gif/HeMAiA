"""R8: per-configuration frozen L_ref, then S1b-2 checks, S1b-3 scenes and S1b-4 metrics.

r7 compared SDC-off scenes with the SDC-on reference. Task 15's measured
window spans the serial device exits, whose run time depends on whether
cluster 1 executed the shadow/compare kernels. R8 keeps the r7 profile, gate
values and SDC-on L_ref unchanged and adds a frozen SDC-off reference.

--check-only runs the offline preflight and S1b-2 integrity without writing.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess

from bingo_sdc_recover_reference import reference_context
import s1b_release_r7
import s1b_system_r8 as runner
from s1b_metrics_r8 import metrics
import s1b_reference_r8 as r8
from s1b_system_r1 import sha, write

W = Path(__file__).resolve().parents[1]
E = W / "evidence/GateS1bR8"
RESULTS = W / "results/GateS1bR8"
PRED = s1b_release_r7.PRED
R7_COMMIT = "1a9c05f2ea86f90ad696ca72fca47d74e0a71738"
SDC_ON_SHA = "95396f31ba6779b2447d265cb8c9d36946a3acc9286948415199b1909b93ec75"
PRED_SHA = "21d8b3bd399ddb86f16b6ba558ef5ed00a911e676a73fd46312c66d2f4bd202a"
DEFAULT_IMAGE = "11476c2f08e1454565dec6a3e8bcfd5010b74bf09369017aa9d211238cc49f70"
TOOLS = ["s1b_reference_r8.py", "s1b_system_r8.py", "s1b_release_r8.py", "s1b_metrics_r8.py",
         "test_s1b_reference_r8.py", "s1b_release_r7.py", "s1b_envelope_r7.py"]
DIAGNOSIS = dict(
    task=15, kernel="__host_bingo_kernel_exit", slot="0:2",
    window="task15 eligibility = task9 DONE; dummy 26 ahead of it in slot 0:2 waits for the device exit chain 11->12->13->14",
    healthy_off_latency_ps=872_676_000, sdc_on_L_ref_ps=864_920_000, extra_ps=7_756_000,
    scheduler_hops_ps="identical in both runs: 112000 per DONE->DISPATCH hop, 224000 for task14 DONE->task15 DISPATCH",
    device_exit_run_time_delta_ps={"11": 280_000, "12": -224_000, "13": 7_672_000, "14": 28_000},
    explanation=("All 7.756 us is device-exit kernel run time inside task 15's window. Task 13 is the first "
                 "task cluster 1 runs when SDC is off; with SDC on, cluster 1 core 1 already ran S0/compare0/"
                 "S1/compare1. Device instruction traces are disabled, so the cold-cluster mechanism is a "
                 "consistent hypothesis, not a traced fact."))


def git(*args):
    return subprocess.run(["git", "-C", str(W), *args], check=True, capture_output=True, text=True).stdout


def s1b2_integrity(sealed):
    """S1b-2 for R8: no product change since r7, all staged images byte-identical."""
    assert sha(PRED) == PRED_SHA and sealed["release_reference"]["sha256"] == SDC_ON_SHA
    assert sealed["default_image_id"] == DEFAULT_IMAGE
    gate = sealed["image_gate"]
    assert sha(gate["path"]) == gate["sha256"]
    build = json.loads(Path(gate["path"]).read_text())
    assert build["status"] == "PASS" and len(build["images"]) == 18
    assert build["exact_comparison"]["byte_comparison"] and build["exact_comparison"]["legacy_images"] == 16
    parts = 0
    for row in build["images"]:
        staged = Path(row["output"])
        assert sha(staged / "test_cfg_unpatched.bin") == row["image_id"] == row["unpatched_sha256"]
        task, = staged.glob("task_*")
        for part, hashes in row["part_hashes"].items():
            actual = {str(p.relative_to(task / "bin" / part)): sha(p)
                      for p in (task / "bin" / part).rglob("*") if p.is_file()}
            assert actual == hashes, ("Staged image part changed", row["output"], part)
            parts += len(hashes)
    defaults = [row for row in build["images"] if not row.get("padded_dumps", False)]
    assert len(defaults) == 17 and all(row["default_byte_identical"] for row in defaults)
    default, = [row for row in defaults if row["mode"] == "recover"]
    assert default["image_id"] == DEFAULT_IMAGE
    assert sealed["image"]["image_id"] == build["images"][-1]["image_id"]
    # Only new tool files may differ from the r7 commit; no tracked edits.
    changed = git("diff", "--name-status", R7_COMMIT, "HEAD").split("\n")
    changed = [line.split("\t") for line in changed if line]
    assert all(status == "A" and path.startswith("tools/") and path.endswith(".py")
               for status, path in changed), changed
    assert git("diff", "--name-only") == "" and git("diff", "--cached", "--name-only", "--", ":!tools") == ""
    mismatched = [p for p, d in sealed["source_hashes"].items() if sha(W / p) != d]
    assert not mismatched, mismatched
    return dict(status="PASS", images_checked=18, image_part_files_checked=parts,
                default_image_id=DEFAULT_IMAGE, default_images_byte_identical=17,
                padded_image_id=sealed["image"]["image_id"], build_gate=gate,
                tracked_sources_checked=len(sealed["source_hashes"]),
                commits_since_r7=[line.split("\t")[1] for line in
                                  git("diff", "--name-status", R7_COMMIT, "HEAD").splitlines()],
                head=git("rev-parse", "HEAD").strip(), builds=0,
                note="No product/RTL/runtime source changed since r7, so no rebuild; images re-hashed.")


def preflight():
    sealed = s1b_release_r7.preflight()
    context = reference_context(sealed)
    bindings = {1: dict(sealed["release_reference"], sdc_enable=1)}
    r8.load_reference(bindings[1], context)
    configs = {}
    for name, scene in sealed["scenes"].items():
        sdc = r8.scene_configuration(scene, sealed["predictions"][name]["cfg"])
        configs[name] = r8.CONFIGURATIONS[sdc]
        if sdc == 1:
            r8.require_reference_tasks(r8.select_reference(bindings, scene, sealed["predictions"][name]["cfg"]),
                                       context, runner.planned_dispatch(sealed, name))
    if sorted(n for n, c in configs.items() if c == "sdc_off_recovery_off") != [
            "s1b_healthy_off", "s1b_stage0_primary_off"]:
        raise ValueError("SDC-off scene set changed: STOP_USER")
    off = set(runner.planned_dispatch(sealed, "s1b_healthy_off"))
    if off != set(runner.planned_dispatch(sealed, "s1b_stage0_primary_off")):
        raise ValueError("SDC-off scenes do not share one executing set: STOP_USER")
    return sealed, configs


def freeze_plan(sealed, configs, integrity):
    profile = E / "profile"
    profile.mkdir()
    plan = dict(
        schema=1, created=datetime.datetime.now().astimezone().isoformat(),
        decision=("Option c with per-configuration references: each scene is compared with the frozen "
                  "same-graph non-recovery reference of its own SDC configuration. Cap 1 us, zero warnings, "
                  "8700 us envelope, metric and every other gate unchanged. No task, including host exit, exempt."),
        diagnosis=DIAGNOSIS,
        predictions=dict(path=str(PRED), sha256=sha(PRED)),
        references=dict(
            sdc_on_recovery_off=dict(sealed["release_reference"], sdc_enable=1, source="r7, frozen 18:41 CEST, reused unchanged"),
            sdc_off_recovery_off=dict(capture_scene=runner.CAPTURE[0], source_configuration=runner.CAPTURE[1],
                                      freeze_dir=str(E / "system/reference_sdc_off"),
                                      rule="Captured first, frozen write-once with SHA256 before any scenario; "
                                           "recovery state read back as enabled=0 without deposits")),
        scene_reference=configs, scene_order=list(sealed["scenes"]),
        gates=dict(release_cap_ps=sealed["release_observation"]["threshold_ps"],
                   criterion=sealed["release_observation"]["criterion"],
                   healthy_envelope_us=sealed["budgets"]["normal_all_healthy_envelope"],
                   cache_warnings=0, questa_errors=0),
        s1b2_integrity=integrity,
        tools={str(W / "tools" / name): sha(W / "tools" / name) for name in TOOLS})
    path = profile / "plan.json"
    with path.open("x") as stream:
        stream.write(json.dumps(plan, indent=2) + "\n")
    hash_path = profile / "plan.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  plan.json\n")
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def main(check_only=False):
    sealed, configs = preflight()
    integrity = s1b2_integrity(sealed)
    if check_only:
        print(json.dumps(dict(status="PASS_OFFLINE_PREFLIGHT", simulations=0, scene_reference=configs,
                              sdc_on_L_ref=SDC_ON_SHA, s1b2=integrity["status"],
                              image_part_files=integrity["image_part_files_checked"])))
        return 0
    E.mkdir(exist_ok=True)  # May already hold the offline suite; subdirectories are exclusive.
    with (E / "s1b2_integrity.json").open("x") as stream:
        stream.write(json.dumps(integrity, indent=2) + "\n")
    plan = freeze_plan(sealed, configs, integrity)
    runner.E, runner.ROOT, runner.PRED = E / "system", RESULTS, PRED
    code = runner.main(sdc_on_binding=sealed["release_reference"], plan_binding=plan)
    result = json.loads((runner.E / "result.json").read_text())
    if code or result["status"] != "PASS" or result["runs"] != 7:
        print("STOP_USER", result.get("reason"))
        return 1
    descriptor = W / "evidence/chain_s1b_r7/descriptor_metrics/result.json"
    summary = metrics(sealed, runner.E, descriptor)
    write(E / "metrics.json", summary)
    print("S1b-4", summary["status"], summary["recovery_rate"], summary["false_positive_rate"],
          summary["unrecoverable_rate"])
    return int(summary["status"] != "PASS")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    raise SystemExit(main(parser.parse_args().check_only))
