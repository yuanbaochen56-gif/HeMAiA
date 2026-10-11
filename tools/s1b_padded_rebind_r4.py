"""Bind a freshly built padded image and recompute all physical reference addresses."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

W = Path(__file__).resolve().parents[1]
E = W / "evidence/PaddedS1bR4"
sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test")]
from s1b_freeze_r1 import sha, write
from s1b_padded_build_r4 import replace_once
from s1b_reference_control import reference_prediction

PROFILE = dict(name="s1b_padded_dumps_r4", default=False,
               make_option="S1B_PADDED_DUMPS=1", generator_option="--padded-dumps",
               hex_buffers=10, hex_bytes=136, alignment_bytes=8,
               zero_initialized_bytes=136, terminator_index=128)


def fingerprint(sealed):
    value = dict(profile=sealed["padded_profile"], source_hashes=sealed["source_hashes"],
                 image_id=sealed["image"]["image_id"],
                 image_sha256=sealed["image"]["unpatched_sha256"],
                 parts=sealed["image"]["part_hashes"], bindings=sealed["bindings"])
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def validate_profile(sealed):
    if sealed.get("padded_profile") != PROFILE:
        raise ValueError("Padded reference requires the explicit R4 padded dump profile")
    row = sealed["image"]
    if row.get("padded_dumps") is not True or row["image_id"] == sealed["default_image_id"]:
        raise ValueError("Default or unselected image cannot capture the padded reference")
    if sealed.get("profile_fingerprint_sha256") != fingerprint(sealed):
        raise ValueError("Padded image/source fingerprint mismatch")
    image_path = Path(row["output"]) / "test_cfg_unpatched.bin"
    header = Path(row["output"]) / "generated/offload_bingo_hw.h"
    if (row["image_id"] != row["unpatched_sha256"]
            or sealed["bindings"].get(str(image_path)) != row["image_id"]
            or sha(image_path) != row["image_id"]):
        raise ValueError("Padded image bytes do not match the selected image ID")
    if str(header) not in sealed["bindings"]:
        raise ValueError("Padded generated header is not bound")
    text = header.read_text()
    if (text.count("char hex[136] __attribute__((aligned(8))) = {0};") != 10
            or re.findall(r"char hex\[(\d+)\]", text) != ["136"] * 10):
        raise ValueError("Padded header does not initialize all ten complete dump objects")
    for path, checksum in sealed["bindings"].items():
        if sha(path) != checksum:
            raise ValueError("Padded binding changed: " + path)
    for path, checksum in sealed["source_hashes"].items():
        if sha(W / path) != checksum:
            raise ValueError("Padded source changed: " + path)


def main(build_path):
    build_path = Path(build_path).resolve()
    build = json.loads(build_path.read_text())
    assert build["status"] == "PASS" and len(build["images"]) == 18
    assert build["exact_comparison"]["byte_comparison"] is True
    assert all(row["default_byte_identical"] for row in build["images"][:-1])
    padded, = [r for r in build["images"] if r.get("padded_dumps")]
    default, = [r for r in build["images"] if r["mode"] == "recover" and not r.get("padded_dumps")]
    assert padded["device_byte_identical"] and padded["part_hashes"]["mempool"] == default["part_hashes"]["mempool"]
    assert not (E / "system").exists() and not (W / "results/PaddedS1bR4").exists()
    path = W / "tools/s1b_freeze_r1.py"
    before = json.loads((E / "before.json").read_text())
    assert sha(path) == before["sources"][str(path.relative_to(W))]
    source = replace_once(path.read_text(), 'E = W / "evidence/PredictionsS1bR1"',
                          'E = W / "evidence/PaddedS1bR4/profile"')
    source = replace_once(source, 'build_path = W / "evidence/BuildS1bR2/progress.json"',
                          f'build_path = Path({str(build_path)!r})')
    source = replace_once(source, 'len(build["images"]) == 17', 'len(build["images"]) == 18')
    source = replace_once(source, 'all(row["byte_identical"] and row["parts_byte_identical"] for row in build["images"][:-1])',
                          'all(row["default_byte_identical"] for row in build["images"][:-1])')
    source = replace_once(source, 'if row["mode"] == "recover"]', 'if row.get("padded_dumps")]')
    source = source.replace("PreparationS1bR3", "PreparationS1bR5").replace(
        "evidence/SystemS1bR1", "evidence/PaddedS1bR4/system").replace(
        "s1b_readonly_observer_r2.sv", "s1b_readonly_observer_r4.sv")
    namespace = {"__file__": str(Path(__file__)), "__name__": "s1b_padded_freeze_recipe"}
    exec(compile(source, str(path), "exec"), namespace)
    namespace["main"]()
    profile_dir = E / "profile"
    fresh = json.loads((profile_dir / "predictions.json").read_text())
    prior_path = W / "evidence/PredictionsS1bR5/predictions.json"
    prior = json.loads(prior_path.read_text())
    for key in ("descriptors", "clocks", "cache", "budgets", "simulation_guards", "completion_word_to_sim_end_ps", "predictions"):
        assert fresh[key] == prior[key], ("Unapproved gate change", key)
    for name, scene in prior["scenes"].items():
        assert {k: v for k, v in scene.items() if k != "recovery_graph"} == {
            k: v for k, v in fresh["scenes"][name].items() if k != "recovery_graph"}, name
    assert {k: v for k, v in fresh["graph"].items() if k != "status_addresses"} == {
        k: v for k, v in prior["graph"].items() if k != "status_addresses"}
    assert fresh["release_observation"]["method"] == prior["release_observation"]["method"]
    fresh["release_observation"] = dict(prior["release_observation"])
    fresh["reference_prediction"] = reference_prediction(fresh)
    fresh.update(padded_profile=PROFILE, default_image_id=default["image_id"],
                 sealed=datetime.datetime.now().astimezone().isoformat(),
                 user_decision="2026-10-11 16:33 CEST option 2: off-by-default padded configuration")
    for filename in ("s1b_padded_build_r4.py", "s1b_padded_rebind_r4.py", "s1b_padded_reference_r4.py",
                     "s1b_system_r5.py", "s1b_reference_control.py", "s1b_readonly_observer_r4.sv"):
        fresh["bindings"][str(W / "tools" / filename)] = sha(W / "tools" / filename)
    output = Path(padded["output"])
    task, = output.glob("task_*")
    for part in padded["part_hashes"]:
        for filename in padded["part_hashes"][part]:
            p = task / "bin" / part / filename
            fresh["bindings"][str(p)] = sha(p)
    for p in (build_path, output / "generated/offload_bingo_hw.h",
              W / "target/sim/automation/test/bingo_sdc_recover_reference.py"):
        fresh["bindings"][str(p)] = sha(p)
    fresh["profile_fingerprint_sha256"] = fingerprint(fresh)
    validate_profile(fresh)
    write(profile_dir / "predictions.json", fresh)
    write(profile_dir / "preflight_proof.json", dict(status="PASS_PREFLIGHT_ONLY",
        prior=str(prior_path), prior_sha256=sha(prior_path),
        graph_and_seven_predictions_unchanged=True, gates_unchanged=True,
        addresses_and_resolver_return_PCs_recomputed=True,
        profile_fingerprint_sha256=fresh["profile_fingerprint_sha256"],
        reference_not_yet_captured=True, simulations=0))
    write(profile_dir / "manifest.json", {str(p): sha(p) for p in profile_dir.iterdir()
        if p.is_file() and p.name not in ("manifest.json", "result.json")})
    write(profile_dir / "result.json", dict(status="PASS_PREFLIGHT_ONLY", simulations=0,
        predictions_sha256=sha(profile_dir / "predictions.json"),
        manifest_sha256=sha(profile_dir / "manifest.json")))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--build-progress", type=Path, required=True)
    main(parser.parse_args().build_progress)
