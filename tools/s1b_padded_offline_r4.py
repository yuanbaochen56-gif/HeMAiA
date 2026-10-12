"""Run every R2 offline suite plus padded-profile contracts in fresh R4 paths."""
import datetime
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import s1b_offline_r2 as prior

W = Path(__file__).resolve().parents[1]
E = W / "evidence/PaddedS1bR4"
R = E / "offline"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    rerun = sys.argv[1:]
    if not rerun:
        R.mkdir()
        (R / "fixtures").mkdir()
        (R / "tmp").mkdir()
    inputs = {}
    for path in (W / "evidence/GateCS1bR1/r2/fixtures").glob("*.py"):
        inputs[str(path)] = sha(path)
        source = path.read_text().replace("evidence/GateCS1bR1/r2", "evidence/PaddedS1bR4/offline")
        if path.name == "s1b_graph_implementation_r3.py":
            source = source.replace("offline/product_generation", "offline/padded_generation")
        if not rerun:
            (R / "fixtures" / path.name).write_text(source)
    for package in ("networkx", "matplotlib", "numpy", "hjson", "jsonref"):
        assert importlib.util.find_spec(package) is not None, package
    assert importlib.metadata.version("jsonref") == "1.1.0"
    prior.R, prior.E = R, E
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0",
               MPLCONFIGDIR=str(R / "mpl_cache"), MPLBACKEND="Agg", TMPDIR=str(R / "tmp"))
    env["PYTHONPATH"] = os.pathsep.join(map(str, [R / "fixtures", W / "tools",
        W / "target/sim/automation/test", W / "util/automation_scripts", prior.COMPILER,
        prior.C4, prior.SNITCH / "util/clustergen", W / "util"]))
    generate = [sys.executable, "-B", str(prior.WORKLOAD / "main_bingo.py"),
                "-c", str(prior.WORKLOAD / "params.hjson"), "--platformcfg", str(prior.PLATFORM)]
    suites = [
        ("product_generation", generate + ["--output_dir", str(R / "product_generation/generated")]),
        ("padded_generation", generate + ["--padded-dumps", "--output_dir", str(R / "padded_generation/generated")]),
        ("compiler", [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(prior.COMPILER / "tests"), "-v"]),
        ("checker_driver", [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(W / "target/sim/automation/test"), "-v"]),
        ("observer_reference", [sys.executable, "-B", "-m", "unittest", "discover", "-s", str(W / "tools"), "-p", "test_s1b_*.py", "-v"]),
        ("occamygen", []),
        ("expression_fixture", [sys.executable, "-B", str(R / "fixtures/s1b_fixture_r6.py")]),
        ("model_contracts", [sys.executable, "-B", str(R / "fixtures/s1b_tests_r6.py")]),
        ("actual_graph_byte_oracle", [sys.executable, "-B", str(R / "fixtures/s1b_graph_implementation_r3.py")]),
        ("native_C_semantic_mutants", [sys.executable, "-B", str(R / "fixtures/s1b_mutants_r1.py")]),
        ("standard_library_byte_schedule", [sys.executable, "-S", "-B", str(R / "fixtures/s1b_stdlib_r6.py")]),
    ]
    rows = json.loads((E / "offline_suites.json").read_text()) if rerun else []
    if rerun:
        assert set(rerun) <= {name for name, _ in suites}
        suites = [(name, command) for name, command in suites if name in rerun]
    for name, command in suites:
        suite_env = env
        if name == "occamygen":
            # A rerun must not clobber the first owned checkout.
            checkout = R / "occamy_checkout"
            saved_root = prior.R
            if checkout.exists():
                rerun_root = R / "occamy_checkout_rerun"
                rerun_root.mkdir(exist_ok=True)
                prior.R = rerun_root
            root, suite_env = prior.occamy_environment(env)
            prior.R = saved_root
            command = ["/usr/bin/python3.12", "-B", "-m", "unittest", "discover", "-s", str(root / "util/occamygen/tests"), "-v"]
        log = E / (name + ".log")
        attempt = 2
        while log.exists():
            log = E / (name + f"_attempt_{attempt}.log")
            attempt += 1
        started = datetime.datetime.now().astimezone().isoformat()
        print("RUN", name, flush=True)
        with log.open("x") as stream:
            # Offline suites are finite. Do not send signals on a wall timeout.
            result = subprocess.run(command, cwd=W, env=suite_env, stdout=stream, stderr=subprocess.STDOUT)
        data = log.read_text()
        match = re.search(r"Ran (\d+) tests?", data)
        skipped = re.search(r"OK \(skipped=(\d+)\)", data)
        row = dict(name=name, status="PASS" if result.returncode == 0 else "FAIL",
                   command=command, returncode=result.returncode, started=started,
                   log=str(log), log_sha256=sha(log))
        if match:
            row.update(tests_run=int(match[1]), skipped=int(skipped[1]) if skipped else 0)
        previous = [r for r in rows if r["name"] == name]
        if previous:
            row["previous_attempts"] = previous
        rows = [r for r in rows if r["name"] != name] + [row]
        (E / "offline_suites.json").write_text(json.dumps(rows, indent=2) + "\n")
        print(name, row["status"], row.get("tests_run", ""), flush=True)
        if result.returncode:
            print(data[-5000:], flush=True)
    (E / "offline_inputs.json").write_text(json.dumps(dict(fixtures=inputs,
        driver_sha256=sha(__file__), prior_driver_sha256=sha(prior.__file__)), indent=2) + "\n")
    return int(any(row["status"] != "PASS" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
