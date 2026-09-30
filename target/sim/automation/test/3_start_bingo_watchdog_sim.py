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
s5      as s4, plus confirm timeout 200k cycles            no EOC, that core dead_suspect then
        (fencing enabled)                                  fenced, [BINGO_REPLAY_STUCK], no replay,
                                                           no remap, no other event
s6      healthy int32_add_2plain_1cluster on one           EOC success, host check PASS, no
        snax_versacore_2plain_to_cluster, fencing on       [BINGO_*] event at all
s7      as s6, but the add on plain core 1 (global task    EOC success, check PASS; core 1
        2) hangs forever                                   fenced, task 2 replayed on plain core
                                                           2, core 1 retired, its later tasks
                                                           remapped to core 2 only
s8      as s7, but core 1 comes back after the fence,      as s7, plus the late done of core 1
        before the run ends (zombie)                       is dropped ([BINGO_FENCE])
s9      as s7, but core 1 is only slow: silent longer      EOC success, check PASS; core 1
        than the heartbeat timeout, shorter than the       dead_suspect set and cleared, no
        confirm timeout                                    fence, no replay, no remap
======  =================================================  ==========================================

The bingo manager may only move a dead core's tasks to a core of the same type
(CoreTypeId, generated from the cluster cfgs). In the snax_versacore_to_cluster
clusters of hemaia_ci no two cores of a cluster share a type, so the watchdog
only detects; nothing is remapped. With a confirm timeout (s5) the dead core is
fenced, but its tasks have no substitute: the manager reports replay_stuck and
replays nothing.

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
    python3 3_start_bingo_watchdog_sim.py --scenario s1 s3 s4 s5  # hemaia_ci build
    python3 3_start_bingo_watchdog_sim.py --scenario s2         # 1-cluster build
    python3 3_start_bingo_watchdog_sim.py --scenario s6 s7 s8 s9  # 1-cluster, 2 plain cores

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
# Same 1-cluster cfg with the HeMAiA cluster that has two plain compute cores
# (target/rtl/cfg/cluster/snax_versacore_2plain_to_cluster.hjson)
TWO_PLAIN_CLUSTER_SWAP = ("snax_versacore_to_simd_cluster", "snax_versacore_2plain_to_cluster")
TIGHT_TIMEOUT_CYCLES = 100_000
CONFIRM_TIMEOUT_CYCLES = 2 * TIGHT_TIMEOUT_CYCLES

# In dummy_2cluster, global task 0 is the cluster-0 iDMA copy on the DM core,
# i.e. bingo slot (chip 0, core 1, cluster 0). Its host check depends on it.
FAULT_GID = 0
VICTIM = (0, 1, 0)  # (chip, core, cluster)
# In int32_add_2plain_1cluster, global task 2 is the add on plain core 1; plain
# core 2 (same type) is the only core that may take over its tasks.
TWO_PLAIN_FAULT_GID = 2
TWO_PLAIN_VICTIM = (0, 1, 0)
TWO_PLAIN_SUBSTITUTE = 2

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
    "s5": dict(
        desc="task 0 hangs forever, fencing on: core fenced, no substitute (replay_stuck)",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        workload="dummy_2cluster", fault_stall_cycles=0,
        expect_eoc=False, expect_fence=True, sim_timeout_s=3600,
    ),
    "s6": dict(
        desc="healthy int32 add on two plain cores, fencing on: no false positive",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        workload="int32_add_2plain_1cluster", fault_stall_cycles=None,
        expect_eoc=True, sim_timeout_s=3600,
    ),
    "s7": dict(
        desc="plain core 1 hangs on its add: fenced, task replayed on plain core 2, run completes",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        workload="int32_add_2plain_1cluster", fault_stall_cycles=0,
        fault_gid=TWO_PLAIN_FAULT_GID, victim=TWO_PLAIN_VICTIM, substitute=TWO_PLAIN_SUBSTITUTE,
        expect_eoc=True, expect_fence=True, sim_timeout_s=3600,
    ),
    # The zombie must come back after the fence (confirm timeout: ~5.6 ms after the
    # dispatch at 28 ns per quad-ctrl cycle) but before the run ends (~4.8 ms after
    # the replay, measured in s6/s7). 600k core cycles at ~12.6 ns are ~7.6 ms.
    "s8": dict(
        desc="plain core 1 comes back after the fence (zombie): replayed on core 2, late done dropped",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        workload="int32_add_2plain_1cluster", fault_stall_cycles=3 * CONFIRM_TIMEOUT_CYCLES,
        fault_gid=TWO_PLAIN_FAULT_GID, victim=TWO_PLAIN_VICTIM, substitute=TWO_PLAIN_SUBSTITUTE,
        expect_eoc=True, expect_fence=True, expect_fence_drop=True, sim_timeout_s=3600,
    ),
    # Between the two timeouts: 330k core cycles are ~4.2 ms (~150k quad cycles).
    "s9": dict(
        desc="plain core 1 only slow (between the timeouts): no fence, no replay",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        workload="int32_add_2plain_1cluster", fault_stall_cycles=330_000,
        fault_gid=TWO_PLAIN_FAULT_GID, victim=TWO_PLAIN_VICTIM,
        expect_eoc=True, sim_timeout_s=3600,
    ),
}

