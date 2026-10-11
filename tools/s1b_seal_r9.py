"""Hash-only seal of evidence/GateS1bR9, results/GateS1bR9 and every reused input.

Run after RESULT.md is written; manifest.json and manifest.sha256 are the only
files excluded. Reused R8 runs and frozen references are bound by hash.
"""
import json
from pathlib import Path
import subprocess

from s1b_system_r1 import sha

W = Path(__file__).resolve().parents[1]
E = W / "evidence/GateS1bR9"
RESULTS = W / "results/GateS1bR9"


def status():
    path = E / "metrics.json"
    if not path.exists():
        return "STOP_USER"
    summary = json.loads(path.read_text())
    return "DONE" if summary["status"] == "PASS" and not summary["stop_conditions"] else "STOP_USER"


def main():
    if (E / "manifest.json").exists():
        raise SystemExit("R9 already sealed")
    files = {}
    for root in (E, RESULTS):
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.parent == E and path.name in ("manifest.json", "manifest.sha256"):
                continue
            if path.is_file():
                files[str(path)] = sha(path)
    plan = E / "profile/plan.json"
    if plan.exists():
        value = json.loads(plan.read_text())
        files.update(value["reused_inputs"])
        files.update(value["tools"])
        files[value["predictions"]["path"]] = value["predictions"]["sha256"]
        for binding in value["references"].values():
            files[binding["path"]] = binding["sha256"]
            files[binding["hash_path"]] = sha(binding["hash_path"])
    for path, digest in files.items():
        assert sha(path) == digest, ("Changed while sealing", path)
    head = subprocess.run(["git", "-C", str(W), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()
    manifest = E / "manifest.json"
    with manifest.open("x") as stream:
        stream.write(json.dumps(dict(status=status(), head=head, files=dict(sorted(files.items()))),
                                indent=2) + "\n")
    digest = sha(manifest)
    with (E / "manifest.sha256").open("x") as stream:
        stream.write(digest + "  manifest.json\n")
    print(status(), len(files), "files", digest)


if __name__ == "__main__":
    main()
