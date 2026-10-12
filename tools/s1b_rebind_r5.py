"""Handoff only: reseal the rebuilt image/addresses and recovery-off reference plan."""
import datetime
import hashlib
import json
from pathlib import Path
import sys

W = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test")]
from s1b_freeze_r1 import sha, write
from s1b_reference_control import reference_prediction

E = W / "evidence/PredictionsS1bR5"


def main():
    expected = json.loads((W / "evidence/GateCS1bR1/changed_files_r2.json").read_text())
    assert all(sha(W / name) == checksum for name, checksum in expected.items()), "Handoff source changed"
    assert not E.exists() and not (W / "evidence/SystemS1bR5").exists()
    build = json.loads((W / "evidence/BuildS1bR3/progress.json").read_text())
    assert build["status"] == "PASS" and build["authorized_image_delta"]["legacy_images_identical"] == 16
    prep = json.loads((W / "evidence/PreparationS1bR5/progress.json").read_text())
    assert prep["status"] == "PASS_COMPILE_ONLY" and prep["actual_system_runs"] == 0
    assert prep["actual_ABI"] == dict(C=3, CL=2, T=4, IDW=12, DW=62)
    # Recompute the reviewed ELF/allocator/address/model freeze against the
    # rebuilt image. Old addresses and return PCs cannot be reused.
    path = W / "tools/s1b_freeze_r1.py"
    baseline = json.loads((W / "evidence/GateCS1bR1/before_r2.json").read_text())["files"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == baseline[str(path.relative_to(W))]
    source = path.read_text().replace('E = W / "evidence/PredictionsS1bR1"',
                                     'E = W / "evidence/PredictionsS1bR5"')
    for old, new in (("BuildS1bR2", "BuildS1bR3"), ("PreparationS1bR3", "PreparationS1bR5"),
                     ("SystemS1bR1", "SystemS1bR5")):
        source = source.replace(old, new)
    source = source.replace("s1b_readonly_observer_r2.sv", "s1b_readonly_observer_r4.sv")
    namespace = {"__file__": str(Path(__file__)), "__name__": "s1b_refreeze_handoff"}
    exec(compile(source, str(path), "exec"), namespace)
    namespace["main"]()
    fresh = json.loads((E / "predictions.json").read_text())
    prior_path = W / "evidence/PredictionsS1bR4/predictions.json"
    prior = json.loads(prior_path.read_text())
    for key in ("descriptors", "clocks", "cache", "budgets", "simulation_guards",
                "completion_word_to_sim_end_ps"):
        assert fresh[key] == prior[key], ("Unapproved gate/graph change", key)
    old_graph = dict(prior["graph"]); new_graph = dict(fresh["graph"])
    old_graph.pop("status_addresses"); new_graph.pop("status_addresses")
    assert old_graph == new_graph, "Graph changed"
    for name, scene in prior["scenes"].items():
        assert {k: v for k, v in scene.items() if k != "recovery_graph"} == {
            k: v for k, v in fresh["scenes"][name].items() if k != "recovery_graph"}, name
        assert prior["predictions"][name] == fresh["predictions"][name], name
    assert fresh["release_observation"]["method"] == prior["release_observation"]["method"]
    fresh["reference_prediction"] = reference_prediction(fresh)
    fresh["sealed"] = datetime.datetime.now().astimezone().isoformat()
    fresh["release_observation"].update(
        threshold_ps=1_000_000, criterion="max(0, latency_ps - L_ref_ps) <= threshold_ps",
        reference_scene="s1b_reference_recovery_off", reference_source_scene="s1b_healthy_on",
        reference_sdc_enable=1, reference_recovery_enable=0,
        reference_freeze="Exclusive read-only L_ref.json plus L_ref.sha256; pinned hash persisted before any scenario",
        missing_reference_task="STOP_USER", absolute_latency_reported=True,
        observer_abi=dict(C=3, CL=2, T=4, IDW=12, DW=62),
        user_decision="2026-10-11 CEST, DESIGN 9.26.12 and binding R2 decisions")
    for name in ("tools/s1b_readonly_observer_r4.sv", "tools/s1b_prepare_r5.py",
                 "tools/s1b_system_r5.py", "tools/s1b_reference_control.py", "tools/s1b_rebind_r5.py",
                 "tools/s1b_build_r3.py", "target/sim/automation/test/bingo_sdc_recover_reference.py"):
        fresh["bindings"][str(W / name)] = sha(W / name)
    write(E / "predictions.json", fresh)
    write(E / "preflight_proof.json", dict(status="PASS", prior=str(prior_path),
        prior_sha256=sha(prior_path), graph_and_seven_predictions_unchanged=True,
        budgets_and_readiness_unchanged=True, rebuilt_image_delta=build["authorized_image_delta"],
        addresses_and_resolver_return_PCs_recomputed=True, reference_not_yet_captured=True, simulations=0))
    write(E / "manifest.json", {str(p): sha(p) for p in E.iterdir()
                               if p.is_file() and p.name not in ("manifest.json", "result.json")})
    write(E / "result.json", dict(status="PASS_PREFLIGHT_ONLY", simulations=0,
        manifest_sha256=sha(E / "manifest.json"), predictions_sha256=sha(E / "predictions.json")))


if __name__ == "__main__":
    main()
