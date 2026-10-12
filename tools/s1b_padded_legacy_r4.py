"""Fresh exact replay of all 74 legacy generation cases; no container or signals."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from s1b_build_r1 import sha, write, products, source_hashes

W = Path(__file__).resolve().parents[1]
E = W / "evidence/PaddedS1bR4/legacy_generation"
OLD = W.parent / "s1_20261010"
H = OLD / "HeMAiA"


def main():
    E.mkdir()
    reference = OLD / "evidence/GenerationS1R5/result.json"
    prior = json.loads(reference.read_text())
    assert prior["status"] == "PASS" and len(prior["runs"]) == 74
    snapshot = source_hashes()
    state = dict(status="RUNNING", started=datetime.datetime.now().astimezone().isoformat(),
                 reference=str(reference), reference_sha256=sha(reference), source_hashes=snapshot, runs=[])
    executor_source = W.parent / "p7b_20261009/tools/p7b_generate.py"
    executor = E / "generate.py"
    shutil.copyfile(executor_source, executor)
    state["executor_provenance"] = dict(path=str(executor_source), sha256=sha(executor_source))
    live = {str(path.parent.relative_to(W / "target/sw/host/apps/offload_bingo_hw"))
            for path in (W / "target/sw/host/apps/offload_bingo_hw").glob("*/workloads/*/main_bingo.py")}
    live -= {"single_chip/workloads/dma_chain_sdc_2cluster", "single_chip/workloads/dma_chain_sdc_recover_2cluster"}
    assert live == {row["workload"] for row in prior["runs"]} and len(live) == 73
    try:
        for row in prior["runs"]:
            assert source_hashes() == snapshot
            assert shutil.disk_usage(W).free >= 10 * 1024**3
            old = row["runs"][-1]
            directory = E / Path(row["workload"]).name / row["mode"]
            directory.mkdir(parents=True)
            argv = [arg.replace(str(H), str(W)) for arg in old["execution"]["argv"]]
            argv = [arg.replace(str(W / ".bender"), str(H / ".bender")) for arg in argv]
            resolved = directory / "resolved.json"
            write(resolved, dict(recipe=["python3", *argv]))
            output = directory / "generated"
            log = directory / "generation.log"
            command = [sys.executable, "-B", str(executor), str(W), str(resolved), str(output),
                       str(W / "target/sw/host/runtime/libbingo/mini_compiler/bingo_dfg.py")]
            started = time.monotonic()
            with log.open("x") as stream:
                result = subprocess.run(command, cwd=directory, stdout=stream, stderr=subprocess.STDOUT,
                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0",
                             MPLCONFIGDIR=str(directory / "mpl-cache"), TMPDIR=str(directory)))
            assert time.monotonic() - started <= 120, "Generation wall guard"
            actual = {name: digest for name, digest in products(output).items() if not name.startswith("mpl-cache/")}
            execution = json.loads((output / "execution.json").read_text())
            assert result.returncode == old["returncode"] and actual == old["products"], (row["workload"], row["mode"])
            if result.returncode:
                assert all(execution[key] == old["execution"][key] for key in ("exception_type", "message"))
            state["runs"].append(dict(workload=row["workload"], mode=row["mode"], status="IDENTICAL",
                returncode=result.returncode, products=actual, log=str(log), log_sha256=sha(log)))
            write(E / "progress.json", state)
            print(row["workload"], row["mode"], "IDENTICAL", flush=True)
        assert source_hashes() == snapshot
        state["status"] = "PASS"
    except Exception as error:
        state.update(status="STOP_USER", reason=repr(error))
    write(E / "progress.json", state)
    write(E / "result.json", dict(status=state["status"], runs=len(state["runs"]), reason=state.get("reason")))
    return int(state["status"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