# fenced= only exists since the replay support; older RTL prints dead_suspect only.
WD_RE = re.compile(r"\[BINGO_WD\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) dead_suspect=(\d)(?: fenced=(\d))?")
REMAP_RE = re.compile(r"\[BINGO_REMAP\].*")
REPLAY_RE = re.compile(r"\[BINGO_REPLAY\].*")
# Parsed forms of the remap / replay / retire / fence-drop lines
REMAP_FIELDS_RE = re.compile(r"\[BINGO_REMAP\] \d+ chip=(\d+) task=(\d+) logical_core=(\d+) -> physical_core=(\d+) cluster=(\d+)")
REPLAY_FIELDS_RE = re.compile(r"\[BINGO_REPLAY\] \d+ chip=(\d+) task=(\d+) type=\d+ logical_core=(\d+) from=(\d+) to=(\d+) cluster=(\d+)")
RETIRED_RE = re.compile(r"\[BINGO_RETIRED\] \d+ chip=(\d+) core=(\d+) cluster=(\d+)")
FENCE_DROP_RE = re.compile(r"\[BINGO_FENCE\] \d+ chip=(\d+) core=(\d+) cluster=(\d+) dropped done task=(\d+)")
STUCK_RE = re.compile(r"\[BINGO_REPLAY_STUCK\].*")
ASSERT_RE = re.compile(r"\[BINGO_ASSERT\].*")
CSR_RE = re.compile(r"\[BINGO_CSR\].*")
CHECK_RE = re.compile(r"Check \[([^\]]*)\]: (PASS|FAIL)")


