"""Check observed ABI parameters at time zero and bind a corrected passive monitor."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import traceback
from s1b_prepare_r2 import sha, write

W = Path(__file__).resolve().parents[1]
E = W / "evidence/PreparationS1bR5"
H = E / "HeMAiA"
OBSERVER = W / "tools/s1b_readonly_observer_r4.sv"


def main():
    E.mkdir()
    previous = W / "evidence/PreparationS1bR1"
    prior = json.loads((previous / "progress.json").read_text())
    state = dict(status="RUNNING", pid=os.getpid(), commands=[], actual_system_runs=0,
                 source_hashes=prior["source_hashes"], correction="Internal WdTaskDescWidth62; legacy TaskDescWidth59 excludes wd_class")
    write(E / "progress.json", state)
    try:
        assert shutil.disk_usage(W).free >= int(10.5 * 1024**3)
        shutil.copytree(previous / "HeMAiA", H, symlinks=True)
        sim = H / "target/sim"
        manager = ("/testharness/i_dut/i_hemaia_0_0/i_occamy_chip/i_occamy/"
                   "i_occamy_soc/i_occamy_quad/i_occamy_quad_ctrl/i_bingo_hw_manager")
        memory = "/testharness/i_dut/i_hemaia_0_0/i_occamy_chip/i_hemaia_mem_system/i_hemaia_mem"
        commands = [
            ["vlog", "-sv", "-svinputport=compat", "-override_timescale", "1ns/1ps",
             "-work", str(sim / "work-vsim"), str(OBSERVER)],
            ["vopt", "-access=r+" + manager + "+2", "-access=rw+" + memory + "+4",
             "-work", str(sim / "work-vsim"), "testharness", "s1b_readonly_bind_r4",
             "-o", "testharness_opt"],
        ]
        for index, command in enumerate(commands):
            log = E / f"compile_{index}.log"
            with log.open("x") as stream:
                result = subprocess.run(command, cwd=sim, stdout=stream, stderr=subprocess.STDOUT,
                                        timeout=1800, start_new_session=True)
            state["commands"].append(dict(command=command, log=str(log), returncode=result.returncode))
            write(E / "progress.json", state)
            assert result.returncode == 0
        probe = E / "probe.tcl"
        old_probe = (W / "evidence/ElaborationS1bR1/check.tcl").read_text()
        check = ("foreach {name expected} {C 3 CL 2 T 4 IDW 12 DW 62} {\n"
                 '  set value [examine -radix unsigned "$manager/i_s1b_readonly_observer/$name"]\n'
                 '  echo "ACTUAL_ABI $name=$value"\n'
                 '  if {$value != $expected} {quit -f -code 1}\n}\n')
        probe.write_text(old_probe.replace('echo "ELABORATION_ONLY_PASS_NO_RUN"', check +
                                           'echo "ELABORATION_ONLY_PASS_NO_RUN"'))
        log = E / "elaboration.log"
        with log.open("x") as stream:
            result = subprocess.run(
                ["vsim", "-c", "-t", "1ps", "-warning", "8386", "-work", str(sim / "work-vsim"),
                 "testharness_opt", "+S1B_OBSERVATIONS=" + str(E / "unused_observations.txt"),
                 "-do", "source " + str(probe)], cwd=sim, stdout=stream,
                stderr=subprocess.STDOUT, timeout=180, start_new_session=True)
        state["commands"].append(dict(label="actual_ABI_no_run", log=str(log), returncode=result.returncode))
        assert result.returncode == 0 and "ELABORATION_ONLY_PASS_NO_RUN" in log.read_text()
        assert "Port size" not in log.read_text()
        launcher = sim / "bin/occamy_chip.vsim"
        shutil.copyfile(W / "evidence/PreparationS1bR3/HeMAiA/target/sim/bin/occamy_chip.vsim", launcher)
        launcher.chmod(0o700)
        state.update(status="PASS_COMPILE_ONLY", product_build_copy=str(H),
                     observer_sha256=sha(OBSERVER), launcher_sha256=sha(launcher),
                     no_product_sources_changed=True, actual_ABI=dict(C=3, CL=2, T=4, IDW=12, DW=62))
    except BaseException as error:
        state.update(status="STOP_USER", reason=repr(error), traceback=traceback.format_exc())
        write(E / "stop.json", state)
    state["finished"] = datetime.datetime.now().astimezone().isoformat()
    write(E / "progress.json", state)
    write(E / "manifest.json", {str(path): sha(path) for path in
        [*E.glob("*.log"), E / "probe.tcl", E / "progress.json", OBSERVER, Path(__file__)]})
    write(E / "result.json", dict(status=state["status"], manifest_sha256=sha(E / "manifest.json"),
                                 simulations=0, reason=state.get("reason")))
    print(state["status"], state.get("reason", ""), flush=True)
    return int(state["status"] != "PASS_COMPILE_ONLY")


if __name__ == "__main__":
    raise SystemExit(main())
