#!/usr/bin/env python3
r"""
Bingo HW manager watchdog / heartbeat system tests
==================================================
Single-chip RTL sims (Questa, no private vendor modules) that check the
bingo_hw_manager watchdog integration end to end:

======  =================================================  ==========================================
name    setup                                              pass criteria
======  =================================================  ==========================================
s1      hemaia_ci, dummy_2cluster, watchdog 100k cycles    EOC success, no [BINGO_WD]/[BINGO_REMAP]/
        (healthy run, tight timeout)                       [BINGO_CSR] event, no failed host check
s2      hemaia_singlechiplet_16MB_1cluster with the        same as s1 (covers the heartbeat in the
        non-SIMD VersaCore cluster, gemm_serial_dma_1-     VersaCore wait loops on core 0)
        cluster, watchdog 100k cycles
s3      as s1, but global task 0 (cluster 0 DM core)       EOC success, exactly dead_suspect=1 then
        stalls 1M core cycles without heartbeat            =0 on that core, no remap, no other event
s4      as s1, but global task 0 hangs forever             no EOC (bounded run), dead_suspect=1 on
                                                           that core only, never cleared, no remap
======  =================================================  ==========================================

HeMAiA configures the bingo manager with CoreRemapAllowMask = '0 (the cores of a
cluster are heterogeneous), so the watchdog only detects; nothing is remapped.

The watchdog events are printed by bingo_hw_manager_top in simulation
([BINGO_WD] / [BINGO_REMAP] / [BINGO_CSR]) and parsed from the task's
``bin/sim_run.log``. Each scenario rebuilds the device runtime library and the
device/host app from scratch, because a flags-only change does not retrigger
the SW build.

Prerequisites
-------------
* Questa on PATH: ``source ~micasusr/design/scripts/questasim_2025.2.rc`` and
  ``export MTI_VCO_MODE=64 QSIM_VCO_MODE=64``
* ``Bender.yml`` pins ``bingo_hw_manager`` to ``../bingo_hw_manager`` (mounted into
  the build container via ``--bingo-repo``).

Run it
------
    python3 3_start_bingo_watchdog_sim.py --scenario s1 s3 s4   # hemaia_ci build
    python3 3_start_bingo_watchdog_sim.py --scenario s2         # 1-cluster build

Results: ``<out-root>/<scenario>/result.md`` and the runner's task directory.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SCRIPT = Path(__file__).resolve()
_REPO_ROOT = _SCRIPT.parents[4]  # target/sim/automation/test -> repo root
sys.path.insert(0, str(_REPO_ROOT / "util" / "automation_scripts"))

from hemaia_sim_runner import (  # noqa: E402
    SIM_ERR_MARKER, SIM_OK_MARKER, HeMAiASimRunner, make_task, task_dir_name,
)

HEMAIA_CI_CFG = "target/rtl/cfg/hemaia_ci.hjson"
# The 1-cluster GEMM workloads need exactly one cluster, and they generate their
# golden data from snax_versacore_to_cluster.hjson. No checked-in single-chip cfg
# has that (hemaia_singlechiplet_16MB_1cluster uses the SIMD cluster;
# hemaia_tapeout_1c needs the private hemaia_d2d_link; hemaia_ci has two
# clusters), so s2 swaps the cluster of the 1-cluster single-chip cfg. The
# workload must keep its operands on the compute chip: gemm_double_buffer_1cluster
# and friends read A/B/golden D from the memory chiplet, which a single-chip cfg
# does not have.
ONE_CLUSTER_CFG = "target/rtl/cfg/hemaia_singlechiplet_16MB_1cluster.hjson"
VERSACORE_CLUSTER_SWAP = ("snax_versacore_to_simd_cluster", "snax_versacore_to_cluster")
TIGHT_TIMEOUT_CYCLES = 100_000

# In dummy_2cluster, global task 0 is the cluster-0 iDMA copy on the DM core,
# i.e. bingo slot (chip 0, core 1, cluster 0). Its host check depends on it.
FAULT_GID = 0
VICTIM = (0, 1, 0)  # (chip, core, cluster)

SCENARIOS: Dict[str, dict] = {
    "s1": dict(
        desc="healthy dummy_2cluster, tight watchdog timeout: no false positive",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        workload="dummy_2cluster", fault_stall_cycles=None,
        expect_eoc=True, sim_timeout_s=3600,
    ),
    "s2": dict(
        desc="healthy 1-cluster GEMM, tight watchdog timeout: VersaCore heartbeat path",
        cfg=ONE_CLUSTER_CFG, cluster_swap=VERSACORE_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        workload="gemm_serial_dma_1cluster", fault_stall_cycles=None,
        expect_eoc=True, sim_timeout_s=4 * 3600,
    ),
    # The stall counts core cycles, the watchdog quad-ctrl cycles. In the RTL sim
    # the core clock was measured ~2.2x faster than the quad-ctrl clock, so
    # stall for 10x the timeout to exceed it with a clear margin.
    "s3": dict(
        desc="task 0 stalls without heartbeat, then completes: dead_suspect set and cleared",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        workload="dummy_2cluster", fault_stall_cycles=10 * TIGHT_TIMEOUT_CYCLES,
        expect_eoc=True, sim_timeout_s=3600,
    ),
    "s4": dict(
        desc="task 0 hangs forever: dead_suspect detected, run cannot finish",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        workload="dummy_2cluster", fault_stall_cycles=0,
        expect_eoc=False, sim_timeout_s=900,
    ),
}

WD_RE = re.compile(r"\[BINGO_WD\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) dead_suspect=(\d)")
REMAP_RE = re.compile(r"\[BINGO_REMAP\].*")
CSR_RE = re.compile(r"\[BINGO_CSR\].*")
CHECK_RE = re.compile(r"Check \[([^\]]*)\]: (PASS|FAIL)")


def make_cfg(base_cfg: str, timeout_cycles: Optional[int],
             cluster_swap: Optional[Tuple[str, str]] = None) -> str:
    """Return a repo-relative cfg; write a patched copy if anything is overridden."""
    if timeout_cycles is None and cluster_swap is None:
        return base_cfg
    src = _REPO_ROOT / base_cfg
    new_text = src.read_text()
    suffix = ""
    if cluster_swap is not None:
        old, new = cluster_swap
        new_text, n = re.subn(rf'"{old}"', f'"{new}"', new_text)
        if n != 1:
            raise ValueError(f"expected exactly one cluster '{old}' in {base_cfg}, found {n}")
        suffix += "_" + new
    if timeout_cycles is not None:
        key = "bingo_watchdog_timeout_cycles"
        if key in new_text:
            raise ValueError(f"{base_cfg} already sets {key}")
        # s1_quadrant carries dep_tag_width; add the watchdog timeout next to it.
        new_text, n = re.subn(r"^(\s*)(dep_tag_width:[^\n]*)$",
                              rf"\1\2\n\1{key}: {timeout_cycles}", new_text, count=1, flags=re.M)
        if n != 1:
            raise ValueError(f"could not find dep_tag_width in {base_cfg}")
        suffix += f"_wd{timeout_cycles}"
    dst = _REPO_ROOT / "target/rtl/cfg/generated" / f"{src.stem}{suffix}.hjson"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(new_text)
    return str(dst.relative_to(_REPO_ROOT))


def clean_app_builds(workload: str) -> None:
    """Force a fresh SW build: objects and libraries do not depend on USER_FLAGS.

    The device runtime library matters too: the offload loop that actually runs
    (bingo_hw_offload_manager, a C99 inline function) is linked from
    libsnRuntime.a, not from the app's own translation unit.
    """
    for build_dir in (
        _REPO_ROOT / "target/sw/device/runtime/build",
        _REPO_ROOT / "target/sw/device/apps/snax/snax-bingo-offload/build",
        _REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads" / workload / "build",
    ):
        if build_dir.is_dir():
            shutil.rmtree(build_dir)


def evaluate(name: str, sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Return the list of failed expectations (empty = pass)."""
    problems: List[str] = []
    eoc_ok = SIM_OK_MARKER in log_text and SIM_ERR_MARKER not in log_text
    events = [tuple(int(x) for x in m.groups()) for m in WD_RE.finditer(log_text)]
    remaps = REMAP_RE.findall(log_text)
    csr = CSR_RE.findall(log_text)
    checks = CHECK_RE.findall(uart_text)

    if sc["expect_eoc"] and not eoc_ok:
        problems.append(f"expected '{SIM_OK_MARKER}' without errors")
    if not sc["expect_eoc"] and eoc_ok:
        problems.append("run finished although the victim task hangs forever")
    if remaps:
        problems.append(f"{len(remaps)} unexpected [BINGO_REMAP] event(s): {remaps[:3]}")
    if csr:
        problems.append(f"{len(csr)} [BINGO_CSR] unknown-address event(s): {csr[:3]}")
    failed_checks = [c for c in checks if c[1] == "FAIL"]
    if failed_checks:
        problems.append(f"host check(s) failed: {failed_checks}")
    if sc["expect_eoc"] and not checks:
        problems.append("no host 'Check [...]: PASS' line in the UART log")

    victim_events = [(e[4]) for e in events if (e[1], e[2], e[3]) == VICTIM]
    other_events = [e for e in events if (e[1], e[2], e[3]) != VICTIM]
    stall = sc["fault_stall_cycles"]
    if stall is None:
        if events:
            problems.append(f"unexpected [BINGO_WD] events in a healthy run: {events}")
    else:
        if other_events:
            problems.append(f"[BINGO_WD] events on non-victim cores: {other_events}")
        expected = [1, 0] if stall > 0 else [1]
        if victim_events != expected:
            problems.append(f"victim {VICTIM} dead_suspect sequence {victim_events}, expected {expected}")

    print(f"[{name}] eoc_ok={eoc_ok} wd_events={events} remaps={len(remaps)} "
          f"csr_unknown={len(csr)} checks={checks}")
    return problems