def make_cfg(base_cfg: str, timeout_cycles: Optional[int],
             cluster_swap: Optional[Tuple[str, str]] = None,
             confirm_timeout_cycles: Optional[int] = None) -> str:
    """Return a repo-relative cfg; write a patched copy if anything is overridden."""
    if timeout_cycles is None and cluster_swap is None and confirm_timeout_cycles is None:
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
    if confirm_timeout_cycles is not None:
        key = "bingo_watchdog_confirm_timeout_cycles"
        if key in new_text:
            raise ValueError(f"{base_cfg} already sets {key}")
        new_text, n = re.subn(r"^(\s*)(dep_tag_width:[^\n]*)$",
                              rf"\1\2\n\1{key}: {confirm_timeout_cycles}", new_text, count=1, flags=re.M)
        if n != 1:
            raise ValueError(f"could not find dep_tag_width in {base_cfg}")
        suffix += f"_cf{confirm_timeout_cycles}"
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
    # (time, chip, core, cluster, dead_suspect, fenced)
    events = [tuple(int(x) if x is not None else 0 for x in m.groups()) for m in WD_RE.finditer(log_text)]
    remaps = REMAP_RE.findall(log_text)
    replays = REPLAY_RE.findall(log_text)
    stuck = STUCK_RE.findall(log_text)
    asserts = ASSERT_RE.findall(log_text)
    csr = CSR_RE.findall(log_text)
    checks = CHECK_RE.findall(uart_text)

    if sc["expect_eoc"] and not eoc_ok:
        problems.append(f"expected '{SIM_OK_MARKER}' without errors")
    if not sc["expect_eoc"] and eoc_ok:
        problems.append("run finished although the victim task hangs forever")
    victim = sc.get("victim", VICTIM)
    substitute = sc.get("substitute")
    if substitute is None:
        # No core may take over the victim's tasks: nothing moves
        if remaps:
            problems.append(f"{len(remaps)} unexpected [BINGO_REMAP] event(s): {remaps[:3]}")
        if replays:
            problems.append(f"{len(replays)} unexpected [BINGO_REPLAY] event(s): {replays[:3]}")
    else:
        # Everything the victim held or gets later moves to the substitute only
        chip, core, cluster = victim
        moved = [tuple(int(x) for x in m.groups()) for m in REPLAY_FIELDS_RE.finditer(log_text)]
        if not any(task == sc["fault_gid"] for _, task, _, _, _, _ in moved):
            problems.append(f"task {sc['fault_gid']} was not replayed: {replays[:3]}")
        wrong = [r for r in moved if (r[0], r[3], r[4], r[5]) != (chip, core, substitute, cluster)]
        if wrong or len(moved) != len(replays):
            problems.append(f"replay other than core {core} -> {substitute}: {replays[:3]}")
        routed = [tuple(int(x) for x in m.groups()) for m in REMAP_FIELDS_RE.finditer(log_text)]
        wrong = [r for r in routed if (r[0], r[2], r[3], r[4]) != (chip, core, substitute, cluster)]
        if wrong or len(routed) != len(remaps):
            problems.append(f"remap other than logical core {core} -> {substitute}: {remaps[:3]}")
        retired = [tuple(int(x) for x in m.groups()) for m in RETIRED_RE.finditer(log_text)]
        if retired != [victim]:
            problems.append(f"[BINGO_RETIRED] expected only {victim}, got {retired}")
    if sc.get("expect_fence_drop", False):
        drops = [tuple(int(x) for x in m.groups()) for m in FENCE_DROP_RE.finditer(log_text)]
        if drops != [(*victim, sc["fault_gid"])]:
            problems.append(f"[BINGO_FENCE] expected one dropped done of task {sc['fault_gid']} "
                            f"on {victim}, got {drops}")
    if asserts:
        problems.append(f"{len(asserts)} [BINGO_ASSERT] violation(s): {asserts[:3]}")
    expect_stuck = sc.get("expect_fence", False) and substitute is None
    if expect_stuck != bool(stuck):
        problems.append(f"[BINGO_REPLAY_STUCK] expected {expect_stuck}, got {stuck[:1]}")
    if csr:
        problems.append(f"{len(csr)} [BINGO_CSR] unknown-address event(s): {csr[:3]}")
    failed_checks = [c for c in checks if c[1] == "FAIL"]
    if failed_checks:
        problems.append(f"host check(s) failed: {failed_checks}")
    if sc["expect_eoc"] and not checks:
        problems.append("no host 'Check [...]: PASS' line in the UART log")

    victim_events = [(e[4], e[5]) for e in events if (e[1], e[2], e[3]) == victim]
    other_events = [e for e in events if (e[1], e[2], e[3]) != victim]
    stall = sc["fault_stall_cycles"]
    if stall is None:
        if events:
            problems.append(f"unexpected [BINGO_WD] events in a healthy run: {events}")
    else:
        if other_events:
            problems.append(f"[BINGO_WD] events on non-victim cores: {other_events}")
        # (dead_suspect, fenced) per [BINGO_WD] line of the victim
        if sc.get("expect_fence", False):
            expected = [(1, 0), (1, 1)]
        else:
            expected = [(1, 0), (0, 0)] if stall > 0 else [(1, 0)]
        if victim_events != expected:
            problems.append(f"victim {victim} (dead_suspect, fenced) sequence {victim_events}, expected {expected}")

    print(f"[{name}] eoc_ok={eoc_ok} wd_events={events} remaps={len(remaps)} "
          f"replays={len(replays)} csr_unknown={len(csr)} checks={checks}")
    return problems


def run_scenario(name: str, args: argparse.Namespace) -> bool:
    sc = SCENARIOS[name]
    out_dir = Path(args.out_root) / name
    print(f"\n===== {name}: {sc['desc']} =====")
    cfg = make_cfg(sc["cfg"], sc["timeout_cycles"], sc.get("cluster_swap"),
                   sc.get("confirm_timeout_cycles"))
    clean_app_builds(sc["workload"])

    task = make_task(host_app_type="offload_bingo_hw", chip_type="single_chip",
                     workload=sc["workload"], dev_app="snax-bingo-offload")
    if sc["fault_stall_cycles"] is not None:
        task["extra_user_flags"] = (f"-DBINGO_WD_FAULT_GID={sc.get('fault_gid', FAULT_GID)} "
                                    f"-DBINGO_WD_FAULT_STALL_CYCLES={sc['fault_stall_cycles']}")
        if sc.get("substitute") is not None:
            # The substitute gets the same task replayed: only the victim misbehaves
            _, core, cluster = sc["victim"]
            task["extra_user_flags"] += f" -DBINGO_WD_FAULT_CLUSTER={cluster} -DBINGO_WD_FAULT_CORE={core}"

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
