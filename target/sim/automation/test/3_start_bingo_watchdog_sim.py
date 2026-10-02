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
s10     as s7, but level 3 forced through a loopback      EOC success, check PASS; core 1 fenced
        remote link: substitute mask 4 (remote only),      and retired, its tasks exported (proxy
        import mask 1, plain type -> own chip              slot 1), sent as AXI-Lite packets to
                                                           {chip, 0x0a002000} through the quad
                                                           xbar, imported on plain core 2, dones
                                                           back via {chip, 0x0a003000}; packets
                                                           decoded and checked, no link error
s11     as s6, but the DM core (global task 0) hangs, with no EOC; DM core fenced, [BINGO_REPLAY_STUCK],
        level 3 on (mask 5) and a remote target for the    nothing exported, imported or sent over
        plain type only                                    the remote link (its type has no target)
s13     as s10, but the import may only use its home   no EOC; core 1 fenced, task 2 exported,
        slot (import mask 0), which is the dead core 1:    the import rejected back over the link
        nothing can run the task                           (REJECT packet on the done page), the
                                                           proxy stuck (replay_stuck=1), its exit
                                                           task never exported, no link error
s14     as s7, with the recovery boost (host sets the      as s7, plus the cluster domain goes to
        boost power level to 3, faster than the normal     level 3 while core 2 runs core 1's
        level 6)                                           tasks ([BINGO_PM]); EOC compared to s7
s12     as s5, but with level 2 (substitute mask 3):       EOC success, both host checks PASS; the
        the cluster-0 DM core hangs on task 0 and the      victim is fenced, task 0 replayed on the
        cluster-1 DM core (same type) takes over           cluster-1 DM core, the victim retired,
                                                           its exit task remapped there as well
s16     moe2_2cluster, top-1, level 1 only; expert 0       one CERF g0 -> g1 event, expert 1 runs,
        accelerator hangs, CERF fallback enabled           join and EOC complete, expert 1 golden PASS
s17     same workload/table as s16, without a fault       expert 0 golden PASS, expert 1 skipped,
                                                           no watchdog or CERF fallback event
s18     dma_chain_2cluster, level 2: the cluster-0 DM      three late beats (precursors off), task 3
        core is late on tasks 0-2, then hangs on task 3    fenced and replayed on the cluster-1 DM
                                                           core, run completes (baseline for s19)