def run_scenario(name: str, args: argparse.Namespace) -> bool:
    sc = SCENARIOS[name]
    out_dir = Path(args.out_root) / name
    print(f"\n===== {name}: {sc['desc']} =====")
    cfg = make_cfg(sc["cfg"], sc["timeout_cycles"], sc.get("cluster_swap"))
    clean_app_builds(sc["workload"])

    task = make_task(host_app_type="offload_bingo_hw", chip_type="single_chip",
                     workload=sc["workload"], dev_app="snax-bingo-offload")
    if sc["fault_stall_cycles"] is not None:
        task["extra_user_flags"] = (f"-DBINGO_WD_FAULT_GID={FAULT_GID} "
                                    f"-DBINGO_WD_FAULT_STALL_CYCLES={sc['fault_stall_cycles']}")

    runner = HeMAiASimRunner(
        repo_root=_REPO_ROOT,
        output_dir=out_dir,
        engine="vsim",
        with_waveform=False,
        cfg=cfg,
        sim_cfg="target/sim/cfg/sim_rtl.hjson",
        with_macro=False,
        with_d2d=False,
        with_pll=False,
        max_jobs=1,
        skip_setup=True,          # never `make clean` (it would wipe .bender)
        build_sw_fleet=False,     # build only this workload
        fail_on_task_failure=False,
        timeout_seconds=sc["sim_timeout_s"],
        extra_mounts=[Path(args.bingo_repo)],
    )
    runner.run([task])

    bin_dir = out_dir / task_dir_name(0, task["ci_name"]) / "bin"
    log_path = bin_dir / "sim_run.log"
    uart_path = bin_dir / "uart_chip_0_0.log"
    log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
    uart_text = uart_path.read_text(errors="replace") if uart_path.exists() else ""
    problems = evaluate(name, sc, log_text, uart_text)
    if not log_text:
        problems.append(f"missing {log_path}")

    status = "PASS" if not problems else "FAIL"
    lines = [f"# {name}: {status}", "", f"- {sc['desc']}", f"- cfg: `{cfg}`",
             f"- workload: `{sc['workload']}`", f"- extra flags: `{task.get('extra_user_flags', '')}`",
             f"- log: `{log_path}`", ""]
    lines += [f"- problem: {p}" for p in problems] or ["- all expectations met"]
    lines += ["", "## [BINGO_*] lines", "```"]
    lines += [l for l in log_text.splitlines() if "[BINGO_" in l][:50]
    lines += ["```"]
    (out_dir / "result.md").write_text("\n".join(lines) + "\n")
    print(f"[{name}] {status}" + "".join(f"\n  - {p}" for p in problems))
    return not problems


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scenario", nargs="+", choices=sorted(SCENARIOS), default=["s1"],
                        help="scenario(s) to run, in order (default: %(default)s)")
    parser.add_argument("--out-root", default="/volume1/users/r1015708/hemaia_bingo_wd/system",
                        help="output root; each scenario gets its own subdirectory "
                             "(keep it off the quota-limited home directory)")
    parser.add_argument("--bingo-repo", default=str(_REPO_ROOT.parent / "bingo_hw_manager"),
                        help="bingo_hw_manager checkout used by the Bender path pin")
    args = parser.parse_args()

    if shutil.which("vsim") is None:
        sys.exit("vsim not found: source the Questa setup script first")

    results = {name: run_scenario(name, args) for name in args.scenario}
    print("\n===== summary =====")
    for name, ok in results.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}  ({SCENARIOS[name]['desc']})")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
