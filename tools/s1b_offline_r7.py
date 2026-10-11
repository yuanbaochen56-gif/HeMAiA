"""Run offline Python/Tcl suites while excluding builds, simulator launches and commits."""
import ast
import argparse
import contextlib
import datetime
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

W = Path(__file__).resolve().parents[1]
E = W / "evidence/chain_s1b_r7"
COMPILER = W / "target/sw/host/runtime/libbingo/mini_compiler"
SNITCH = W / "evidence/PreparationS1bR1/HeMAiA/.bender/git/checkouts/snitch_cluster-9a64e6b3572c7a8d"
FORBIDDEN = {"cc", "gcc", "clang", "make", "ninja", "bender", "vsim", "vlog", "vopt",
             "verilator", "podman", "docker"}


def build_dependencies(module):
    """Find test methods calling build/commit helpers, without executing them."""
    source = Path(module.__file__)
    tree = ast.parse(source.read_text())
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    dependencies = {}
    bad = set()
    for node in functions:
        literals = {n.value for n in ast.walk(node) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        # Simulator runner tests often contain 'vsim' as a mocked engine name;
        # runtime guarding below prevents an actual simulator call.
        if (literals & {"cc", "gcc", "clang", "make", "commit"}
                or ("git" in literals and literals & {"init", "add", "reset", "checkout", "merge", "push"})):
            bad.add(node.name)
        dependencies[node.name] = {n.func.id if isinstance(n.func, ast.Name) else n.func.attr
            for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}
    while True:
        more = {name for name, calls in dependencies.items() if calls & bad} - bad
        if not more:
            return bad
        bad |= more


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main(output=None):
    global E
    if output is not None:
        E = Path(output).resolve()
        if W / "evidence/chain_s1b_r7" not in E.parents:
            raise ValueError("Offline retries must use a new owned R7 evidence directory")
        E.mkdir()
    os.environ.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", MPLBACKEND="Agg",
                      MPLCONFIGDIR=str(E / "mpl_cache"))
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test"),
        str(W / "util/automation_scripts"), str(COMPILER),
        str(W.parent / "c4_droid_20261005/tools"), str(SNITCH / "util/clustergen"), str(W / "util")]
    original_popen = subprocess.Popen
    calls = []

    class OfflinePopen(original_popen):
        def __init__(self, args, *positional, **kwargs):
            if isinstance(args, (str, bytes)) or kwargs.get("shell"):
                raise RuntimeError("Shell process forbidden by Phase A offline test guard")
            executable = Path(str(args[0])).name
            if executable in FORBIDDEN or (executable == "git" and
                    any(str(a) in {"commit", "init", "add", "amend", "reset", "checkout", "merge", "push"} for a in args[1:])):
                raise RuntimeError("Phase A build/simulation/git mutation forbidden: " + executable)
            calls.append(list(map(str, args)))
            super().__init__(args, *positional, **kwargs)

    subprocess.Popen = OfflinePopen
    rows = []
    roots = [("tools", W / "tools", "test_s1b_*.py"),
             ("recovery_checker_and_driver", W / "target/sim/automation/test", "test*.py"),
             ("mini_compiler", COMPILER / "tests", "test*.py")]
    try:
        for name, root, pattern in roots:
            suite = unittest.TestLoader().discover(str(root), pattern=pattern)
            selected, deferred, cache = [], [], {}
            for test in flatten(suite):
                module_name = test.__class__.__module__
                if module_name not in cache:
                    cache[module_name] = build_dependencies(importlib.import_module(module_name))
                bad = cache[module_name]
                method = test._testMethodName
                if method in bad or bad & {"setUp", "setUpClass", "tearDown", "tearDownClass"}:
                    deferred.append(dict(test=test.id(), reason="Build/make or git commit is outside Phase A"))
                else:
                    selected.append(test)
            log = E / (name + "_offline.log")
            with log.open("x") as stream:
                with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
                    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.TestSuite(selected))
            rows.append(dict(name=name, status="PASS" if result.wasSuccessful() else "FAIL",
                tests_run=result.testsRun, skipped=len(result.skipped), phase_b_deferred=deferred,
                log=str(log), failures=len(result.failures), errors=len(result.errors)))
            print(name, rows[-1]["status"], result.testsRun, "tests;", len(deferred), "deferred", flush=True)
            if not result.wasSuccessful():
                print(log.read_text()[-6000:], flush=True)
                break
    finally:
        subprocess.Popen = original_popen
    from s1b_system_r1 import sha
    for row in rows:
        row["log_sha256"] = sha(row["log"])
    path = E / "offline_tests.json"
    with path.open("x") as stream:
        json.dump(dict(status="PASS_OFFLINE_ONLY" if all(r["status"] == "PASS" for r in rows)
                       and len(rows) == len(roots) else "FAIL", suites=rows,
            tests_run=sum(r["tests_run"] for r in rows),
            processes_started=calls, builds=0, simulations=0, git_commits=0,
            completed=datetime.datetime.now().astimezone().isoformat()), stream, indent=2)
        stream.write("\n")
    return int(len(rows) != len(roots) or any(r["status"] != "PASS" for r in rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    raise SystemExit(main(parser.parse_args().output))