s19     as s18, precursors on (two late beats: park)       cluster-0 DM core at risk after task 1
                                                           starts, tasks 2-5 run on cluster 1,
                                                           nothing fenced or replayed, run completes
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
    python3 3_start_bingo_watchdog_sim.py --scenario s10 s11 s13  # same, level 3 (remote link)
    python3 3_start_bingo_watchdog_sim.py --scenario s12          # hemaia_ci, level 2 (cross-cluster)

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
# Level-3 loopback (s10): bingo core type of the plain cores in
# snax_versacore_2plain_to_cluster (generated BingoCoreTypeId: core 0 -> 1,
# plain cores 1, 2 -> 2, DM core 3 -> 3) and the chip id of the single-chip sim.
TWO_PLAIN_CORE_TYPE = 2
LOOPBACK_CHIP = 0
REMOTE_LINK_BASE = 0x0A002000  # BingoRemoteLinkBaseAddr: dispatch page, done page +0x1000
LOOPBACK_CFG_KEYS = {
    "bingo_substitute_level_mask": "4",         # level 3 only: force remote
    "bingo_import_substitute_level_mask": "1",  # an import may use a same-cluster core
    "bingo_remote_target_chip": f'{{"{TWO_PLAIN_CORE_TYPE}": {LOOPBACK_CHIP}}}',
}
# s11: levels 1 and 3, a remote target for the plain type only. The DM core has
# no same-type core in the cluster and its type has no target, so it is stuck.
NO_TARGET_CFG_KEYS = {
    "bingo_substitute_level_mask": "5",
    "bingo_remote_target_chip": f'{{"{TWO_PLAIN_CORE_TYPE}": {LOOPBACK_CHIP}}}',
}
# s13: as s10, but an import may only use its home slot, the dead core 1: the
# loopback import is rejected and the proxy becomes stuck.
REJECT_CFG_KEYS = {
    "bingo_substitute_level_mask": "4",
    "bingo_import_substitute_level_mask": "0",
    "bingo_remote_target_chip": f'{{"{TWO_PLAIN_CORE_TYPE}": {LOOPBACK_CHIP}}}',
}
# s12: levels 1 and 2. The DM cores of the two hemaia_ci clusters share a type,
# so the cluster-1 DM core may run the cluster-0 DM core's tasks (the iDMA copy
# uses 64-bit global addresses, the task tables are chip-wide copies).
L2_CFG_KEYS = {"bingo_substitute_level_mask": "3"}
# In int32_add_2plain_1cluster, global task 0 is the first iDMA load on the DM core.
TWO_PLAIN_DM_FAULT_GID = 0
TWO_PLAIN_DM_VICTIM = (0, 3, 0)
# Generated BingoCoreTypeId of snax_versacore_2plain_to_cluster, checked against
# the generated RTL by the level-3 scenarios (their cfg keys use these ids)
TWO_PLAIN_CORE_TYPES = {0: 1, 1: TWO_PLAIN_CORE_TYPE, 2: TWO_PLAIN_CORE_TYPE, 3: 3, 4: 0}
QUAD_CTRL_RTL = "target/rtl/src/occamy_quad_ctrl.sv"
# dma_chain_2cluster: copies 0-5 on the cluster-0 DM core, in order. The victim
# is late on each task before task 3 (100k core cycles, ~45k quad cycles: past
# the 30k late threshold, short of the 100k heartbeat timeout), then hangs on it.
CHAIN_FAULT_GID = 3
# bingo slots per cluster of hemaia_ci (two cores and the host slot): a status
# bitmap bit is core + cluster * HEMAIA_CI_SLOTS
HEMAIA_CI_SLOTS = 3
CHAIN_FLAGS = "-DBINGO_WD_FAULT_PRE_STALL_CYCLES=100000 -DBINGO_RISK_LATE=30000"
MOE2_FAULT_GID = 4  # e0_gemm; explicit node ids in moe2_2cluster/main_bingo.py
MOE2_CORE_TYPE = 1
MOE2_FB_FLAGS = ("-DBINGO_CERF_FB_CLUSTER=0 -DBINGO_CERF_FB_CORE=0 "
                 "-DBINGO_CERF_FB_CLEAR=0 -DBINGO_CERF_FB_SET=1")

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
    "s10": dict(
        desc="plain core 1 hangs, level 3 forced over a loopback remote link: run on core 2 via import",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=LOOPBACK_CFG_KEYS, cfg_suffix="_rlink_loopback",
        workload="int32_add_2plain_1cluster", fault_stall_cycles=0,
        fault_gid=TWO_PLAIN_FAULT_GID, victim=TWO_PLAIN_VICTIM, substitute=TWO_PLAIN_SUBSTITUTE,
        remote_loopback=True, expect_core_types=TWO_PLAIN_CORE_TYPES,
        expect_eoc=True, expect_fence=True, sim_timeout_s=3600,
    ),
    "s11": dict(
        desc="DM core hangs, level 3 on but no remote target for its type: fenced, stuck, nothing exported",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=NO_TARGET_CFG_KEYS, cfg_suffix="_rlink_notarget",
        workload="int32_add_2plain_1cluster", fault_stall_cycles=0,
        fault_gid=TWO_PLAIN_DM_FAULT_GID, victim=TWO_PLAIN_DM_VICTIM,
        expect_core_types=TWO_PLAIN_CORE_TYPES,
        # healthy runs of this workload end after ~3 min of wall time
        expect_eoc=False, expect_fence=True, sim_timeout_s=1800,
    ),
    "s13": dict(
        desc="plain core 1 hangs, level 3 over the loopback link, but no core may run the import: rejected, stuck",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=REJECT_CFG_KEYS, cfg_suffix="_rlink_reject",
        workload="int32_add_2plain_1cluster", fault_stall_cycles=0,
        fault_gid=TWO_PLAIN_FAULT_GID, victim=TWO_PLAIN_VICTIM,
        remote_reject=True, expect_core_types=TWO_PLAIN_CORE_TYPES,
        expect_eoc=False, expect_fence=True, sim_timeout_s=1800,
    ),
    "s14": dict(
        desc="as s7 with the recovery boost: the substitute's domain runs at level 3 while it takes over",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        workload="int32_add_2plain_1cluster", fault_stall_cycles=0,
        fault_gid=TWO_PLAIN_FAULT_GID, victim=TWO_PLAIN_VICTIM, substitute=TWO_PLAIN_SUBSTITUTE,
        extra_flags="-DBINGO_PM_BOOST_POWER_LEVEL=3", expect_boost_level=3,
        expect_eoc=True, expect_fence=True, sim_timeout_s=3600,
    ),
    "s15": dict(
        desc="no fault: the host parks the cluster-0 DM core, its tasks run on the cluster-1 DM core, run completes",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dummy_2cluster", fault_stall_cycles=None,
        victim=VICTIM, substitute=1, substitute_cluster=1, park=True,
        # bingo slot = core + cluster * cores per cluster: the victim (core 1, cluster 0)
        extra_flags="-DBINGO_PARK_REQ=2",
        expect_eoc=True, sim_timeout_s=3600,
    ),
    "s12": dict(
        desc="cluster-0 DM core hangs, level 2: its tasks run on the cluster-1 DM core, run completes",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dummy_2cluster", fault_stall_cycles=0,
        fault_gid=FAULT_GID, victim=VICTIM, substitute=1, substitute_cluster=1,
        expect_takeover=True,
        expect_eoc=True, expect_fence=True, sim_timeout_s=3600,
    ),
    "s16": dict(
        desc="expert 0 accelerator hangs, CERF g0 -> g1: backup expert and join complete",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_cerf_l1",
        workload="moe2_2cluster", fault_stall_cycles=0,
        fault_gid=MOE2_FAULT_GID, victim=(0, 0, 0), cerf_fallback=True,
        extra_flags=MOE2_FB_FLAGS + " -DBINGO_MOE_EXPECT_EXPERT=1",
        expect_core_types={0: MOE2_CORE_TYPE, 1: 2, 2: 0},
        expect_eoc=True, expect_fence=True, expect_host_stuck=True, sim_timeout_s=900,
    ),
    "s17": dict(
        desc="healthy top-1 MoE2: expert 0 runs, ordered backup expert is skipped",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_cerf_l1",
        workload="moe2_2cluster", fault_stall_cycles=None, cerf_fallback=True,
        victim=(0, 0, 0),
        extra_flags=MOE2_FB_FLAGS + " -DBINGO_MOE_EXPECT_EXPERT=0",
        expect_core_types={0: MOE2_CORE_TYPE, 1: 2, 2: 0},
        expect_eoc=True, sim_timeout_s=900,
    ),
    "s18": dict(
        desc="DM core late on tasks 0-2 then hangs, precursors off: fenced, replayed on the cluster-1 DM core",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=0, fault_gid=CHAIN_FAULT_GID,
        victim=VICTIM, substitute=1, substitute_cluster=1, expect_takeover=True,
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=0",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_late=3, expect_eoc=True, expect_fence=True, sim_timeout_s=900,
    ),
    "s19": dict(
        desc="as s18 with precursors on: at risk after two late beats, parked, never fenced",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=0, fault_gid=CHAIN_FAULT_GID,
        victim=VICTIM, substitute=1, substitute_cluster=1, park=True,
        # threshold 2, park
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=0x12",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_late=2, expect_risk=True, expect_wd=[], expect_eoc=True, sim_timeout_s=900,
    ),
}

