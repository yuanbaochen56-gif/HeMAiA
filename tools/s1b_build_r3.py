"""Handoff only: fresh generation/builds after the authorized dump initialization."""
import hashlib
import json
from pathlib import Path

W = Path(__file__).resolve().parents[1]
gate = W / "evidence/GateCS1bR1"
expected = json.loads((gate / "changed_files_r2.json").read_text())
for name, checksum in expected.items():
    assert hashlib.sha256((W / name).read_bytes()).hexdigest() == checksum, ("Handoff source changed", name)

# Retain the reviewed isolated-build, warning and sixteen legacy-image gates.
path = W / "tools/s1b_build_r1.py"
baseline = json.loads((gate / "before_r2.json").read_text())["files"]
assert hashlib.sha256(path.read_bytes()).hexdigest() == baseline[str(path.relative_to(W))]
source = path.read_text()
source = source.replace('E = W / "evidence/BuildS1bR1"', 'E = W / "evidence/BuildS1bR3"')
source = source.replace("warning_namespace = {}", "warning_namespace = {'sha': sha}")
source = source.replace('lower = E / "dependency-lower"\n            shutil.copytree(H13 / ".bender", lower, symlinks=True)',
                        'lower = W / "evidence/BuildS1bR1/dependency-lower"')
needle = '            state["status"] = "PASS"'
source = source.replace(needle, '''            prior = json.loads((W / "evidence/BuildS1bR2/progress.json").read_text())
            old, = [r for r in prior["images"] if r["mode"] == "recover"]
            new, = [r for r in state["images"] if r["mode"] == "recover"]
            old_generated = Path(old["output"]) / "generated"
            new_generated = Path(new["output"]) / "generated"
            old_header = (old_generated / "offload_bingo_hw.h").read_bytes()
            new_header = (new_generated / "offload_bingo_hw.h").read_bytes()
            assert old_header.count(b"char hex[129];") == 10
            assert new_header == old_header.replace(b"char hex[129];", b"char hex[129] = {0};")
            assert products(old_generated).keys() == products(new_generated).keys()
            assert all(sha(p) == sha(new_generated / p.name) for p in old_generated.iterdir()
                       if p.is_file() and p.name != "offload_bingo_hw.h")
            assert new["part_hashes"]["mempool"] == old["part_hashes"]["mempool"]
            state["authorized_image_delta"] = dict(generated_C="Only ten hex[129] zero initializers",
                graph_and_other_generated_files_identical=True, device_image_identical=True,
                legacy_images_identical=16, old_image_id=old["image_id"], new_image_id=new["image_id"],
                binary_changes="Host image rebuild from the sole authorized product source delta")
''' + needle)
exec(compile(source, str(Path(__file__)), "exec"),
     {"__file__": str(Path(__file__)), "__name__": "__main__"})