# fenced= only exists since the replay support; older RTL prints dead_suspect only.
WD_RE = re.compile(r"\[BINGO_WD\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) dead_suspect=(\d)(?: fenced=(\d))?")
REMAP_RE = re.compile(r"\[BINGO_REMAP\].*")
REPLAY_RE = re.compile(r"\[BINGO_REPLAY\].*")
# Parsed forms of the remap / replay / retire / fence-drop lines
REMAP_FIELDS_RE = re.compile(r"\[BINGO_REMAP\] \d+ chip=(\d+) task=(\d+) logical_core=(\d+) -> physical_core=(\d+) cluster=(\d+)")
REPLAY_FIELDS_RE = re.compile(r"\[BINGO_REPLAY\] \d+ chip=(\d+) task=(\d+) type=\d+ logical_core=(\d+) from=(\d+) to=(\d+) cluster=(\d+) to_cluster=(\d+)")
RETIRED_RE = re.compile(r"\[BINGO_RETIRED\] \d+ chip=(\d+) core=(\d+) cluster=(\d+)")
FENCE_DROP_RE = re.compile(r"\[BINGO_FENCE\] \d+ chip=(\d+) core=(\d+) cluster=(\d+) dropped done task=(\d+)")
STUCK_RE = re.compile(r"\[BINGO_REPLAY_STUCK\].*")
PARK_RE = re.compile(r"\[BINGO_PARK\] \d+ chip=(\d+) core=(\d+) cluster=(\d+) (HOLD|FAIL|dropped, fenced|PARKED -> core=(\d+) cluster=(\d+))")
ASSERT_RE = re.compile(r"\[BINGO_ASSERT\].*")
CSR_RE = re.compile(r"\[BINGO_CSR\].*")
CHECK_RE = re.compile(r"Check \[([^\]]*)\]: (PASS|FAIL)")
# Level 3 (bingo_hw_manager_top / occamy_quad_ctrl remote link logs)
EXPORT_RE = re.compile(r"\[BINGO_EXPORT\] \d+ chip=(\d+) task=(\d+) type_id=(\d+) proxy_slot=(\d+)")
IMPORT_RE = re.compile(r"\[BINGO_IMPORT\] \d+ chip=(\d+) task=(\d+) from chip=(\d+) proxy_slot=(\d+) -> core=(\d+) cluster=(\d+)")
RDONE_OUT_RE = re.compile(r"\[BINGO_REMOTE_DONE_OUT\] \d+ chip=(\d+) task=(\d+) -> chip=(\d+) proxy_slot=(\d+)")
RDONE_IN_RE = re.compile(r"\[BINGO_REMOTE_DONE_IN\] \d+ chip=(\d+) task=(\d+) proxy_slot=(\d+)")
REJECT_OUT_RE = re.compile(r"\[BINGO_REMOTE_REJECT_OUT\] \d+ chip=(\d+) task=(\d+) -> chip=(\d+) proxy_slot=(\d+)")
REJECT_IN_RE = re.compile(r"\[BINGO_REMOTE_REJECT_IN\] \d+ chip=(\d+) task=(\d+) proxy_slot=(\d+)")
RLINK_RE = re.compile(r"\[BINGO_RLINK_(TX|RX)_(AW|W|B)\] \d+ chip=(\d+) (addr|data|resp)=(0x[0-9a-fA-F]+|\d+)")
LATE_RE = re.compile(r"\[BINGO_LATE\] \d+ core=(\d+) cluster=(\d+)")
RISK_RE = re.compile(r"\[BINGO_RISK\] \d+ core=(\d+) cluster=(\d+) at risk")
CERF_FB_RE = re.compile(r"\[BINGO_CERF_FB\] (\d+) type (\d+) clear g(\d+) set g(\d+)")
DISPATCH_RE = re.compile(r"\[BINGO_DISPATCH\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")


def evaluate_risk_chain(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Require the chain's actual placement, host risk bitmap and both goldens."""
    problems: List[str] = []
    chip, core, cluster = sc["victim"]
    sub, sub_cluster = sc["substitute"], sc["substitute_cluster"]
    parked = sc.get("expect_risk", False)
    dispatches = [tuple(map(int, m.groups())) for m in DISPATCH_RE.finditer(log_text)
                  if int(m.group(3)) < 6]
    expected = [(task, core, cluster) for task in range(2 if parked else 4)]
    expected += [(task, sub, sub_cluster) for task in range(2 if parked else 3, 6)]
    if [(d[2], d[3], d[4]) for d in dispatches] != expected or any(
            d[1] != chip for d in dispatches):
        problems.append(f"chain dispatches {dispatches}, expected {expected} on chip {chip}")
    if parked:
        risk_times = [int(t) for t in re.findall(
            r"\[BINGO_RISK\] (\d+) core=\d+ cluster=\d+ at risk", log_text)]
        task1 = [d[0] for d in dispatches if d[2] == 1]
        task2 = [d[0] for d in dispatches if d[2] == 2]
        if len(risk_times) != 1 or len(task1) != 1 or len(task2) != 1 or not (
                task1[0] < risk_times[0] < task2[0]):
            problems.append("risk must trip during task 1, before task 2 is dispatched")
    host = re.search(r"\[Host\] Bingo status: .* risk=0x([0-9a-fA-F]+)", uart_text)
    expected_risk = (1 << (core + HEMAIA_CI_SLOTS * cluster)) if parked else 0
    if not host or int(host.group(1), 16) != expected_risk:
        problems.append(f"host risk {host.group(1) if host else None}, expected 0x{expected_risk:x}")
    if sorted(CHECK_RE.findall(uart_text)) != [
            ("A_chain_cluster0", "PASS"), ("A_cluster1", "PASS")]:
        problems.append("expected both DMA-chain host checks to pass exactly once")
    return problems


def evaluate_cerf_fallback(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Check actual dispatches, final CERF/evt and the selected expert golden."""
    problems: List[str] = []
    fault = sc["fault_stall_cycles"] is not None
    expert = 1 if fault else 0
    fb = [tuple(map(int, m.groups())) for m in CERF_FB_RE.finditer(log_text)]
    if len(fb) != int(fault) or any(e[1:] != (MOE2_CORE_TYPE, 0, 1) for e in fb):
        problems.append(f"CERF fallback events {fb}, expected {'one type 1 g0 -> g1' if fault else 'none'}")
    dispatches = [tuple(map(int, m.groups())) for m in DISPATCH_RE.finditer(log_text)]
    primary_ids = {2, 3, 4, 5, 12}  # branch and the g0 exit of the fault core
    backup_ids = {6, 7, 8, 9}
    expected_primary = {2, 3, 4} if fault else primary_ids
    primary = [d for d in dispatches if d[2] in primary_ids]
    backup = [d for d in dispatches if d[2] in backup_ids]
    if {d[2] for d in primary} != expected_primary or len(primary) != len(expected_primary):
        problems.append(f"primary dispatches {primary}, expected tasks {sorted(expected_primary)} once")
    if {d[2] for d in backup} != (backup_ids if fault else set()) or len(backup) != (4 if fault else 0):
        problems.append(f"backup dispatches {backup}, expected {'tasks 6..9 once' if fault else 'none'}")
    for d in primary + backup:
        cl = 0 if d[2] in primary_ids else 1
        core = 0 if d[2] in {4, 8, 12} else 1
        if (d[1], d[3], d[4]) != (0, core, cl):
            problems.append(f"expert task on wrong physical slot: {d}")
    if fault and len(fb) == 1:
        switched_at = fb[0][0]
        if any(d[0] >= switched_at for d in primary):
            problems.append("g0 task dispatched after the CERF fallback")
        if any(d[0] <= switched_at for d in backup):
            problems.append("backup task dispatched before the CERF fallback")
    joins = [d for d in dispatches if d[2] == 10]
    if len(joins) != 1 or joins[0][1:] != (0, 10, 2, 0):
        problems.append(f"host join dispatches {joins}, expected task 10 once on host")
    elif any(d[0] >= joins[0][0] for d in primary + backup if d[2] != 12):
        problems.append("join dispatched before the expert branch finished dispatching")
    host = re.search(r"\[Host\] Bingo status: .* cerf=0x([0-9a-fA-F]+) "
                     r"cerf_fb_en=0x([0-9a-fA-F]+) cerf_fb_evt=0x([0-9a-fA-F]+)", uart_text)
    expected = (1 << expert, 1 << MOE2_CORE_TYPE, (1 << MOE2_CORE_TYPE) if fault else 0)
    if not host or tuple(int(v, 16) for v in host.groups()) != expected:
        problems.append(f"host CERF/en/evt {host.groups() if host else None}, expected {expected}")
    if CHECK_RE.findall(uart_text) != [(f"expert_{expert}_D", "PASS")]:
        problems.append(f"expected only Check [expert_{expert}_D]: PASS")
    if f"[MoE2] router selected expert 0; join complete; output expert {expert}" not in uart_text:
        problems.append("missing router / join completion marker")
    print(f"[cerf_fallback] events={fb} primary={primary} backup={backup} joins={joins}")
    return problems


def make_cfg(base_cfg: str, timeout_cycles: Optional[int],
             cluster_swap: Optional[Tuple[str, str]] = None,
             confirm_timeout_cycles: Optional[int] = None,
             extra_cfg: Optional[Dict[str, str]] = None, cfg_suffix: str = "") -> str:
    """Return a repo-relative cfg; write a patched copy if anything is overridden.

    extra_cfg: further s1_quadrant keys (hjson literals), added next to
    dep_tag_width; cfg_suffix names that variant.
    """
    if timeout_cycles is None and cluster_swap is None and confirm_timeout_cycles is None \
            and not extra_cfg:
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
    for key, value in (extra_cfg or {}).items():
        if re.search(rf"^\s*{key}\s*:", new_text, flags=re.M):
            raise ValueError(f"{base_cfg} already sets {key}")
        new_text, n = re.subn(r"^(\s*)(dep_tag_width:[^\n]*)$",
                              lambda m: f"{m.group(1)}{m.group(2)}\n{m.group(1)}{key}: {value}",
                              new_text, count=1, flags=re.M)
        if n != 1:
            raise ValueError(f"could not find dep_tag_width in {base_cfg}")
    suffix += cfg_suffix
    dst = _REPO_ROOT / "target/rtl/cfg/generated" / f"{src.stem}{suffix}.hjson"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(new_text)
    return str(dst.relative_to(_REPO_ROOT))


def clean_app_builds(workload: str) -> None:
    """Force a fresh SW build: objects and libraries do not depend on USER_FLAGS.

    The device runtime library matters too: the offload loop that actually runs
    (bingo_hw_offload_manager, a C99 inline function) is linked from
    libsnRuntime.a, not from the app's own translation unit. So does the host's
    libbingo: BINGO_PM_* (idle entry delay, boost level) are compiled into it.
    """
    for build_dir in (
        _REPO_ROOT / "target/sw/device/runtime/build",
        _REPO_ROOT / "target/sw/host/runtime/libbingo/build",
        _REPO_ROOT / "target/sw/device/apps/snax/snax-bingo-offload/build",
        _REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads" / workload / "build",
    ):
        if build_dir.is_dir():
            shutil.rmtree(build_dir)


def decode_packet(word: int) -> dict:
    """bingo_hw_manager_remote_link packet (one 64-bit AXI-Lite write)."""
    return dict(kind=(word >> 60) & 0xF, chip=(word >> 52) & 0xFF, slot=(word >> 44) & 0xFF,
                core_type=(word >> 40) & 0xF, seq=(word >> 32) & 0xFF,
                task_type=(word >> 30) & 0x3, task_id=word & 0x3FFFFFFF)


def evaluate_remote_loopback(sc: dict, log_text: str) -> List[str]:
    """Level 3 over the loopback remote link: bingo events and the link packets."""
    problems: List[str] = []
    chip, core, cluster = victim = sc["victim"]
    substitute = sc["substitute"]
    proxy_slot = core  # one cluster: flat slot = core + cluster * cores per cluster
    # The victim's entries are rotated (kept on the proxy), never moved locally
    moved = [tuple(int(x) for x in m.groups()) for m in REPLAY_FIELDS_RE.finditer(log_text)]
    if any((r[3], r[4]) != (core, core) for r in moved):
        problems.append(f"local replay although level 3 is forced: {moved[:3]}")
    if REMAP_RE.findall(log_text):
        problems.append(f"local remap although level 3 is forced: {REMAP_RE.findall(log_text)[:3]}")
    retired = [tuple(int(x) for x in m.groups()) for m in RETIRED_RE.finditer(log_text)]
    if retired != [victim]:
        problems.append(f"[BINGO_RETIRED] expected only {victim}, got {retired}")
    exports = [tuple(int(x) for x in m.groups()) for m in EXPORT_RE.finditer(log_text)]
    imports = [tuple(int(x) for x in m.groups()) for m in IMPORT_RE.finditer(log_text)]
    dones_out = [tuple(int(x) for x in m.groups()) for m in RDONE_OUT_RE.finditer(log_text)]
    dones_in = [tuple(int(x) for x in m.groups()) for m in RDONE_IN_RE.finditer(log_text)]
    exp_tasks = [e[1] for e in exports]
    if sc["fault_gid"] not in exp_tasks:
        problems.append(f"task {sc['fault_gid']} was not exported: {exports}")
    if any((e[0], e[2], e[3]) != (chip, TWO_PLAIN_CORE_TYPE, proxy_slot) for e in exports):
        problems.append(f"export not from chip {chip} type {TWO_PLAIN_CORE_TYPE} slot {proxy_slot}: {exports}")
    if [i[1] for i in imports] != exp_tasks:
        problems.append(f"imports {[i[1] for i in imports]} != exports {exp_tasks}")
    if any((i[0], i[2], i[3], i[4], i[5]) != (chip, chip, proxy_slot, substitute, cluster) for i in imports):
        problems.append(f"import not on core {substitute} from slot {proxy_slot}: {imports}")
    if [d[1] for d in dones_out] != exp_tasks or any((d[2], d[3]) != (chip, proxy_slot) for d in dones_out):
        problems.append(f"remote dones out {dones_out}, expected tasks {exp_tasks} to slot {proxy_slot}")
    if [d[1] for d in dones_in] != exp_tasks or any(d[2] != proxy_slot for d in dones_in):
        problems.append(f"remote dones in {dones_in}, expected tasks {exp_tasks} on slot {proxy_slot}")
    # Packets on the real quad AXI-Lite xbar path: TX (link master) and RX (mailbox leaf)
    ev = {(d, c): [] for d in ("TX", "RX") for c in ("AW", "W", "B")}
    for m in RLINK_RE.finditer(log_text):
        direction, channel, pchip, _, value = m.groups()
        ev[(direction, channel)].append(int(value, 0))
    tx = list(zip(ev[("TX", "AW")], ev[("TX", "W")]))
    rx = list(zip(ev[("RX", "AW")], ev[("RX", "W")]))
    if len(ev[("TX", "AW")]) != len(ev[("TX", "W")]) or len(ev[("TX", "B")]) != len(tx):
        problems.append(f"TX AW/W/B counts {[len(ev[('TX', c)]) for c in ('AW', 'W', 'B')]}")
    if any(ev[("TX", "B")]):
        problems.append(f"TX write error responses: {ev[('TX', 'B')]}")
    if rx != tx:
        problems.append(f"loopback RX packets differ from TX: tx={tx[:4]} rx={rx[:4]}")
    if len(tx) != 2 * len(exports):
        problems.append(f"{len(tx)} link packets for {len(exports)} exports (expected dispatch + done each)")
    chip_base = (chip << 40) | REMOTE_LINK_BASE
    dispatch_ids, done_ids = [], []
    for addr, data in tx:
        pk = decode_packet(data)
        if addr == chip_base and pk["kind"] == 0x5:
            if (pk["chip"], pk["slot"], pk["core_type"]) != (chip, proxy_slot, TWO_PLAIN_CORE_TYPE):
                problems.append(f"dispatch packet fields {pk} (0x{data:016x})")
            dispatch_ids.append(pk["task_id"])
        elif addr == chip_base + 0x1000 and pk["kind"] == 0xA:
            if (pk["chip"], pk["slot"]) != (chip, proxy_slot):
                problems.append(f"done packet fields {pk} (0x{data:016x})")
            done_ids.append(pk["task_id"])
        else:
            problems.append(f"unexpected packet addr=0x{addr:012x} data=0x{data:016x}")
    if dispatch_ids != exp_tasks or done_ids != exp_tasks:
        problems.append(f"packet task ids: dispatch {dispatch_ids}, done {done_ids}, exports {exp_tasks}")
    # Sequence numbers start at 0 after reset, per stream (dispatch / done)
    for page, what in ((chip_base, "dispatch"), (chip_base + 0x1000, "done")):
        seqs = [decode_packet(d)["seq"] for a, d in tx if a == page]
        if seqs != [i % 256 for i in range(len(seqs))]:
            problems.append(f"{what} sequence numbers {seqs}")
    status = re.findall(r"\[BINGO_STATUS\] \d+ chip=\d+ (.*)", log_text)
    if not status:
        problems.append("no [BINGO_STATUS] line (the fence must change it)")
    print(f"[remote_loopback] exports={exports} imports={imports} tx_packets="
          f"{[(hex(a), hex(d)) for a, d in tx]} status={status[-1:] if status else None}")
    return problems


def evaluate_remote_reject(sc: dict, log_text: str) -> List[str]:
    """Level 3 over the loopback link, import rejected: the proxy is stuck."""
    problems: List[str] = []
    chip, core, cluster = sc["victim"]
    proxy_slot = core  # one cluster
    task = sc["fault_gid"]
    exports = [tuple(int(x) for x in m.groups()) for m in EXPORT_RE.finditer(log_text)]
    if exports != [(chip, task, TWO_PLAIN_CORE_TYPE, proxy_slot)]:
        problems.append(f"exports {exports}, expected only task {task} (the exit task stays held)")
    for name, rx in (("[BINGO_IMPORT]", IMPORT_RE), ("[BINGO_REMOTE_DONE_OUT]", RDONE_OUT_RE),
                     ("[BINGO_REMOTE_DONE_IN]", RDONE_IN_RE)):
        if rx.findall(log_text):
            problems.append(f"unexpected {name}: {rx.findall(log_text)[:3]}")
    rej_out = [tuple(int(x) for x in m.groups()) for m in REJECT_OUT_RE.finditer(log_text)]
    rej_in = [tuple(int(x) for x in m.groups()) for m in REJECT_IN_RE.finditer(log_text)]
    if rej_out != [(chip, task, chip, proxy_slot)] or rej_in != [(chip, task, proxy_slot)]:
        problems.append(f"rejects out {rej_out} / in {rej_in}, expected task {task} to slot {proxy_slot}")
    moved = [tuple(int(x) for x in m.groups()) for m in REPLAY_FIELDS_RE.finditer(log_text)]
    if any((r[3], r[4]) != (core, core) for r in moved):
        problems.append(f"local replay although level 3 is forced: {moved[:3]}")
    # Link: the dispatch, then the reject (kind 0xC) on the done page
    ev = {(d, c): [] for d in ("TX", "RX") for c in ("AW", "W", "B")}
    for m in RLINK_RE.finditer(log_text):
        direction, channel, _, _, value = m.groups()
        ev[(direction, channel)].append(int(value, 0))
    tx = list(zip(ev[("TX", "AW")], ev[("TX", "W")]))
    rx = list(zip(ev[("RX", "AW")], ev[("RX", "W")]))
    chip_base = (chip << 40) | REMOTE_LINK_BASE
    kinds = [(a - chip_base, decode_packet(d)["kind"], decode_packet(d)["task_id"]) for a, d in tx]
    if kinds != [(0, 0x5, task), (0x1000, 0xC, task)]:
        problems.append(f"link packets {[(hex(a), hex(d)) for a, d in tx]}, expected dispatch + reject of task {task}")
    if rx != tx or any(ev[("TX", "B")]):
        problems.append(f"RX {rx} / TX {tx} / B {ev[('TX', 'B')]}")
    status = re.findall(r"\[BINGO_STATUS\] \d+ chip=\d+ (.*)", log_text)
    if not status or "replay_stuck=1 " not in status[-1]:
        problems.append(f"final [BINGO_STATUS] {status[-1:]}: expected replay_stuck=1")
    print(f"[remote_reject] exports={exports} rejects={rej_out}/{rej_in} packets={[(hex(a), hex(d)) for a, d in tx]}")
    return problems


def evaluate_park(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Core parking without a fault: the victim slot goes HOLD -> PARKED onto the
    substitute, every task routed away from it lands there, nothing is fenced,
    replayed or retired, and the host sees no park failure."""
    problems: List[str] = []
    chip, core, cluster = sc["victim"]
    sub, sub_cluster = sc["substitute"], sc["substitute_cluster"]
    park = [(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).split(" ->")[0],
             m.group(5), m.group(6)) for m in PARK_RE.finditer(log_text)]
    expected = [(chip, core, cluster, "HOLD", None, None),
                (chip, core, cluster, "PARKED", str(sub), str(sub_cluster))]
    if park != expected:
        problems.append(f"[BINGO_PARK] events {park}, expected HOLD then PARKED -> core {sub} cluster {sub_cluster}")
    routed = [tuple(int(x) for x in m.groups()) for m in REMAP_FIELDS_RE.finditer(log_text)]
    if not routed:
        problems.append("no task of the parked core was routed to its substitute")
    wrong = [r for r in routed if (r[0], r[2], r[3], r[4]) != (chip, core, sub, sub_cluster)]
    if wrong:
        problems.append(f"remap other than logical core {core} -> {sub} (cluster {sub_cluster}): {wrong[:3]}")
    if REPLAY_RE.findall(log_text) or RETIRED_RE.findall(log_text):
        problems.append("a parked core was replayed or retired")
    if f"Exit task of cluster {cluster} core {core} taken over" not in uart_text:
        problems.append(f"no 'Exit task of cluster {cluster} core {core} taken over' in the UART log")
    host = re.search(r"\[Host\] Bingo status: .* park_fail=0x([0-9a-fA-F]+)", uart_text)
    if not host or int(host.group(1), 16) != 0:
        problems.append(f"host park_fail {host.group(1) if host else '?'}, expected 0")
    print(f"[park] events={park} routed={len(routed)}")
    return problems


def check_core_types(expected: Dict[int, int]) -> List[str]:
    """Compare the generated homogeneous BingoCoreTypeId with {core: type}."""
    rtl = _REPO_ROOT / QUAD_CTRL_RTL
    text = rtl.read_text(errors="replace") if rtl.exists() else ""
    found = {int(core): int(t) for t, core in
             re.findall(r"^\s*\d+'d(\d+)[^\n]*// core (\d+), clusters \d+\.\.\d+$", text, flags=re.M)}
    if found != expected:
        return [f"generated BingoCoreTypeId {found} in {QUAD_CTRL_RTL}, the scenario assumes {expected}"]
    return []


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
    # (includes the host reading X, which derails it before EOC)
    other_asserts = re.findall(r"^.*(?:\[ASSERT FAILED\]|Assertion.*failed|reading invalid data).*$",
                               log_text, flags=re.M)
    csr = CSR_RE.findall(log_text)
    checks = CHECK_RE.findall(uart_text)
    if sc.get("cerf_fallback"):
        problems += evaluate_cerf_fallback(sc, log_text, uart_text)
    elif "[BINGO_CERF_FB]" in log_text:
        problems.append("unexpected CERF fallback in a continuity scenario")
    if sc.get("workload") == "dma_chain_2cluster":
        problems += evaluate_risk_chain(sc, log_text, uart_text)

    # Fault precursors: late beats and at-risk events only where expected, on the victim
    vc = sc.get("victim", VICTIM)
    late = [tuple(int(x) for x in m.groups()) for m in LATE_RE.finditer(log_text)]
    if late != [(vc[1], vc[2])] * sc.get("expect_late", 0):
        problems.append(f"[BINGO_LATE] on (core, cluster) {late}, expected {sc.get('expect_late', 0)} on the victim")
    risk = [tuple(int(x) for x in m.groups()) for m in RISK_RE.finditer(log_text)]
    if risk != ([(vc[1], vc[2])] if sc.get("expect_risk") else []):
        problems.append(f"[BINGO_RISK] events {risk}, expected {'one on the victim' if sc.get('expect_risk') else 'none'}")
    host_risk = re.search(r"\[Host\] Bingo status: .* risk=0x([0-9a-fA-F]+)", uart_text)
    if host_risk and int(host_risk.group(1), 16) != (1 << (vc[1] + HEMAIA_CI_SLOTS * vc[2]) if sc.get("expect_risk") else 0):
        problems.append(f"host risk=0x{host_risk.group(1)}")

    if sc["expect_eoc"] and not eoc_ok:
        problems.append(f"expected '{SIM_OK_MARKER}' without errors")
    if not sc["expect_eoc"] and eoc_ok:
        problems.append("run finished although the victim task hangs forever")
    victim = sc.get("victim", VICTIM)
    substitute = sc.get("substitute")
    if sc.get("remote_loopback", False):
        problems += evaluate_remote_loopback(sc, log_text)
    elif sc.get("remote_reject", False):
        problems += evaluate_remote_reject(sc, log_text)
    else:
        # Level 3 stays idle: nothing exported, imported or sent over the link
        l3 = [l for l in log_text.splitlines()
              if re.search(r"\[BINGO_(EXPORT|IMPORT|REMOTE_DONE_OUT|REMOTE_DONE_IN|RLINK_\w+)\]", l)]
        if l3:
            problems.append(f"{len(l3)} unexpected level-3 event(s): {l3[:3]}")
    # The sticky link / remote-done / proxy-timeout flags stay clear in every scenario
    status = re.findall(r"\[BINGO_STATUS\] \d+ chip=\d+ (.*)", log_text)
    if status and not ("remote_done_mismatch=0 " in status[-1] and "link_error=0 " in status[-1] + " " and
                       "remote_timeout=0" in status[-1]):
        problems.append(f"final [BINGO_STATUS] {status[-1]}")
    if sc.get("remote_loopback", False) or sc.get("remote_reject", False):
        pass
    elif sc.get("park", False):
        problems += evaluate_park(sc, log_text, uart_text)
    elif substitute is None:
        # No core may take over the victim's tasks: nothing moves
        if remaps:
            problems.append(f"{len(remaps)} unexpected [BINGO_REMAP] event(s): {remaps[:3]}")
        if replays:
            problems.append(f"{len(replays)} unexpected [BINGO_REPLAY] event(s): {replays[:3]}")
    else:
        # Everything the victim held or gets later moves to the substitute only
        # (substitute_cluster: level 2, another cluster of the chip)
        chip, core, cluster = victim
        sub_cluster = sc.get("substitute_cluster", cluster)
        # (chip, task, logical_core, from, to, cluster, to_cluster)
        moved = [tuple(int(x) for x in m.groups()) for m in REPLAY_FIELDS_RE.finditer(log_text)]
        if not any(r[1] == sc["fault_gid"] for r in moved):
            problems.append(f"task {sc['fault_gid']} was not replayed: {replays[:3]}")
        wrong = [r for r in moved
                 if (r[0], r[3], r[4], r[5], r[6]) != (chip, core, substitute, cluster, sub_cluster)]
        if wrong or len(moved) != len(replays):
            problems.append(f"replay other than core {core} -> {substitute} (cluster {cluster} -> "
                            f"{sub_cluster}): {replays[:3]}")
        # (chip, task, logical_core, physical_core, physical cluster)
        routed = [tuple(int(x) for x in m.groups()) for m in REMAP_FIELDS_RE.finditer(log_text)]
        wrong = [r for r in routed if (r[0], r[2], r[3], r[4]) != (chip, core, substitute, sub_cluster)]
        if wrong or len(routed) != len(remaps):
            problems.append(f"remap other than logical core {core} -> {substitute} "
                            f"(cluster {sub_cluster}): {remaps[:3]}")
        if sc.get("expect_takeover", False) and \
                f"Exit task of cluster {cluster} core {core} taken over" not in uart_text:
            problems.append(f"no 'Exit task of cluster {cluster} core {core} taken over' in the UART log")
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
    if other_asserts:
        problems.append(f"{len(other_asserts)} RTL assertion failure(s): {other_asserts[:3]}")
    # (s13: stuck through the remote reject, not a local [BINGO_REPLAY_STUCK])
    expect_stuck = sc.get("expect_fence", False) and substitute is None and not sc.get("remote_reject", False)
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
        # (s19: parked before the hang, the victim never misses its timeout)
        expected = sc.get("expect_wd", expected)
        if victim_events != expected:
            problems.append(f"victim {victim} (dead_suspect, fenced) sequence {victim_events}, expected {expected}")

    print(f"[{name}] eoc_ok={eoc_ok} wd_events={events} remaps={len(remaps)} "
          f"replays={len(replays)} csr_unknown={len(csr)} checks={checks}")
    return problems


class CoreTypeCheckedSimRunner(HeMAiASimRunner):
    """Check the compiled configuration before a long simulation can outlive it."""

    def __init__(self, *, expected_core_types=None, **kwargs):
        super().__init__(**kwargs)
        self.expected_core_types = expected_core_types
        self.core_type_problems = []

    def run_simulations(self, tasks_info):
        # Generated RTL is shared by builds and may be overwritten while vsim
        # runs. Keep the check result for this build, not the next one's RTL.
        if self.expected_core_types is not None:
            self.core_type_problems = check_core_types(self.expected_core_types)
        return super().run_simulations(tasks_info)


class NoTraceSimRunner(CoreTypeCheckedSimRunner):
    """Sends the Snitch instruction traces to /dev/null.

    Questa always writes them, one line per cycle for a core spinning in a
    hang, so a scenario that runs into its wall-clock limit leaves GBs behind
    (s13: 6 GB in 30 min). The host (CVA6) trace is kept.
    """

    def run_simulations(self, tasks_info):
        for task_dir, _ in tasks_info:
            logs = Path(task_dir) / "bin" / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            for hart in range(1, 65):
                trace = logs / f"trace_chip_00_hart_{hart:05x}.dasm"
                if not trace.is_symlink():
                    trace.unlink(missing_ok=True)
                    trace.symlink_to("/dev/null")
        return super().run_simulations(tasks_info)


def run_scenario(name: str, args: argparse.Namespace) -> bool:
    sc = dict(SCENARIOS[name])
    if args.wd_timeout is not None and sc.get("timeout_cycles") is not None:
        # Sweep: other watchdog timeouts (the confirm timeout stays twice as long)
        sc["timeout_cycles"] = args.wd_timeout
        if sc.get("confirm_timeout_cycles"):
            sc["confirm_timeout_cycles"] = 2 * args.wd_timeout
    out_dir = Path(args.out_root) / name
    print(f"\n===== {name}: {sc['desc']} =====")
    cfg = make_cfg(sc["cfg"], sc["timeout_cycles"], sc.get("cluster_swap"),
                   sc.get("confirm_timeout_cycles"), sc.get("extra_cfg"), sc.get("cfg_suffix", ""))
    clean_app_builds(sc["workload"])

    task = make_task(host_app_type="offload_bingo_hw", chip_type="single_chip",
                     workload=sc["workload"], dev_app="snax-bingo-offload")
    if sc["fault_stall_cycles"] is not None:
        task["extra_user_flags"] = (f"-DBINGO_WD_FAULT_GID={sc.get('fault_gid', FAULT_GID)} "
                                    f"-DBINGO_WD_FAULT_STALL_CYCLES={sc['fault_stall_cycles']}")
        if sc.get("substitute") is not None or sc.get("cerf_fallback"):
            # The substitute gets the same task replayed: only the victim misbehaves
            _, core, cluster = sc["victim"]
            task["extra_user_flags"] += f" -DBINGO_WD_FAULT_CLUSTER={cluster} -DBINGO_WD_FAULT_CORE={core}"
    for flags in (sc.get("extra_flags"), args.extra_flags):
        if flags:
            task["extra_user_flags"] = (task.get("extra_user_flags", "") + " " + flags).strip()

    runner_cls = CoreTypeCheckedSimRunner if args.keep_traces else NoTraceSimRunner
    runner = runner_cls(
        expected_core_types=sc.get("expect_core_types"),
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
    problems += runner.core_type_problems
    if sc["expect_eoc"] and log_text:
        # The host prints the manager's status at the end; its fenced bitmap must
        # match the last [BINGO_STATUS] of the RTL (register order; 0 without
        # such a line)
        host = re.search(r"\[Host\] Bingo status: replay_stuck=(\d+) remote_done_mismatch=(\d+) "
                         r"link_error=(\d+) fenced=0x([0-9a-fA-F]+)", uart_text)
        rtl = re.findall(r"\[BINGO_STATUS\] \d+ chip=\d+ .*fenced=0x([0-9a-fA-F]+)", log_text)
        rtl_fenced = int(rtl[-1], 16) if rtl else 0
        if not host:
            problems.append("no '[Host] Bingo status' line in the UART log")
        elif (int(host.group(4), 16) != rtl_fenced) or \
                host.group(1) != str(int(sc.get("expect_host_stuck", False))) or host.group(3) != "0":
            problems.append(f"host status {host.group(0)} (RTL fenced 0x{rtl_fenced:x})")
    if sc.get("expect_boost_level"):
        # The victim's cluster domain (1) reaches the boost level after the fence
        fence_t = [int(m.group(1)) for m in WD_RE.finditer(log_text) if m.group(6) == "1"]
        boosts = [int(t) for t, lvl in re.findall(r"\[BINGO_PM\] (\d+) domain=1 level=(\d+)", log_text)
                  if int(lvl) == sc["expect_boost_level"]]
        if not fence_t or not any(t >= fence_t[0] for t in boosts):
            problems.append(f"no [BINGO_PM] domain=1 level={sc['expect_boost_level']} after the fence")
    eoc = re.search(r"All chips finished successfully at (\d+)", log_text)
    if eoc:
        print(f"[{name}] EOC at {int(eoc.group(1)) / 1e9:.3f} ms")
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
    parser.add_argument("--extra-flags", default="",
                        help="further SW flags for every scenario, given with '=' because they start "
                             "with '-', e.g. --extra-flags='-DBINGO_PM_IDLE_ENTRY_DELAY=10000' (sweeps)")
    parser.add_argument("--wd-timeout", type=int, default=None,
                        help="override the watchdog heartbeat timeout (quad_ctrl cycles) of every "
                             "scenario that sets one; the confirm timeout becomes twice that (sweeps; "
                             "s3/s8/s9 size their stalls for the default 100k)")
    parser.add_argument("--keep-traces", action="store_true",
                        help="keep the Snitch instruction traces (bin/logs/*.dasm); by default "
                             "they go to /dev/null, a hung core writes GBs of them")
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
