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
s20     sparse late beats, epoch 30k, threshold 2          counts decay between beats, no risk or park
s21     park after two late beats, host CLEAR after copy 2 copy 2 on cluster 1, copies 3-5 back home
s22     level 1 only, park + derate level 10               park fails, domain 1 derates, copies stay home
s23     same sparse beats as s20, but epoch 0              risk trips and parks (decay-off control)
s24     s16 with boost level 3, substitute boost policy    as s16; the stuck core has no substitute,
        (P3b)                                              so no domain is ever boosted
s25     s16 with boost level 3, capacity policy (one       as s16; domain 2 (the type-1 survivor in
        domain per lost core)                              cluster 1) boosted after the fallback, never
                                                           domain 1
s26     two plain cores, L3 only, home-slot import;        export and reject, CERF g0 -> g1, backup
        primary add hangs, CERF fallback enabled           add and join complete, EOC and backup PASS
s27     same workload/configuration as s26, no fault      primary add PASS, backup skipped, EOC,
                                                           no watchdog or fallback event
s28     dma_cerf_2cluster, level 2: copy 0 hangs on any    both DM cores fenced (the substitute after
        core, so the cluster-1 DM core dies on the         the replay), CERF type 2 g0 -> g1, the
        replay too; host iDMA fallback                     substitute drains the dead core's task,
                                                           host copy and join complete, EOC, PASS
s29     as s28, but only the cluster-0 DM core hangs       replay to the cluster-1 DM core, which
                                                           runs both copies and the taken-over exit;
                                                           no fallback, primary output PASS
s37     in-place int32 add, fault after the kernel        no replay, blocked CSR, separate-output
                                                           host CERF backup, both outputs PASS
s38     same fault, test-only unsafe replay               add replayed, double accumulation PASS
s39     same protected workload, healthy                  one accumulation, no fault or backup
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
* ``Bender.yml`` pins the tested Bingo Git revision; ``Bender.lock`` selects the
  actual checkout. A Path checkout must match ``--bingo-repo`` (the container
  mount). Startup rejects changed RTL, but only warns on RTL-identical HEAD
  divergence or uncommitted changes.

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
import csv
import hashlib
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_SCRIPT = Path(__file__).resolve()
_REPO_ROOT = _SCRIPT.parents[4]  # target/sim/automation/test -> repo root
sys.path.insert(0, str(_SCRIPT.parent))  # Also support importlib-based tests.
sys.path.insert(0, str(_REPO_ROOT / "util" / "automation_scripts"))

from hemaia_sim_runner import (  # noqa: E402
    SIM_ERR_MARKER, SIM_OK_MARKER, HeMAiASimRunner, make_task, task_dir_name,
)
from bingo_bender_pin import check_bingo_bender_pin  # noqa: E402
from bingo_evlog import check_evlog, check_evlog_pair  # noqa: E402
from bingo_p8 import recovery_hold_config, check_recovery_hold, check_access_level  # noqa: E402
from bingo_c2 import healthy_problems  # noqa: E402

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
PLAIN_CERF_FAULT_GID = 3  # core1_add in int32_add_2plain_cerf_1cluster
DMA_CERF_FAULT_GID = 1  # copy_0 in dma_cerf_2cluster
DMA_CERF_CORE_TYPE = 2  # the DM cores of hemaia_ci
HOST_FALLBACK_FAULT_GID = 0  # pinned against the generated graph in test_host_fallback
INPLACE_FAULT_GID = 2  # pinned against the generated graph in test_inplace_cerf_workload

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
        extra_flags="-DBINGO_MOE_EXPECT_EXPERT=1",
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
        extra_flags="-DBINGO_MOE_EXPECT_EXPERT=0",
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
    "s20": dict(
        desc="sparse late beats with a nonzero epoch: count halves between beats, never parks",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=100_000, fault_gid=999,
        victim=VICTIM,
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=0x12 -DBINGO_RISK_EPOCH=30000",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_chain_clusters=[0] * 6, expect_decay=True,
        expect_late=7, expect_wd=[], expect_eoc=True, sim_timeout_s=900,
    ),
    "s21": dict(
        desc="host holds risk CLEAR after remapped copy 2: UNPARK, later copies return home",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=100_000, fault_gid=999,
        victim=VICTIM, substitute=1, substitute_cluster=1, park=True,
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=0x12 -DBINGO_CHAIN_CLEAR_TEST",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_chain_clusters=[0, 0, 1, 0, 0, 0],
        expect_unpark=True, expect_final_risk=0,
        expect_late=6, expect_risk=True, expect_wd=[], expect_eoc=True, sim_timeout_s=900,
    ),
    "s22": dict(
        desc="no same-type level-1 substitute: park fails, domain 1 derates to level 10",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_risk_l1",
        workload="dma_chain_2cluster", fault_stall_cycles=100_000, fault_gid=999,
        victim=VICTIM,
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=0xa32",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_chain_clusters=[0] * 6, expect_derate_level=10,
        expect_late=7, expect_risk=True, expect_wd=[], expect_eoc=True, sim_timeout_s=900,
    ),
    "s23": dict(
        desc="decay-off control for s20: the same sparse late beats trip risk and park",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=100_000, fault_gid=999,
        victim=VICTIM, substitute=1, substitute_cluster=1, park=True,
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=0x12 -DBINGO_RISK_EPOCH=0",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_chain_clusters=[0, 0, 1, 1, 1, 1],
        expect_late=2, expect_risk=True, expect_wd=[], expect_eoc=True, sim_timeout_s=900,
    ),
    "s24": dict(
        desc="as s16 with the boost level and the substitute policy: no substitute, no boost",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_cerf_l1",
        workload="moe2_2cluster", fault_stall_cycles=0,
        fault_gid=MOE2_FAULT_GID, victim=(0, 0, 0), cerf_fallback=True,
        extra_flags="-DBINGO_MOE_EXPECT_EXPERT=1 "
                    "-DBINGO_PM_BOOST_POWER_LEVEL=3 -DBINGO_BOOST_POLICY=0x0",
        expect_core_types={0: MOE2_CORE_TYPE, 1: 2, 2: 0}, expect_boost_domains=(),
        expect_eoc=True, expect_fence=True, expect_host_stuck=True, sim_timeout_s=900,
    ),
    "s25": dict(
        desc="as s16 with the boost level and the capacity policy: the type-1 survivor's domain boosts",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_cerf_l1",
        workload="moe2_2cluster", fault_stall_cycles=0,
        fault_gid=MOE2_FAULT_GID, victim=(0, 0, 0), cerf_fallback=True,
        # capacity policy, one boosted domain per lost core, no minimum load
        extra_flags="-DBINGO_MOE_EXPECT_EXPERT=1 "
                    "-DBINGO_PM_BOOST_POWER_LEVEL=3 -DBINGO_BOOST_POLICY=0x101",
        expect_core_types={0: MOE2_CORE_TYPE, 1: 2, 2: 0}, expect_boost_domains=(2,),
        expect_eoc=True, expect_fence=True, expect_host_stuck=True, sim_timeout_s=900,
    ),
    "s26": dict(
        desc="plain core 1 hangs, L3 import rejects: CERF switches to core 2, join and EOC complete",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=REJECT_CFG_KEYS, cfg_suffix="_rlink_reject",
        workload="int32_add_2plain_cerf_1cluster", fault_stall_cycles=0,
        fault_gid=PLAIN_CERF_FAULT_GID, victim=TWO_PLAIN_VICTIM,
        remote_reject=True, plain_cerf_fallback=True,
        extra_flags="-DBINGO_ADD_EXPECT_BRANCH=1", expect_core_types=TWO_PLAIN_CORE_TYPES,
        expect_eoc=True, expect_fence=True, expect_host_stuck=True, sim_timeout_s=900,
    ),
    "s27": dict(
        desc="healthy int32 CERF add: core 1 runs, ordered core 2 backup is skipped",
        cfg=ONE_CLUSTER_CFG, cluster_swap=TWO_PLAIN_CLUSTER_SWAP,
        timeout_cycles=TIGHT_TIMEOUT_CYCLES, confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=REJECT_CFG_KEYS, cfg_suffix="_rlink_reject",
        workload="int32_add_2plain_cerf_1cluster", fault_stall_cycles=None,
        victim=TWO_PLAIN_VICTIM, plain_cerf_fallback=True,
        extra_flags="-DBINGO_ADD_EXPECT_BRANCH=0", expect_core_types=TWO_PLAIN_CORE_TYPES,
        expect_eoc=True, sim_timeout_s=900,
    ),
    "s28": dict(
        desc="both DM cores die on copy 0 (replay, then stuck): CERF to the host copy, EOC",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_cerf_2cluster", fault_stall_cycles=0,
        fault_gid=DMA_CERF_FAULT_GID, fault_any_core=True,
        victim=VICTIM, substitute=1, substitute_cluster=1, dma_cerf=True,
        extra_flags="-DBINGO_DMA_EXPECT_BRANCH=1",
        expect_core_types={0: MOE2_CORE_TYPE, 1: DMA_CERF_CORE_TYPE, 2: 0},
        expect_wd_other={(0, 1, 1): [(1, 0), (1, 1)]}, expect_replay_stuck=True,
        expect_eoc=True, expect_fence=True, expect_host_stuck=True, sim_timeout_s=1200,
    ),
    "s29": dict(
        desc="only the cluster-0 DM core dies on copy 0: replay completes, no CERF fallback",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_cerf_2cluster", fault_stall_cycles=0,
        fault_gid=DMA_CERF_FAULT_GID,
        victim=VICTIM, substitute=1, substitute_cluster=1, dma_cerf=True,
        extra_flags="-DBINGO_DMA_EXPECT_BRANCH=0",
        expect_core_types={0: MOE2_CORE_TYPE, 1: DMA_CERF_CORE_TYPE, 2: 0},
        expect_takeover=True,
        expect_eoc=True, expect_fence=True, sim_timeout_s=1200,
    ),
    # s30-s33 remain reserved for the parked C2 evaluation.
    "s34": dict(
        desc="automatic iDMA host fallback: both DM cores die, type 2 switches to the same-output host copy",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_host_fallback_2cluster", fault_stall_cycles=0,
        fault_gid=HOST_FALLBACK_FAULT_GID, fault_any_core=True,
        victim=VICTIM, substitute=1, substitute_cluster=1, host_fallback=True,
        extra_flags="-DBINGO_DMA_EXPECT_BRANCH=1",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_wd_other={(0, 1, 1): [(1, 0), (1, 1)]},
        expect_replay_stuck=True, expect_host_stuck=True,
        expect_eoc=True, expect_fence=True, sim_timeout_s=1200,
    ),
    "s35": dict(
        desc="automatic iDMA host fallback control: only cluster 0 dies, cluster 1 replays, no host copy",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_host_fallback_2cluster", fault_stall_cycles=0,
        fault_gid=HOST_FALLBACK_FAULT_GID,
        victim=VICTIM, substitute=1, substitute_cluster=1, host_fallback=True,
        extra_flags="-DBINGO_DMA_EXPECT_BRANCH=0",
        expect_core_types={0: 1, 1: 2, 2: 0}, expect_takeover=True,
        expect_eoc=True, expect_fence=True, sim_timeout_s=1200,
    ),
    "s36": dict(
        desc="healthy automatic iDMA host fallback: no watchdog, replay or CERF event, no host copy",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_host_fallback_2cluster", fault_stall_cycles=None,
        host_fallback=True, extra_flags="-DBINGO_DMA_EXPECT_BRANCH=0",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_eoc=True, sim_timeout_s=1200,
    ),
    "s37": dict(
        desc="in-place add stalls after writing once; replay protection triggers separate-output CERF backup",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="int32_inplace_cerf_2cluster", replay_safety=True,
        fault_stall_cycles=0, fault_gid=INPLACE_FAULT_GID, victim=VICTIM,
        extra_flags="-DBINGO_WD_FAULT_AFTER_KERNEL=1 -DBINGO_INPLACE_EXPECT_BRANCH=1",
        expect_core_types={0: 1, 1: 2, 2: 0}, expect_fence=True,
        expect_replay_stuck=True, expect_host_stuck=True,
        expect_eoc=True, sim_timeout_s=1200,
    ),
    "s38": dict(
        desc="unsafe replay negative control: in-place add executes twice",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="int32_inplace_cerf_2cluster", replay_safety=True, unsafe_replay=True,
        fault_stall_cycles=0, fault_gid=INPLACE_FAULT_GID,
        victim=VICTIM, substitute=1, substitute_cluster=1, expect_takeover=True,
        extra_flags="-DBINGO_WD_FAULT_AFTER_KERNEL=1 -DBINGO_INPLACE_UNSAFE_REPLAY=1 "
                    "-DBINGO_INPLACE_EXPECT_BRANCH=0",
        expect_core_types={0: 1, 1: 2, 2: 0}, expect_fence=True,
        expect_eoc=True, sim_timeout_s=1200,
    ),
    "s39": dict(
        desc="healthy protected in-place add: one accumulation and no backup",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="int32_inplace_cerf_2cluster", replay_safety=True,
        fault_stall_cycles=None, extra_flags="-DBINGO_INPLACE_EXPECT_BRANCH=0",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_eoc=True, sim_timeout_s=1200,
    ),
    "s40": dict(
        desc="registered risk, R=0: confirm at the unchanged C threshold",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=0, fault_gid=CHAIN_FAULT_GID,
        victim=VICTIM, substitute=1, substitute_cluster=1, expect_takeover=True,
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=2 -DBINGO_RISK_CONFIRM=0",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_late=3, expect_risk=True, expect_final_risk=0, risk_confirm=0,
        expect_eoc=True, expect_fence=True, sim_timeout_s=900,
    ),
    "s41": dict(
        desc="registered risk, R=125000: earlier fence and level-2 replay",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=0, fault_gid=CHAIN_FAULT_GID,
        victim=VICTIM, substitute=1, substitute_cluster=1, expect_takeover=True,
        extra_flags=CHAIN_FLAGS + " -DBINGO_RISK_POLICY=2 -DBINGO_RISK_CONFIRM=125000",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_late=3, expect_risk=True, expect_final_risk=0, risk_confirm=125000,
        expect_eoc=True, expect_fence=True, sim_timeout_s=900,
    ),
    "s43": dict(
        desc="GEMM2 hangs: publish the already computed shallow logits via CERF",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_cerf_l1",
        workload="early_exit_2cluster", fault_stall_cycles=0,
        victim=(0, 0, 0), early_exit=True,
        extra_flags="-DBINGO_EE_EXPECT_SHALLOW=1",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_eoc=True, expect_fence=True, expect_host_stuck=True, sim_timeout_s=900,
    ),
    "s44": dict(
        desc="healthy GEMM chain: deep output, no shallow copy dispatch",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_cerf_l1",
        workload="early_exit_2cluster", fault_stall_cycles=None,
        victim=(0, 0, 0), early_exit=True,
        extra_flags="-DBINGO_EE_EXPECT_SHALLOW=0",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_eoc=True, sim_timeout_s=900,
    ),
    "s42": dict(
        desc="healthy registered-risk chain: progress strictly between H and R",
        cfg=HEMAIA_CI_CFG, timeout_cycles=TIGHT_TIMEOUT_CYCLES,
        confirm_timeout_cycles=CONFIRM_TIMEOUT_CYCLES,
        extra_cfg=L2_CFG_KEYS, cfg_suffix="_l2",
        workload="dma_chain_2cluster", fault_stall_cycles=100000, fault_gid=999,
        victim=VICTIM,
        # s18/s20 measured ~1.2001 ms per 100k core cycles at 28 ns/quad tick.
        # 260k -> ~111430 ticks, >1.05*H and <0.95*R. Check actual intervals.
        extra_flags="-DBINGO_WD_FAULT_PRE_STALL_CYCLES=260000 -DBINGO_RISK_LATE=30000 "
                    "-DBINGO_RISK_POLICY=2 -DBINGO_RISK_CONFIRM=125000",
        expect_core_types={0: 1, 1: 2, 2: 0},
        expect_chain_clusters=[0] * 6,
        expect_late=7, expect_risk=True, expect_final_risk=2, risk_confirm=125000,
        healthy_risk_confirm=True, expect_wd=[(1, 0), (0, 0)] * 7,
        expect_eoc=True, expect_fence=False, sim_timeout_s=900,
    ),
}

T1_FAMILIES = {
    "F1": ("tch0", "t18", "t19", "t40", "t41", "t42"),
    "F2": ("t43", "t44"),
}
for _number in (18, 19, 40, 41, 42, 43, 44):
    SCENARIOS[f"t{_number}"] = dict(SCENARIOS[f"s{_number}"], t1=True)
SCENARIOS["tch0"] = dict(
    SCENARIOS["s18"], t1=True, desc="healthy test-configuration DMA chain, all controls off",
    fault_stall_cycles=None, extra_flags="", expect_late=0, expect_fence=False,
    expect_takeover=False, substitute=None, expect_chain_clusters=[0] * 6, healthy_test_cfg=True,
    dispatch_log=True,
)
SCENARIOS["tch3"] = dict(
    SCENARIOS["s18"], t1=True, dispatch_log=True, extra_flags="", expect_late=0,
    desc="DMA task 3 hangs without PRE_STALL or risk controls; parameter watchdog baseline",
)
for _name, _baseline, _type in (("t50", "tch3", 2), ("t51", "tch0", 2), ("t52", "tch3", 1)):
    _h, _c = [0] * 16, [0] * 16
    _h[_type], _c[_type] = 20000, 40000
    SCENARIOS[_name] = dict(
        SCENARIOS[_baseline], wd_type_h=_h, wd_type_c=_c,
        desc=f"{_baseline} with type {_type} watchdog thresholds 20k/40k",
        expect_type_confirm=(_name == "t50"),
    )
A5_FAMILY = ("tch0", "tch3", "t50", "t51", "t52")

for _name, _sample, _fault in (("t45", 1, False), ("t46", 0, False),
                              ("t47", 0, True), ("t48", 1, True)):
    SCENARIOS[_name] = dict(
        SCENARIOS["t44"], early_exit=False, early_exit_conf=True,
        workload="early_exit_conf_2cluster", extra_flags="", t1_user=[_sample, 0, 0, 0],
        fault_stall_cycles=0 if _fault else None,
        fault_slot_from_gemm2=True,
        expect_fence=(_name == "t47"), expect_host_stuck=(_name == "t47"),
        expect_wd=[(1, 0), (1, 1)] if _name == "t47" else [],
        desc=f"confidence early exit sample {_sample}, deep fault={_fault}",
    )
SCENARIOS["t48"]["early_exit_conf_pair"] = "t45"
SCENARIOS["t48"]["same_as"] = "t45"
T1_FAMILIES["F3"] = ("t45", "t46", "t47", "t48")
for _name, _baseline in (("t53", "s34"), ("t54", "s35"), ("t55", "s36")):
    SCENARIOS[_name] = dict(
        SCENARIOS[_baseline], t1=True, workload="add_host_fallback_2cluster",
        add_host_fallback=True, extra_flags="",
        desc=f"automatic int32 add host fallback ({_baseline} fault configuration)",
    )
T1_FAMILIES["F4"] = ("t53", "t54", "t55")

for _name, _baseline in (("t60", "t18"), ("t61", "t19"), ("t62", "t43")):
    SCENARIOS[_name] = dict(SCENARIOS[_baseline], evlog_enable=0)
    SCENARIOS[_name + "e"] = dict(SCENARIOS[_name], evlog_enable=1)
T1_FAMILIES["A6_F1"] = ("t60", "t60e", "t61", "t61e")
T1_FAMILIES["A6_F2"] = ("t62", "t62e")
SCENARIOS["t64"] = dict(SCENARIOS["tch3"], evlog_enable=1, evlog_pair=False,
                       recovery_hold=0, p8_recovery=True)
SCENARIOS["t64h"] = dict(SCENARIOS["t64"], recovery_hold_from="t64")
for _name, _hold, _level in (("t65a", 1000, 0), ("t65s", 1000, 12), ("t65o", 0, 0)):
    SCENARIOS[_name] = dict(SCENARIOS["tch0"], pm_access_level=_level,
                           extra_flags=f"-DBINGO_PM_ACCESS_WAKE_HOLD={_hold}",
                           p8_access=True)
T1_FAMILIES["P8_F1"] = ("t64", "t64h", "t65a", "t65s", "t65o")

def c2_scenarios(calibration=None) -> Dict[str, dict]:
    """Generate exactly the C2.4 matrix from the accepted workload templates."""
    calibration = calibration or {}
    scenes = {}
    for label, template in (("chain", "tch0"), ("dummy", "s1"), ("moe2", "s17"),
                            ("dmafb", "s36")):
        base = dict(SCENARIOS[template], t1=True, c2=True, fault_stall_cycles=None,
                    dispatch_log=True, evlog_enable=1, evlog_pair=False, expect_eoc=True,
                    timeout_cycles=None, confirm_timeout_cycles=None, extra_cfg=None,
                    cfg_suffix="", c2_workload=label)
        variants = ("off", "nofb") if label == "dmafb" else ("off", "hw", "on", "nohb")
        if label == "moe2":
            variants += ("nofb",)
        k = calibration.get(label, 1)
        if not isinstance(k, int) or not 1 <= k <= 10:
            raise ValueError(f"C2 calibration k outside 1..10: {label}={k}")
        for variant in variants:
            scene = dict(base, c2_variant=variant,
                         c2_part=1 if variant in ("off", "hw", "on") and label != "dmafb"
                         else 2 if variant == "nohb" else 3,
                         desc=f"C2 no-fault {label} {variant}")
            if variant in ("hw", "on"):
                scene.update(timeout_cycles=100000 * k, confirm_timeout_cycles=200000 * k,
                             extra_cfg=dict(L2_CFG_KEYS), cfg_suffix="_l2")
            if variant == "on":
                h, c = [0] * 16, [0] * 16
                h[2], c[2] = 20000 * k, 40000 * k
                scene.update(wd_type_h=h, wd_type_c=c, recovery_hold=1000)
                controls = {
                    "BINGO_RISK_LATE": 30000 * k, "BINGO_RISK_POLICY": 0x02,
                    "BINGO_RISK_CONFIRM": 125000 * k, "BINGO_PM_BOOST_POWER_LEVEL": 3,
                    "BINGO_BOOST_POLICY": 0x10101, "BINGO_CERF_FB_CLUSTER": 0,
                    "BINGO_CERF_FB_CORE": 1, "BINGO_CERF_FB_CLEAR": 31,
                    "BINGO_CERF_FB_SET": 30,
                }
                scene["extra_flags"] = (scene.get("extra_flags", "") + " " +
                                        " ".join(f"-D{name}={value}" for name, value in controls.items())).strip()
            elif variant == "nohb":
                scene["image_flags"] = "-DBINGO_WD_NO_HEARTBEAT"
            elif variant == "nofb":
                scene["workload"] = {"moe2": "moe2_nofb_2cluster",
                                     "dmafb": "dma_host_nofb_2cluster"}[label]
                scene.pop("cerf_fallback", None)
                scene.pop("host_fallback", None)
            scenes[f"c2_{label}_{variant}"] = scene
    return scenes


SCENARIOS.update(c2_scenarios())

TEST_CFG_FIELDS = (
    "magic", "version", "fault_gid", "fault_stall_cycles", "fault_cluster", "fault_core",
    "fault_pre_stall_cycles", "fault_after_kernel", "risk_late", "risk_policy",
    "risk_epoch", "risk_confirm", "pm_boost_power_level", "boost_policy",
    "pm_idle_entry_delay", "pm_access_wake_hold", "remote_proxy_timeout", "park_req",
    "cerf_fb_enable", "cerf_fb_cluster", "cerf_fb_core", "cerf_fb_clear", "cerf_fb_set",
)
TEST_CFG_MACROS = {
    "BINGO_WD_FAULT_GID": "fault_gid",
    "BINGO_WD_FAULT_STALL_CYCLES": "fault_stall_cycles",
    "BINGO_WD_FAULT_CLUSTER": "fault_cluster", "BINGO_WD_FAULT_CORE": "fault_core",
    "BINGO_WD_FAULT_PRE_STALL_CYCLES": "fault_pre_stall_cycles",
    "BINGO_WD_FAULT_AFTER_KERNEL": "fault_after_kernel",
    "BINGO_RISK_LATE": "risk_late", "BINGO_RISK_POLICY": "risk_policy",
    "BINGO_RISK_EPOCH": "risk_epoch", "BINGO_RISK_CONFIRM": "risk_confirm",
    "BINGO_PM_BOOST_POWER_LEVEL": "pm_boost_power_level", "BINGO_BOOST_POLICY": "boost_policy",
    "BINGO_PM_IDLE_ENTRY_DELAY": "pm_idle_entry_delay",
    "BINGO_PM_ACCESS_WAKE_HOLD": "pm_access_wake_hold",
    "BINGO_REMOTE_PROXY_TIMEOUT": "remote_proxy_timeout", "BINGO_PARK_REQ": "park_req",
    "BINGO_CERF_FB_CLUSTER": "cerf_fb_cluster", "BINGO_CERF_FB_CORE": "cerf_fb_core",
    "BINGO_CERF_FB_CLEAR": "cerf_fb_clear", "BINGO_CERF_FB_SET": "cerf_fb_set",
    "BINGO_EVLOG_ENABLE": "evlog_enable",
    "BINGO_RECOVERY_HOLD": "recovery_hold", "BINGO_PM_ACCESS_LEVEL": "pm_access_level",
}


def test_cfg_defaults(version: int = 2) -> dict:
    if version not in (1, 2):
        raise ValueError("unsupported test configuration version")
    cfg = dict.fromkeys(TEST_CFG_FIELDS, 0)
    cfg.update(magic=0x42475431, version=version, fault_gid=0xFFFFFFFF,
               fault_cluster=0xFFFFFFFF, fault_core=0xFFFFFFFF, user=[0] * 4, evlog_enable=0,
               recovery_hold=0, pm_access_level=0)
    if version == 2:
        cfg.update(wd_type_h=[0] * 16, wd_type_c=[0] * 16)
    return cfg


def check_same_ps_fault_cfg(first: dict, second: dict) -> None:
    """Only the GID may differ in a declared same-image timing pair."""
    fields = ("fault_cluster", "fault_core", "fault_stall_cycles",
              "fault_pre_stall_cycles", "fault_after_kernel")
    different = [field for field in fields if first[field] != second[field]]
    if different:
        raise ValueError("same-ps pair has different fault fields: " + ", ".join(different))


def scenario_test_cfg(sc: dict, extra_flags: str = "", *, check_pair: bool = True) -> dict:
    """Translate only data controls. Unknown flags must never change a T1 graph."""
    cfg = test_cfg_defaults()
    cfg["evlog_enable"] = sc.get("evlog_enable", 0)
    cfg["recovery_hold"] = sc.get("recovery_hold", 0)
    cfg["pm_access_level"] = sc.get("pm_access_level", 0)
    if "t1_user" in sc:
        cfg["user"] = list(sc["t1_user"])
    for field in ("wd_type_h", "wd_type_c"):
        if field in sc:
            cfg[field] = list(sc[field])
    if sc["fault_stall_cycles"] is not None:
        cfg.update(fault_gid=sc.get("fault_gid", FAULT_GID),
                   fault_stall_cycles=sc["fault_stall_cycles"])
    if (sc["fault_stall_cycles"] is not None or sc.get("fault_slot_from_gemm2")) and not sc.get("fault_any_core"):
        _, core, cluster = sc.get("victim", VICTIM)
        cfg.update(fault_cluster=cluster, fault_core=core)
    for flag in shlex.split(sc.get("extra_flags", "") + " " + extra_flags):
        match = re.fullmatch(r"-D([A-Za-z_]\w*)(?:=(.+))?", flag)
        if not match:
            raise ValueError(f"unsupported T1 build flag: {flag}")
        macro, value = match.groups()
        if re.fullmatch(r"BINGO_\w+_EXPECT_\w+", macro):
            continue
        if macro not in TEST_CFG_MACROS:
            raise ValueError(f"unknown T1 configuration macro: {macro}")
        number = int((value or "1").rstrip("uUlL"), 0)
        if not -1 <= number <= 0xFFFFFFFF:
            raise ValueError(f"T1 value outside uint32: {flag}")
        cfg[TEST_CFG_MACROS[macro]] = number & 0xFFFFFFFF
        if macro.startswith("BINGO_CERF_FB_"):
            cfg["cerf_fb_enable"] = 1
    test_cfg_bytes(cfg)
    if check_pair and sc.get("same_as"):
        reference = SCENARIOS[sc["same_as"]]
        if not sc.get("t1") or not reference.get("t1"):
            raise ValueError("same-ps configuration pairs require T1 scenarios")
        check_same_ps_fault_cfg(cfg, scenario_test_cfg(reference, extra_flags, check_pair=False))
    return cfg


def t1_image_flags(sc: dict) -> str:
    value = sc.get("image_flags", "")
    if not isinstance(value, str):
        raise ValueError("T1 image_flags must be a flag string")
    flags = shlex.split(value)
    if set(flags) - {"-DBINGO_WD_NO_HEARTBEAT"}:
        raise ValueError("unsupported T1 image_flags")
    if flags and (not sc.get("t1") or sc.get("same_as") or any(
            sc is SCENARIOS.get(name) for names in T1_FAMILIES.values() for name in names)):
        raise ValueError("image_flags must not belong to a same-image family")
    return " ".join(flags)


def test_cfg_bytes(cfg: dict) -> bytes:
    version = cfg["version"]
    if (version not in (1, 2) or set(cfg) - set(TEST_CFG_FIELDS) - {
            "user", "evlog_enable", "recovery_hold", "pm_access_level", "wd_type_h", "wd_type_c"}
            or version == 1 and any(field in cfg for field in ("wd_type_h", "wd_type_c"))):
        raise ValueError("unsupported test configuration layout")
    user = cfg.get("user", [0] * 4)
    if len(user) != 4 or any(not isinstance(v, int) or not 0 <= v <= 0xFFFFFFFF for v in user):
        raise ValueError("user must have four uint32 words")
    evlog = cfg.get("evlog_enable", 0)
    if not isinstance(evlog, int) or not 0 <= evlog <= 0xFFFFFFFF:
        raise ValueError("evlog_enable must be a uint32 word")
    p8 = [cfg.get(field, 0) for field in ("recovery_hold", "pm_access_level")]
    if any(not isinstance(value, int) or not 0 <= value <= 0xFFFFFFFF for value in p8):
        raise ValueError("recovery_hold and pm_access_level must be uint32 words")
    words = [cfg[field] for field in TEST_CFG_FIELDS] + list(user) + [evlog] + p8 + [0] * 2
    if version == 2:
        for field in ("wd_type_h", "wd_type_c"):
            values = cfg.get(field, [0] * 16)
            if len(values) != 16 or any(not isinstance(v, int) or not 0 <= v <= 0xFFFFFFFF for v in values):
                raise ValueError(f"{field} must have sixteen words")
            words += values
    return struct.pack("<" + "I" * len(words), *words)


def test_cfg_elf_location(elf: Path) -> dict:
    """Read the RV64 ELF's symbol and actual file-backed LOAD geometry."""
    data = elf.read_bytes()
    if data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("test configuration requires a little-endian ELF64")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    phoff, shoff, phsize, phnum, shsize, shnum = (
        header[5], header[6], header[9], header[10], header[11], header[12])
    loads = [struct.unpack_from("<IIQQQQQQ", data, phoff + i * phsize)
             for i in range(phnum)]
    loads = [p for p in loads if p[0] == 1 and p[5]]
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, shoff + i * shsize)
                for i in range(shnum)]
    symbols = []
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        strings = sections[section[6]]
        names = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name, info, other, index, address, size = struct.unpack_from("<IBBHQQ", data, offset)
            if names[name:].split(b"\0", 1)[0] == b"bingo_test_cfg":
                symbols.append((index, address, size))
    if len(symbols) != 1 or not loads:
        raise ValueError("expected one initialized bingo_test_cfg symbol and a LOAD segment")
    index, address, size = symbols[0]
    if index >= len(sections) or sections[index][1] != 1 or (sections[index][2] & 3) != 3:
        raise ValueError("bingo_test_cfg is not in initialized writable image data")
    containing = [p for p in loads if p[3] <= address and address + size <= p[3] + p[5]]
    if len(containing) != 1:
        raise ValueError("bingo_test_cfg is not entirely file-backed by a LOAD segment")
    if any(p[3] != p[4] for p in loads):
        raise ValueError("unsupported LOAD virtual/physical address mapping")
    base = min(p[4] for p in loads)
    offset = address - base
    if size not in (128, 256) or address % 128 or offset % 128:
        raise ValueError("test configuration must occupy aligned complete bank rows")
    return dict(address=address, load_base=base, offset=offset, size=size)


def read_bank_image(directory: Path) -> bytes:
    banks = [(directory / f"bank_{bank}.hex").read_text().splitlines()
             for bank in range(16)]
    if len({len(bank) for bank in banks}) != 1:
        raise ValueError("unequal bank image lengths")
    return b"".join(int(banks[bank][row], 16).to_bytes(8, "little")
                    for row in range(len(banks[0])) for bank in range(16))


def patch_test_cfg(directory: Path, location: dict, cfg: dict) -> dict:
    """Preserve every unchanged bank line, including its original formatting."""
    before = read_bank_image(directory)
    offset = location["offset"]
    replacement = test_cfg_bytes(cfg)
    if len(replacement) != location["size"] or offset + len(replacement) > len(before):
        raise ValueError("test configuration is outside the staged image or has the wrong size")
    if struct.unpack_from("<II", before, offset) != (0x42475431, cfg["version"]):
        raise ValueError("staged test configuration magic/version mismatch")
    changed = []
    for bank in range(16):
        path = directory / f"bank_{bank}.hex"
        lines = path.read_text().splitlines(keepends=True)
        for local in range(bank * 8, len(replacement), 128):
            row = (offset + local) // 128
            word = int.from_bytes(replacement[local:local + 8], "little")
            if int(lines[row], 16) != word:
                lines[row] = f"{word:08X}\n"
                changed.append([bank, row])
        if any(b == bank for b, row in changed):
            path.write_text("".join(lines))
    after = read_bank_image(directory)
    if before[:offset] != after[:offset] or before[offset + len(replacement):] != after[offset + len(replacement):]:
        raise ValueError("patch changed data outside the test configuration")
    if after[offset:offset + len(replacement)] != replacement:
        raise ValueError("test configuration patch did not round-trip")
    return dict(image_id=hashlib.sha256(before).hexdigest(), configuration=cfg,
                location=location, changed_bank_lines=changed, changed_bank_line_count=len(changed),
                changed_rows=sorted({row for bank, row in changed}), PYTHONHASHSEED="0")


def check_t1_family(records: List[dict]) -> None:
    if len({record["image_id"] for record in records}) != 1:
        raise ValueError("T1 family does not share one unpatched image ID")
    if len({json.dumps(record["location"], sort_keys=True) for record in records}) != 1:
        raise ValueError("T1 family configuration locations differ")
    for record in records:
        first = record["location"]["offset"] // 128
        count = record["location"]["size"] // 128
        if not set(record["changed_rows"]) <= set(range(first, first + count)):
            raise ValueError("T1 family patch escaped its configuration rows")

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
PARK_RE = re.compile(r"\[BINGO_PARK\] \d+ chip=(\d+) core=(\d+) cluster=(\d+) (HOLD|FAIL|dropped, fenced|PARKED -> core=(\d+) cluster=(\d+)|UNPARKED|UNPARK \(\d+ tasks left elsewhere\))")
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
DONE_RE = re.compile(r"\[BINGO_DONE\] (\d+) chip=(\d+) task=(\d+) core=(\d+) cluster=(\d+)")
TYPE_CONFIRM_RE = re.compile(
    r"\[BINGO_TYPE_CONFIRM\] (\d+) core=(\d+) cluster=(\d+) type=(\d+) threshold=(\d+)")


def dispatch_host_slot() -> Tuple[int, int, int]:
    """Read the host slot, checking the generated watchdog mask excludes it."""
    header = (_REPO_ROOT / "target/sw/shared/platform/generated/occamy.h").read_text()
    cores = re.findall(r"^#define\s+N_CORES_PER_CLUSTER\s+(\d+)\s*$", header, re.M)
    rtl = (_REPO_ROOT / QUAD_CTRL_RTL).read_text()
    mask = ("{{NrClustersPerQuad{1'b0}}, "
            "{((BINGO_HW_MANAGER_NR_CORE_PER_CLUSTER-1)*NrClustersPerQuad){1'b1}}}")
    if len(cores) != 1 or mask not in rtl:
        raise ValueError("cannot confirm generated host slot and watchdog mask")
    return (0, int(cores[0]), 0)


def dispatch_done_pairs(log_text: str, allowed_pending=(), *, graph_csv=None,
                        host_slot=None, checker_passed=False, pairing_evidence=None) -> List[dict]:
    """Pair real busy transitions; only a checked terminal host exit may lack DONE.

    allowed_pending is the pre-existing, explicit permanently fenced fault
    expectation, not an exemption for other monitored-slot dispatches.
    """
    pending, pairs = {}, []
    for line in log_text.splitlines():
        dispatch, done = DISPATCH_RE.search(line), DONE_RE.search(line)
        match = dispatch or done
        if not match:
            continue
        time, chip, task, core, cluster = map(int, match.groups())
        slot = (chip, core, cluster)
        if dispatch:
            if slot in pending:
                raise ValueError(f"two dispatches without done on slot {slot}")
            pending[slot] = (time, task)
        else:
            if slot not in pending:
                raise ValueError(f"done without dispatch on slot {slot}")
            start, dispatched = pending.pop(slot)
            if dispatched != task or time <= start:
                raise ValueError(f"done does not match dispatched task on slot {slot}")
            pairs.append(dict(chip=chip, core=core, cluster=cluster, task=task,
                              dispatch_ps=start, done_ps=time))
    unresolved = {(*slot, task) for slot, (time, task) in pending.items()}
    expected_faults = set(allowed_pending)
    if not expected_faults <= unresolved:
        raise ValueError(f"unpaired dispatches {sorted(unresolved)}")
    terminal = unresolved - expected_faults
    if terminal:
        if len(terminal) != 1 or host_slot is None or graph_csv is None:
            raise ValueError(f"unpaired dispatches {sorted(unresolved)}")
        chip, core, cluster, task = next(iter(terminal))
        if (chip, core, cluster) != host_slot:
            raise ValueError(f"unpaired device dispatch {(chip, core, cluster, task)}")
        with Path(graph_csv).open(newline="") as stream:
            rows = [row for row in csv.DictReader(stream) if int(row["ID"]) == task]
        if len(rows) != 1 or rows[0]["Kernel"] != "__host_bingo_kernel_exit" or (
                int(rows[0]["Chiplet"], 16), int(rows[0]["Core"]), int(rows[0]["Cluster"])) != host_slot:
            raise ValueError(f"unpaired host task {task} is not the generated host exit")
        # A subsequent dispatch on this slot already fails the alternating
        # transition check above, so this pending dispatch is necessarily last.
        start = pending[host_slot][0]
        eocs = re.findall(r"All chips finished successfully at (\d+)", log_text)
        if (not checker_passed or len(eocs) != 1 or int(eocs[0]) <= start or
                SIM_ERR_MARKER in log_text):
            raise ValueError("unpaired host exit requires EOC and a passing checker")
        if pairing_evidence is not None:
            pairing_evidence.append(dict(chip=chip, core=core, cluster=cluster, task=task,
                                         dispatch_ps=start, kernel=rows[0]["Kernel"],
                                         graph_csv=str(graph_csv), terminal_host_exit=True))
    return pairs


def evaluate_type_threshold(sc: dict, log_text: str) -> List[str]:
    causes = [tuple(map(int, match.groups())) for match in TYPE_CONFIRM_RE.finditer(log_text)]
    if not sc.get("expect_type_confirm"):
        return ["unexpected type-confirm cause"] if causes else []
    chip, core, cluster = sc["victim"]
    fences = [int(m.group(1)) for m in WD_RE.finditer(log_text)
              if tuple(map(int, m.groups()[1:4])) == (chip, core, cluster) and m.group(6) == "1"]
    c = sc["wd_type_c"][2]
    if len(fences) != 1 or len(causes) != 1 or causes[0][1:] != (core, cluster, 2, c):
        return [f"wrong type-confirm cause: {causes}"]
    if causes[0][0] >= fences[0] or "[BINGO_RISK_CONFIRM]" in log_text:
        return ["type-confirm cause must precede fence status without an R cause"]
    return []


def evaluate_risk_chain(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Require the chain's actual placement, host risk bitmap and both goldens."""
    problems: List[str] = []
    chip, core, cluster = sc["victim"]
    sub, sub_cluster = sc.get("substitute", core), sc.get("substitute_cluster", cluster)
    parked = sc.get("expect_risk", False) and sc.get("park", False)
    dispatches = [tuple(map(int, m.groups())) for m in DISPATCH_RE.finditer(log_text)
                  if int(m.group(3)) < 6]
    expected = [(task, core, cluster) for task in range(2 if parked else 4)]
    expected += [(task, sub, sub_cluster) for task in range(2 if parked else 3, 6)]
    if "expect_chain_clusters" in sc:
        expected = [(task, core, cl) for task, cl in enumerate(sc["expect_chain_clusters"])]
    # The v1 hardware gate and G1b-off run intentionally omit dispatch logging.
    check_dispatches = sc.get("dispatch_log") or not sc.get("healthy_test_cfg") or bool(dispatches)
    if check_dispatches and ([(d[2], d[3], d[4]) for d in dispatches] != expected or any(
            d[1] != chip for d in dispatches)):
        problems.append(f"chain dispatches {dispatches}, expected {expected} on chip {chip}")
    if sc.get("expect_risk", False):
        risk_times = [int(t) for t in re.findall(
            r"\[BINGO_RISK\] (\d+) core=\d+ cluster=\d+ at risk", log_text)]
        task1 = [d[0] for d in dispatches if d[2] == 1]
        task2 = [d[0] for d in dispatches if d[2] == 2]
        if len(risk_times) != 1 or len(task1) != 1 or len(task2) != 1 or not (
                task1[0] < risk_times[0] < task2[0]):
            problems.append("risk must trip during task 1, before task 2 is dispatched")
    host = re.search(r"\[Host\] Bingo status: .* risk=0x([0-9a-fA-F]+)", uart_text)
    expected_risk = (1 << (core + HEMAIA_CI_SLOTS * cluster)) if sc.get("expect_risk") else 0
    expected_risk = sc.get("expect_final_risk", expected_risk)
    if not host or int(host.group(1), 16) != expected_risk:
        problems.append(f"host risk {host.group(1) if host else None}, expected 0x{expected_risk:x}")
    if sorted(CHECK_RE.findall(uart_text)) != [
            ("A_chain_cluster0", "PASS"), ("A_cluster1", "PASS")]:
        problems.append("expected both DMA-chain host checks to pass exactly once")
    return problems


def evaluate_risk_confirm(sc: dict, log_text: str) -> List[str]:
    """Check the R cause, actual timer latency and the healthy H..R window."""
    problems: List[str] = []
    chip, core, cluster = sc["victim"]
    r = sc["risk_confirm"]
    tick_ps = 28000  # measured quad-control tick in s18/s20, unchanged configuration
    h, c = sc["timeout_cycles"], sc["confirm_timeout_cycles"]
    causes = [tuple(map(int, m)) for m in re.findall(
        r"\[BINGO_RISK_CONFIRM\] (\d+) core=(\d+) cluster=(\d+) threshold=(\d+)",
        log_text)]
    wd = [tuple(map(int, m.groups())) for m in WD_RE.finditer(log_text)
          if tuple(map(int, m.groups()[1:4])) == (chip, core, cluster)]
    if sc.get("healthy_risk_confirm"):
        if causes or any(e[5] for e in wd) or "[BINGO_REPLAY]" in log_text:
            problems.append("healthy at-risk progress was fenced or replayed")
        dispatches = [tuple(map(int, m.groups())) for m in DISPATCH_RE.finditer(log_text)
                      if tuple(map(int, (m.group(2), m.group(4), m.group(5)))) ==
                      (chip, core, cluster)]
        late_times = [int(t) for t in re.findall(
            rf"\[BINGO_LATE\] (\d+) core={core} cluster={cluster}", log_text)]
        intervals = {}
        for d in dispatches:
            if d[2] not in range(6):
                continue
            next_dispatch = min((x[0] for x in dispatches if x[0] > d[0]), default=float("inf"))
            progress = [t for t in late_times if d[0] < t < next_dispatch]
            if len(progress) != 1:
                problems.append(f"copy {d[2]} lacks exactly one actual late progress beat")
                continue
            elapsed = progress[0] - d[0]
            intervals[d[2]] = elapsed / tick_ps
            if not 105*h*tick_ps <= 100*elapsed <= 95*r*tick_ps:
                problems.append(f"copy {d[2]} progress at {elapsed/tick_ps:.3f} ticks "
                                "outside the 5%-margin H..R window")
        if set(intervals) != set(range(6)):
            problems.append("missing actual progress intervals for the six healthy copies")
        print(f"[risk_confirm] healthy progress ticks={intervals}")
    else:
        suspects = [e[0] for e in wd if e[4:] == (1, 0)]
        fences = [e[0] for e in wd if e[4:] == (1, 1)]
        if len(suspects) != 1 or len(fences) != 1:
            problems.append("expected one suspicion and one fence of the victim")
        else:
            # The existing WD status logger observes timer changes one tick later.
            last_progress = suspects[0] - (h+1)*tick_ps
            elapsed = fences[0] - last_progress
            expected = r or c
            if abs(elapsed - expected*tick_ps) > expected*tick_ps//20:
                problems.append(f"last-progress-to-fence {elapsed/tick_ps:.3f} ticks "
                                f"not within 5% of {expected}")
            if r:
                if causes != [(fences[0]-tick_ps, core, cluster, r)]:
                    problems.append(f"wrong actual R fence cause: {causes}")
            elif causes:
                problems.append("R=0 must not print an R fence cause")
            print(f"[risk_confirm] inferred last progress={last_progress} "
                  f"fence={fences[0]} elapsed_ticks={elapsed/tick_ps:.3f} threshold={expected}")
    return problems


def evaluate_risk_controls(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Check decay, real CSR CLEAR, and fail-before-derate ordering."""
    problems: List[str] = []
    chip, core, cluster = sc["victim"]
    if sc.get("expect_decay"):
        decay = [tuple(map(int, m)) for m in re.findall(
            r"\[BINGO_RISK_DECAY\] (\d+) core=(\d+) cluster=(\d+) count=(\d+) -> count=(\d+)",
            log_text)]
        late_times = [int(t) for t in re.findall(
            rf"\[BINGO_LATE\] (\d+) core={core} cluster={cluster}", log_text)]
        if not decay or any(d[1:] != (core, cluster, 1, 0) for d in decay):
            problems.append(f"expected victim count 1 -> 0 decay, got {decay}")
        if any(not any(a < d[0] < b for d in decay)
               for a, b in zip(late_times, late_times[1:])):
            problems.append("missing count decay between consecutive late beats")
    elif "[BINGO_RISK_DECAY]" in log_text:
        problems.append("unexpected decay with epoch disabled")
    clear = [(int(t), int(mask, 16)) for t, mask in re.findall(
        r"\[BINGO_RISK_CLEAR\] (\d+) mask=0x([0-9a-fA-F]+)", log_text)]
    if sc.get("expect_unpark"):
        marker = re.findall(
            r"\[RiskChain\] CLEAR before=0x([0-9a-fA-F]+) mask=0x([0-9a-fA-F]+) "
            r"held=0x([0-9a-fA-F]+) after=0x([0-9a-fA-F]+)", uart_text)
        mask = 1 << (core + HEMAIA_CI_SLOTS * cluster)
        if [tuple(int(v, 16) for v in m) for m in marker] != [(mask, mask, mask, 0)]:
            problems.append(f"host CLEAR readbacks {marker}, expected {(mask, mask, mask, 0)}")
        unpark = [int(t) for t in re.findall(
            rf"\[BINGO_PARK\] (\d+) chip={chip} core={core} cluster={cluster} UNPARKED",
            log_text)]
        task2 = [int(t) for t in re.findall(
            rf"\[BINGO_DISPATCH\] (\d+) chip={chip} task=2 core={core} cluster=1", log_text)]
        task3 = [int(t) for t in re.findall(
            rf"\[BINGO_DISPATCH\] (\d+) chip={chip} task=3 core={core} cluster={cluster}", log_text)]
        if len(clear) != 1 or clear[0][1] != mask or len(unpark) != 1 or \
                len(task2) != 1 or len(task3) != 1 or not (
                    task2[0] < clear[0][0] < unpark[0] < task3[0]):
            problems.append("expected remapped copy 2 < CLEAR < UNPARKED < home copy 3")
    elif clear or "[RiskChain] CLEAR" in uart_text:
        problems.append("unexpected host risk CLEAR")
    if sc.get("expect_derate_level"):
        park = [m.groups()[:3] + (m.group(4),) for m in PARK_RE.finditer(log_text)]
        expected = [(str(chip), str(core), str(cluster), state) for state in ("HOLD", "FAIL")]
        if park != expected:
            problems.append(f"expected HOLD then FAIL without PARKED, got {park}")
        fail = [int(t) for t in re.findall(
            rf"\[BINGO_PARK\] (\d+) chip={chip} core={core} cluster={cluster} FAIL", log_text)]
        pm = [(int(t), int(level)) for t, level in re.findall(
            r"\[BINGO_PM\] (\d+) domain=1 level=(\d+)", log_text)]
        level = sc["expect_derate_level"]
        derated = [t for t, lvl in pm if lvl == level]
        if len(fail) != 1 or not derated or derated[0] <= fail[0] or \
                any(t > derated[0] and lvl < level for t, lvl in pm):
            problems.append(f"expected domain 1 to derate after FAIL and stay capped: {pm}")
        host = re.search(r"\[Host\] Bingo status: .* park_fail=0x([0-9a-fA-F]+)", uart_text)
        mask = 1 << (core + HEMAIA_CI_SLOTS * cluster)
        if not host or int(host.group(1), 16) != mask:
            problems.append("host park_fail must contain only the victim")
    elif not sc.get("park") and "[BINGO_PARK]" in log_text:
        problems.append("unexpected parking event")
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


def evaluate_plain_cerf_fallback(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Require rejection before fallback, the selected add, and an ordered join."""
    problems: List[str] = []
    fault = sc["fault_stall_cycles"] is not None
    branch = int(fault)
    fb = [tuple(map(int, m.groups())) for m in CERF_FB_RE.finditer(log_text)]
    if len(fb) != int(fault) or any(e[1:] != (TWO_PLAIN_CORE_TYPE, 0, 1) for e in fb):
        problems.append(f"plain CERF events {fb}, expected {'one type 2 g0 -> g1' if fault else 'none'}")
    dispatches = [tuple(map(int, m.groups())) for m in DISPATCH_RE.finditer(log_text)]
    # User tasks 0..5; task 8 is the compiler's g0 exit on the protected core.
    expected = {0: 4, 1: 3, 2: 3, 3: 1, 5: 4}
    expected[4 if fault else 8] = 2 if fault else 1
    selected = [d for d in dispatches if d[2] in set(range(6)) | {8}]
    if sorted(d[2] for d in selected) != sorted(expected):
        problems.append(f"plain branch dispatches {selected}, expected tasks {sorted(expected)} once")
    for d in selected:
        if d[2] not in expected or (d[1], d[3], d[4]) != (0, expected[d[2]], 0):
            problems.append(f"plain task on wrong physical slot: {d}")
    times = {d[2]: d[0] for d in selected}
    if all(task in times for task in (0, 1, 2, 3, 5)) and not (
            times[0] < times[1] < times[2] < times[3] < times[5]):
        problems.append("expected gating < both DM loads < primary add < host join")
    if fault and len(fb) == 1:
        rejected = [int(t) for t in re.findall(
            r"\[BINGO_REMOTE_REJECT_IN\] (\d+) chip=0 task=3 proxy_slot=1", log_text)]
        if len(rejected) != 1 or not rejected[0] < fb[0][0]:
            problems.append("expected proxy reject before CERF fallback")
        if not all(task in times for task in (3, 4, 5)) or not (
                times[3] < fb[0][0] < times[4] < times[5]):
            problems.append("expected primary add < CERF fallback < backup add < join")
    elif not fault:
        if "[BINGO_REMOTE_REJECT" in log_text:
            problems.append("unexpected remote reject in the healthy control")
        if 8 in times and 5 in times and times[8] <= times[5]:
            problems.append("protected exit dispatched before the host join")
    host = re.search(r"\[Host\] Bingo status: .* cerf=0x([0-9a-fA-F]+) "
                     r"cerf_fb_en=0x([0-9a-fA-F]+) cerf_fb_evt=0x([0-9a-fA-F]+)", uart_text)
    expected_status = (1 << branch, 1 << TWO_PLAIN_CORE_TYPE,
                       (1 << TWO_PLAIN_CORE_TYPE) if fault else 0)
    if not host or tuple(int(v, 16) for v in host.groups()) != expected_status:
        problems.append(f"host plain CERF/en/evt {host.groups() if host else None}, expected {expected_status}")
    if CHECK_RE.findall(uart_text) != [(f"C_branch_{branch}", "PASS")]:
        problems.append(f"expected only Check [C_branch_{branch}]: PASS")
    if f"[Int32AddCERF] gating selected g0; join complete; output branch {branch}" not in uart_text:
        problems.append("missing gating / join completion marker")
    print(f"[plain_cerf_fallback] events={fb} dispatches={selected}")
    return problems


def evaluate_dma_cerf(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Both DM cores die on copy 0 (s28: host fallback) or only the victim (s29: replay)."""
    problems: List[str] = []
    double = sc.get("fault_any_core", False)
    branch = int(double)
    fb = [tuple(map(int, m.groups())) for m in CERF_FB_RE.finditer(log_text)]
    if len(fb) != int(double) or any(e[1:] != (DMA_CERF_CORE_TYPE, 0, 1) for e in fb):
        problems.append(f"DMA CERF events {fb}, expected {'one type 2 g0 -> g1' if double else 'none'}")
    # task -> [(core, cluster, time)]; copies 1, 2, host copy 3, join 4, DM exits 7 and 9
    seen: Dict[int, list] = {}
    for m in DISPATCH_RE.finditer(log_text):
        t, _, task, core, cluster = map(int, m.groups())
        seen.setdefault(task, []).append((core, cluster, t))
    slots = {task: [(c, cl) for c, cl, _ in seen.get(task, [])] for task in (1, 2, 3, 4, 7, 9)}
    expected = {1: [(1, 0), (1, 1)], 4: [(2, 0)]}
    if double:
        # copy 1 and both DM exits are skipped; the substitute drains copy 0
        expected.update({2: [], 3: [(2, 0)], 7: [], 9: []})
    else:
        # the substitute runs copy 1 and the victim's exit, then its own
        expected.update({2: [(1, 1)], 3: [], 7: [(1, 1)], 9: [(1, 1)]})
    for task, want in expected.items():
        if slots[task] != want:
            problems.append(f"task {task} dispatched on (core, cluster) {slots[task]}, expected {want}")
    if double and len(fb) == 1 and seen.get(3) and seen.get(4):
        fence_sub = [int(m.group(1)) for m in WD_RE.finditer(log_text)
                     if (m.group(3), m.group(4), m.group(6)) == ("1", "1", "1")]
        if not fence_sub or not (fence_sub[0] <= fb[0][0] < seen[3][0][2] < seen[4][0][2]):
            problems.append("expected substitute fence <= CERF fallback < host copy < join")
    host = re.search(r"\[Host\] Bingo status: .* cerf=0x([0-9a-fA-F]+) "
                     r"cerf_fb_en=0x([0-9a-fA-F]+) cerf_fb_evt=0x([0-9a-fA-F]+)", uart_text)
    expected_status = (1 << branch, 1 << DMA_CERF_CORE_TYPE,
                       (1 << DMA_CERF_CORE_TYPE) if double else 0)
    if not host or tuple(int(v, 16) for v in host.groups()) != expected_status:
        problems.append(f"host DMA CERF/en/evt {host.groups() if host else None}, expected {expected_status}")
    if CHECK_RE.findall(uart_text) != [(f"A_branch_{branch}", "PASS")]:
        problems.append(f"expected only Check [A_branch_{branch}]: PASS")
    if f"[DmaCERF] gating selected g0; join complete; output branch {branch}" not in uart_text:
        problems.append("missing gating / join completion marker")
    print(f"[dma_cerf] events={fb} dispatches={slots}")
    return problems

def early_exit_task_ids(path: Path) -> dict:
    """Read the chain kernels and output stores from the generated graph."""
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    result = {}
    kernels = {
        "gemm1": ("__snax_bingo_kernel_gemm_full", 0, 1),
        "gemm2": ("__snax_bingo_kernel_gemm_full", 0, 0),
        "requant": ("__host_bingo_kernel_early_exit_requant", 2, 0),
        "backup": ("__host_bingo_kernel_idma", 2, 0),
        "join": ("__host_bingo_kernel_dummy", 2, 0),
        "primary_exit": ("__snax_bingo_kernel_exit", 0, 0),
        "prefix_exit": ("__snax_bingo_kernel_exit", 0, 1),
    }
    for name, (kernel, core, cluster) in kernels.items():
        matches = [row for row in rows if row["Kernel"] == kernel
                   and (int(row["Core"]), int(row["Cluster"])) == (core, cluster)]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} in generated early-exit graph")
        result[name] = int(matches[0]["ID"])
    for name, cluster, predecessor in (
        ("prefix_store", 1, "gemm1"), ("deep_store", 0, "gemm2"),
    ):
        matches = [row for row in rows if row["Kernel"] == "__snax_bingo_kernel_idma_1d_copy"
                   and int(row["Cluster"]) == cluster and int(row["ID"]) > result[predecessor]]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} after its GEMM in early-exit graph")
        result[name] = int(matches[0]["ID"])
    return result


def evaluate_early_exit(sc: dict, log_text: str, uart_text: str) -> List[str]:
    ids = sc.get("early_exit_task_ids")
    if not ids:
        return ["missing task IDs from generated early-exit graph"]
    shallow = sc["fault_stall_cycles"] is not None
    problems = []
    fb = [tuple(map(int, match.groups())) for match in CERF_FB_RE.finditer(log_text)]
    if len(fb) != int(shallow) or any(event[1:] != (1, 0, 1) for event in fb):
        problems.append("expected one type 1 g0 -> g1 only on a deep-layer fault")
    seen = {}
    for match in DISPATCH_RE.finditer(log_text):
        time, chip, task, core, cluster = map(int, match.groups())
        seen.setdefault(task, []).append((chip, core, cluster, time))
    expected = dict(gemm1=[(0, 0, 1)], prefix_store=[(0, 1, 1)],
                    requant=[(0, 2, 0)], gemm2=[(0, 0, 0)],
                    deep_store=[] if shallow else [(0, 1, 0)],
                    backup=[(0, 2, 0)] if shallow else [], join=[(0, 2, 0)],
                    primary_exit=[] if shallow else [(0, 0, 0)],
                    prefix_exit=[] if shallow else [(0, 0, 1)])
    for name, want in expected.items():
        if [event[:3] for event in seen.get(ids[name], [])] != want:
            problems.append(f"wrong early-exit {name} dispatch count or slot")
    if all(len(seen.get(ids[name], [])) == 1 for name in ("gemm1", "prefix_store", "requant", "gemm2")):
        times = [seen[ids[name]][0][3] for name in ("gemm1", "prefix_store", "requant", "gemm2")]
        if times != sorted(set(times)):
            problems.append("expected GEMM1 < store h < requant < GEMM2")
    if shallow:
        fences = [int(match.group(1)) for match in WD_RE.finditer(log_text)
                  if match.groups()[1:] == ("0", "0", "0", "1", "1")]
        stuck = [int(t) for t in re.findall(
            rf"\[BINGO_REPLAY_STUCK\] (\d+) chip=0 core=0 cluster=0: no live core may run task {ids['gemm2']} ", log_text)]
        if (len(fences) != 1 or len(stuck) != 1 or len(fb) != 1
                or len(seen.get(ids["backup"], [])) != 1 or len(seen.get(ids["join"], [])) != 1
                or not seen.get(ids["gemm2"])
                or not seen[ids["gemm2"]][0][3] < fences[0] <= stuck[0] <= fb[0][0]
                       < seen[ids["backup"]][0][3] < seen[ids["join"]][0][3]):
            problems.append("expected GEMM2 < fence <= stuck <= CERF < shallow copy < join")
    elif all(len(seen.get(ids[name], [])) == 1 for name in ("gemm2", "deep_store", "join")):
        times = [seen[ids[name]][0][3] for name in ("gemm2", "deep_store", "join")]
        if times != sorted(set(times)):
            problems.append("expected healthy GEMM2 < store deep output < join")
    host = re.search(r"\[Host\] Bingo status: .* cerf=0x([0-9a-fA-F]+) "
                     r"cerf_fb_en=0x([0-9a-fA-F]+) cerf_fb_evt=0x([0-9a-fA-F]+)", uart_text)
    if not host or tuple(int(value, 16) for value in host.groups()) != (2 if shallow else 1, 2, 2 if shallow else 0):
        problems.append("wrong early-exit CERF/enable/event status")
    if CHECK_RE.findall(uart_text) != [("shallow_out" if shallow else "deep_out", "PASS")]:
        problems.append("wrong early-exit output branch or data check")
    if f"[EarlyExit] join complete; shallow={int(shallow)}" not in uart_text:
        problems.append("missing early-exit branch completion marker")
    return problems


def early_exit_conf_task_ids(path: Path) -> dict:
    result = early_exit_task_ids(path)
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    for name, kernel in (("select", "__host_bingo_kernel_ee_select"),
                         ("gate", "__host_bingo_kernel_ee_conf_gate")):
        matches = [row for row in rows if row["Kernel"] == kernel
                   and (int(row["Core"]), int(row["Cluster"])) == (2, 0)]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} in confidence early-exit graph")
        result[name] = int(matches[0]["ID"])
    return result


def evaluate_early_exit_conf(sc: dict, log_text: str, uart_text: str) -> List[str]:
    ids, data = sc.get("early_exit_task_ids"), sc.get("early_exit_conf_data")
    if not ids or not data:
        return ["missing confidence graph IDs or generated margin data"]
    sample = sc["t1_user"][0]
    if sample not in (0, 1) or len(data["margins"]) != 2:
        return ["invalid confidence sample or margin data"]
    decision = int(data["margins"][sample] >= data["threshold"])
    faulty = sc["fault_stall_cycles"] is not None and not decision
    shallow = int(decision or faulty)
    problems = []
    marker = re.findall(r"\[EarlyExitConf\] sample=(\d+) decision=(\d+) fault_evt=(\d+) shallow=(\d+)",
                        uart_text)
    if [tuple(map(int, values)) for values in marker] != [(sample, decision, int(faulty), shallow)]:
        problems.append("confidence decision/branch disagrees with generated margin or fault event")
    fb = [tuple(map(int, match.groups())) for match in CERF_FB_RE.finditer(log_text)]
    if len(fb) != int(faulty) or any(event[1:] != (1, 0, 1) for event in fb):
        problems.append("expected one type 1 g0 -> g1 only for a dispatched deep fault")
    seen = {}
    for match in DISPATCH_RE.finditer(log_text):
        time, chip, task, core, cluster = map(int, match.groups())
        seen.setdefault(task, []).append((chip, core, cluster, time))
    expected = dict(select=[(0, 2, 0)], gemm1=[(0, 0, 1)], prefix_store=[(0, 1, 1)],
                    requant=[(0, 2, 0)], gate=[(0, 2, 0)],
                    gemm2=[] if decision else [(0, 0, 0)],
                    deep_store=[] if shallow else [(0, 1, 0)],
                    backup=[(0, 2, 0)] if shallow else [], join=[(0, 2, 0)],
                    primary_exit=[] if shallow else [(0, 0, 0)],
                    prefix_exit=[] if shallow else [(0, 0, 1)])
    for name, want in expected.items():
        if [event[:3] for event in seen.get(ids[name], [])] != want:
            problems.append(f"wrong confidence {name} dispatch count or slot")
    prefix = ("select", "gemm1", "prefix_store", "requant", "gate")
    if all(len(seen.get(ids[name], [])) == 1 for name in prefix):
        times = [seen[ids[name]][0][3] for name in prefix]
        if times != sorted(set(times)):
            problems.append("expected select < GEMM1 < store h < requant < confidence gate")
    if faulty:
        fences = [int(match.group(1)) for match in WD_RE.finditer(log_text)
                  if match.groups()[1:] == ("0", "0", "0", "1", "1")]
        stuck = [int(t) for t in re.findall(
            rf"\[BINGO_REPLAY_STUCK\] (\d+) chip=0 core=0 cluster=0: no live core may run task {ids['gemm2']} ",
            log_text)]
        ordered = ("gate", "gemm2", "backup", "join")
        if (len(fences) != 1 or len(stuck) != 1 or len(fb) != 1
                or not all(len(seen.get(ids[name], [])) == 1 for name in ordered)
                or not seen[ids["gate"]][0][3] < seen[ids["gemm2"]][0][3] < fences[0]
                       <= stuck[0] <= fb[0][0] < seen[ids["backup"]][0][3] < seen[ids["join"]][0][3]):
            problems.append("expected gate < GEMM2 < fence <= stuck <= CERF < shallow copy < join")
    else:
        branch = ("gate", "backup", "join") if shallow else ("gate", "gemm2", "deep_store", "join")
        if all(len(seen.get(ids[name], [])) == 1 for name in branch):
            times = [seen[ids[name]][0][3] for name in branch]
            if times != sorted(set(times)):
                problems.append("wrong confidence branch execution order")
        if WD_RE.search(log_text):
            problems.append("unexpected watchdog event without a dispatched deep fault")
    host = re.search(r"\[Host\] Bingo status: .* cerf=0x([0-9a-fA-F]+) "
                     r"cerf_fb_en=0x([0-9a-fA-F]+) cerf_fb_evt=0x([0-9a-fA-F]+)", uart_text)
    if not host or tuple(int(value, 16) for value in host.groups()) != (2 if shallow else 1, 2, 2 if faulty else 0):
        problems.append("wrong confidence CERF/enable/event status")
    if CHECK_RE.findall(uart_text) != [("shallow_out" if shallow else "deep_out", "PASS")]:
        problems.append("wrong confidence output branch or golden check")
    return problems


def check_early_exit_conf_pair(first_log: str, first_uart: str,
                               second_log: str, second_uart: str) -> List[str]:
    """The skipped deep fault must leave every event, UART byte and EOC unchanged."""
    def events(log):
        return [line[line.index("[BINGO_"):] for line in log.splitlines() if "[BINGO_" in line]
    first_eoc = re.findall(r"All chips finished successfully at (\d+)", first_log)
    second_eoc = re.findall(r"All chips finished successfully at (\d+)", second_log)
    if (events(first_log) != events(second_log) or first_uart != second_uart
            or len(first_eoc) != 1 or first_eoc != second_eoc):
        return ["t48 and t45 must have identical timed BINGO events, UART and EOC"]
    return []


def replay_safety_task_ids(path: Path) -> dict:
    """Read the unique workload kernels and protected exits from the compiled CSV."""
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    result = {}
    for name, kernel in {
        "copy": "__snax_bingo_kernel_idma_1d_copy",
        "add": "__snax_bingo_kernel_int32_add",
        "backup": "__host_bingo_kernel_add_i32",
        "check": "__host_bingo_kernel_check_result",
    }.items():
        matches = [row for row in rows if row["Kernel"] == kernel]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} in generated replay safety graph")
        result[name] = int(matches[0]["ID"])
    for name, cluster in (("primary_exit", 0), ("substitute_exit", 1)):
        matches = [row for row in rows if row["Kernel"] == "__snax_bingo_kernel_exit"
                   and (int(row["Cluster"]), int(row["Core"])) == (cluster, 1)]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} in generated replay safety graph")
        result[name] = int(matches[0]["ID"])
    return result


def evaluate_replay_safety(sc: dict, log_text: str, uart_text: str) -> List[str]:
    ids = sc.get("replay_safety_task_ids")
    if not ids:
        return ["missing task IDs from the generated replay safety graph"]
    problems = []
    faulty = sc["fault_stall_cycles"] is not None
    unsafe = sc.get("unsafe_replay", False)
    protected_fault = faulty and not unsafe
    branch = int(protected_fault)
    fb = [tuple(map(int, m.groups())) for m in CERF_FB_RE.finditer(log_text)]
    if len(fb) != branch or any(item[1:] != (2, 0, 1) for item in fb):
        problems.append("expected one type 2 g0 -> g1 only for the protected fault")
    blocked = [tuple(map(int, match)) for match in re.findall(
        r"\[BINGO_REPLAY_BLOCKED\] (\d+) chip=(\d+) core=(\d+) cluster=(\d+) task=(\d+)",
        log_text)]
    if ([item[1:] for item in blocked] !=
            ([(0, 1, 0, ids["add"])] if protected_fault else [])):
        problems.append("expected one blocked event for the protected add, otherwise none")
    seen = {}
    for match in DISPATCH_RE.finditer(log_text):
        time, chip, task, core, cluster = map(int, match.groups())
        seen.setdefault(task, []).append((chip, core, cluster, time))
    expected = {
        "copy": [(0, 1, 0)],
        "add": [(0, 1, 0), (0, 1, 1)] if unsafe else [(0, 1, 0)],
        "backup": [(0, 2, 0)] if protected_fault else [],
        "check": [(0, 2, 0)],
        "primary_exit": [] if protected_fault else [(0, 1, int(unsafe))],
        "substitute_exit": [] if protected_fault else [(0, 1, 1)],
    }
    for name, want in expected.items():
        if [item[:3] for item in seen.get(ids[name], [])] != want:
            problems.append(f"wrong {name} dispatch count or slot")
    replays = [int(time) for time in re.findall(
        rf"\[BINGO_REPLAY\] (\d+) chip=0 task={ids['add']} ", log_text)]
    if len(replays) != int(unsafe):
        problems.append("expected exactly one add replay only in the unsafe control")
    if protected_fault and len(blocked) == len(fb) == 1:
        fences = [int(match.group(1)) for match in WD_RE.finditer(log_text)
                  if (match.group(2), match.group(3), match.group(4), match.group(6)) ==
                  ("0", "1", "0", "1")]
        if (not fences or not seen.get(ids["add"]) or not seen.get(ids["backup"]) or
            not seen.get(ids["check"]) or not
            seen[ids["add"]][0][3] < fences[0] <= blocked[0][0] <= fb[0][0] <
                seen[ids["backup"]][0][3] < seen[ids["check"]][0][3]):
            problems.append("expected add < fence <= blocked <= CERF < backup < check")
    if unsafe and len(replays) == 1 and len(seen.get(ids["add"], [])) == 2:
        fences = [int(match.group(1)) for match in WD_RE.finditer(log_text)
                  if (match.group(2), match.group(3), match.group(4), match.group(6)) ==
                  ("0", "1", "0", "1")]
        if not fences or not seen[ids["add"]][0][3] < fences[0] <= replays[0] < seen[ids["add"]][1][3]:
            problems.append("expected original add < fence <= replay < substitute add")
    host = re.search(r"\[Host\] Bingo status: .* cerf=0x([0-9a-fA-F]+) "
                     r"cerf_fb_en=0x([0-9a-fA-F]+) cerf_fb_evt=0x([0-9a-fA-F]+)"
                     r".* replay_blocked=0x([0-9a-fA-F]+)", uart_text)
    want_status = (2 if protected_fault else 1, 4, 4 if protected_fault else 0,
                   2 if protected_fault else 0)
    if not host or tuple(int(value, 16) for value in host.groups()) != want_status:
        problems.append(f"wrong CERF/enable/event/blocked status, expected {want_status}")
    checks = [("acc", "PASS")] + ([("bk", "PASS")] if protected_fault else [])
    if CHECK_RE.findall(uart_text) != checks:
        problems.append(f"expected host checks {checks}")
    marker = (f"[Int32Inplace] check complete; branch={branch} unsafe={int(unsafe)} "
              f"replay_blocked=0x{2 if protected_fault else 0:x}")
    if marker not in uart_text:
        problems.append("missing replay safety completion marker")
    return problems


def host_fallback_task_ids(path: Path, *, add=False) -> dict:
    """Read task IDs from the actual compiled graph, not the old s28/s29 layout."""
    with path.open() as stream:
        rows = list(csv.DictReader(stream))
    kernels = {"copy": "__snax_bingo_kernel_idma_1d_copy",
               "backup": "__host_bingo_kernel_idma",
               "check": "__host_bingo_kernel_check_result"}
    if add:
        kernels.update(copy="__snax_bingo_kernel_int32_add", backup="__host_bingo_kernel_add_i32")
    result = {}
    for name, kernel in kernels.items():
        matches = [row for row in rows if row["Kernel"] == kernel]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} in generated host fallback graph")
        result[name] = int(matches[0]["ID"])
    for name, cluster in (("primary_exit", 0), ("substitute_exit", 1)):
        matches = [row for row in rows if row["Kernel"] == "__snax_bingo_kernel_exit"
                   and (int(row["Cluster"]), int(row["Core"])) == (cluster, 1)]
        if len(matches) != 1:
            raise ValueError(f"expected one {name} in generated host fallback graph")
        result[name] = int(matches[0]["ID"])
    return result


def evaluate_host_fallback(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """Validate automatic same-output fallback, replay-only and healthy controls."""
    ids = sc.get("host_fallback_task_ids")
    if not ids:
        return ["missing task IDs from the generated host fallback graph"]
    problems = []
    faulty = sc["fault_stall_cycles"] is not None
    double = sc.get("fault_any_core", False)
    fb = [tuple(map(int, m.groups())) for m in CERF_FB_RE.finditer(log_text)]
    if len(fb) != int(double) or any(e[1:] != (2, 0, 1) for e in fb):
        problems.append(f"automatic host fallback CERF events {fb}, expected "
                        f"{'one type 2 g0 -> g1' if double else 'none'}")
    seen = {}
    for m in DISPATCH_RE.finditer(log_text):
        t, chip, task, core, cluster = map(int, m.groups())
        seen.setdefault(task, []).append((chip, core, cluster, t))
    expected = {
        "copy": [(0, 1, 0), (0, 1, 1)] if faulty else [(0, 1, 0)],
        "backup": [(0, 2, 0)] if double else [],
        "check": [(0, 2, 0)],
        "primary_exit": [] if double else [(0, 1, int(faulty))],
        "substitute_exit": [] if double else [(0, 1, 1)],
    }
    for name, want in expected.items():
        actual = [item[:3] for item in seen.get(ids[name], [])]
        if actual != want:
            problems.append(f"automatic host fallback {name} task {ids[name]} slots {actual}, expected {want}")
    replay_times = [int(t) for t in re.findall(
        rf"\[BINGO_REPLAY\] (\d+) chip=0 task={ids['copy']} ", log_text)]
    if len(replay_times) != int(faulty):
        problems.append("expected exactly one copy replay on fault, none when healthy")
    if faulty and len(seen.get(ids["copy"], [])) == 2 and len(replay_times) == 1:
        original, replayed = seen[ids["copy"]]
        fences = [int(m.group(1)) for m in WD_RE.finditer(log_text)
                  if (m.group(2), m.group(3), m.group(4), m.group(6)) == ("0", "1", "0", "1")]
        if not fences or not original[3] < fences[0] <= replay_times[0] < replayed[3]:
            problems.append("expected original copy < victim fence <= replay < substitute dispatch")
    if double and len(fb) == 1 and seen.get(ids["backup"]) and seen.get(ids["check"]):
        fences = [int(m.group(1)) for m in WD_RE.finditer(log_text)
                  if (m.group(2), m.group(3), m.group(4), m.group(6)) == ("0", "1", "1", "1")]
        stuck = [int(t) for t in re.findall(r"\[BINGO_REPLAY_STUCK\] (\d+) chip=0 core=1 cluster=1:", log_text)]
        if (not fences or not stuck or not
                fences[0] <= stuck[0] <= fb[0][0] < seen[ids["backup"]][0][3] < seen[ids["check"]][0][3]):
            problems.append("expected substitute fence <= stuck <= CERF < host backup < check")
    host = re.search(r"\[Host\] Bingo status: .* cerf=0x([0-9a-fA-F]+) "
                     r"cerf_fb_en=0x([0-9a-fA-F]+) cerf_fb_evt=0x([0-9a-fA-F]+)", uart_text)
    expected_status = (2 if double else 1, 4, 4 if double else 0)
    if not host or tuple(int(v, 16) for v in host.groups()) != expected_status:
        problems.append(f"automatic host fallback CERF/en/evt expected {expected_status}")
    output = "C_l3" if sc.get("add_host_fallback") else "A_L1"
    marker = "AddHostFallback" if sc.get("add_host_fallback") else "DmaHostFallback"
    if CHECK_RE.findall(uart_text) != [(output, "PASS")]:
        problems.append(f"expected exactly one Check [{output}]: PASS")
    if f"[{marker}] check complete; host fallback {int(double)}" not in uart_text:
        problems.append("missing automatic host fallback completion marker")
    print(f"[host_fallback] task_ids={ids} events={fb}")
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


def workload_generated_outputs(workload_dir: Path) -> List[Path]:
    """Read the output targets of the workload's main_bingo generation rule.

    Only literal paths and simple Make variable references are accepted. Do not
    execute Makefile shell expressions just to decide which files to delete.
    """
    text = (workload_dir / "Makefile").read_text().replace("\\\n", " ")
    variables = dict(re.findall(r"^([A-Za-z_]\w*)\s*(?::=|\?=|=)\s*(.*?)\s*$", text, re.M))
    variables["MK_DIR"] = str(workload_dir)

    def expand(value, seen=()):
        def replace(match):
            key = match[1]
            if key in seen or key not in variables:
                raise ValueError(f"Cannot resolve generated output variable: {key}")
            return expand(variables[key], (*seen, key))
        value = re.sub(r"\$\(([A-Za-z_]\w*)\)", replace, value)
        if "$" in value:
            raise ValueError(f"Unsupported generated output expression: {value}")
        return value

    targets = re.findall(r"^([^\t\n:#]+):[^\n]*\bmain_bingo\.py\b[^\n]*$", text, re.M)
    if not targets:
        raise ValueError(f"No main_bingo.py generation rule in {workload_dir}")
    outputs = set()
    for target in targets:
        for name in expand(target).split():
            path = Path(name)
            if not path.is_absolute():
                path = workload_dir / path
            if path.parent.resolve() != workload_dir.resolve():
                raise ValueError(f"Generated output is outside workload directory: {path}")
            outputs.add(path)
    return sorted(outputs)


def clean_app_builds(workload: str) -> None:
    """Force a fresh SW build: objects and libraries do not depend on USER_FLAGS.

    The device runtime library matters too: the offload loop that actually runs
    (bingo_hw_offload_manager, a C99 inline function) is linked from
    libsnRuntime.a, not from the app's own translation unit. So does the host's
    libbingo: BINGO_PM_* (idle entry delay, boost level) are compiled into it.
    Generated headers also lack dependencies on compiler sources, so remove
    the generation rule's outputs before rebuilding. Fail closed if Git cannot
    verify that every output is untracked, before deleting any file.
    """
    workload_dir = (_REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads"
                    / workload)
    if workload_dir.parent.resolve() != (
            _REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads").resolve():
        raise ValueError(f"Invalid workload path: {workload}")
    outputs = workload_generated_outputs(workload_dir)
    tracked = subprocess.run(
        ["git", "-C", str(_REPO_ROOT), "ls-files", "-z", "--",
         *[str(path.relative_to(_REPO_ROOT)) for path in outputs]],
        check=True, stdout=subprocess.PIPE).stdout.decode().split("\0")
    if any(tracked):
        raise ValueError(f"Refusing to delete tracked generated outputs: {tracked}")
    if any(path.is_dir() and not path.is_symlink() for path in outputs):
        raise ValueError("Expected generated files, not directories")
    for path in outputs:
        path.unlink(missing_ok=True)
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
        problems.append(f"exports {exports}, expected only task {task} (no further export after rejection)")
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
    if REMAP_RE.findall(log_text):
        problems.append(f"local remap although level 3 is forced: {REMAP_RE.findall(log_text)[:3]}")
    # Link: the dispatch, then the reject (kind 0xC) on the done page
    ev = {(d, c): [] for d in ("TX", "RX") for c in ("AW", "W", "B")}
    for m in RLINK_RE.finditer(log_text):
        direction, channel, _, _, value = m.groups()
        ev[(direction, channel)].append(int(value, 0))
    tx = list(zip(ev[("TX", "AW")], ev[("TX", "W")]))
    rx = list(zip(ev[("RX", "AW")], ev[("RX", "W")]))
    # The mailbox logger exposes RX AW/W, but B responses only at the TX master.
    if any(len(ev[key]) != 2 for key in
           (("TX", "AW"), ("TX", "W"), ("TX", "B"), ("RX", "AW"), ("RX", "W"))):
        problems.append("expected two TX AW/W/B and RX AW/W transactions")
    chip_base = (chip << 40) | REMOTE_LINK_BASE
    kinds = [(a - chip_base, decode_packet(d)["kind"], decode_packet(d)["task_id"]) for a, d in tx]
    if kinds != [(0, 0x5, task), (0x1000, 0xC, task)]:
        problems.append(f"link packets {[(hex(a), hex(d)) for a, d in tx]}, expected dispatch + reject of task {task}")
    if rx != tx or any(ev[("TX", "B")] + ev[("RX", "B")]):
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
    park = [(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4).split(" ")[0],
             m.group(5), m.group(6)) for m in PARK_RE.finditer(log_text)]
    expected = [(chip, core, cluster, "HOLD", None, None),
                (chip, core, cluster, "PARKED", str(sub), str(sub_cluster))]
    if sc.get("expect_unpark"):
        expected += [(chip, core, cluster, state, None, None) for state in ("UNPARK", "UNPARKED")]
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
    if not sc.get("expect_unpark") and \
            f"Exit task of cluster {cluster} core {core} taken over" not in uart_text:
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
    if name in A5_FAMILY:
        problems += evaluate_type_threshold(sc, log_text)
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
    if sc.get("early_exit_conf"):
        problems += evaluate_early_exit_conf(sc, log_text, uart_text)
    elif sc.get("early_exit"):
        problems += evaluate_early_exit(sc, log_text, uart_text)
    elif sc.get("replay_safety"):
        problems += evaluate_replay_safety(sc, log_text, uart_text)
    elif sc.get("cerf_fallback"):
        problems += evaluate_cerf_fallback(sc, log_text, uart_text)
    elif sc.get("plain_cerf_fallback"):
        problems += evaluate_plain_cerf_fallback(sc, log_text, uart_text)
    elif sc.get("dma_cerf"):
        problems += evaluate_dma_cerf(sc, log_text, uart_text)
    elif sc.get("host_fallback"):
        problems += evaluate_host_fallback(sc, log_text, uart_text)
    elif "[BINGO_CERF_FB]" in log_text:
        problems.append("unexpected CERF fallback in a continuity scenario")
    if sc.get("workload") == "dma_chain_2cluster":
        problems += evaluate_risk_chain(sc, log_text, uart_text)
        problems += evaluate_risk_controls(sc, log_text, uart_text)
    if "risk_confirm" in sc:
        problems += evaluate_risk_confirm(sc, log_text)

    # Fault precursors: late beats and at-risk events only where expected, on the victim
    vc = sc.get("victim", VICTIM)
    late = [tuple(int(x) for x in m.groups()) for m in LATE_RE.finditer(log_text)]
    if late != [(vc[1], vc[2])] * sc.get("expect_late", 0):
        problems.append(f"[BINGO_LATE] on (core, cluster) {late}, expected {sc.get('expect_late', 0)} on the victim")
    risk = [tuple(int(x) for x in m.groups()) for m in RISK_RE.finditer(log_text)]
    if risk != ([(vc[1], vc[2])] if sc.get("expect_risk") else []):
        problems.append(f"[BINGO_RISK] events {risk}, expected {'one on the victim' if sc.get('expect_risk') else 'none'}")
    host_risk = re.search(r"\[Host\] Bingo status: .* risk=0x([0-9a-fA-F]+)", uart_text)
    expected_risk = sc.get("expect_final_risk",
                           1 << (vc[1] + HEMAIA_CI_SLOTS * vc[2]) if sc.get("expect_risk") else 0)
    if host_risk and int(host_risk.group(1), 16) != expected_risk:
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
    # (s28: the substitute dies too and has no live core of its type left)
    expect_stuck = sc.get("expect_replay_stuck", expect_stuck)
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
        # (s28: the substitute also dies) (chip, core, cluster) -> [(dead_suspect, fenced)]
        others: Dict[Tuple[int, int, int], list] = {}
        for e in other_events:
            others.setdefault((e[1], e[2], e[3]), []).append((e[4], e[5]))
        if others != sc.get("expect_wd_other", {}):
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

    if sc.get("dispatch_log"):
        # The injected permanently hung task remains pending on its fenced slot.
        pending = [(*sc["victim"], sc["fault_gid"])] if sc.get("expect_fence") else []
        try:
            dispatch_done_pairs(log_text, pending, graph_csv=sc.get("dispatch_graph_csv"),
                                host_slot=dispatch_host_slot(), checker_passed=not problems)
        except (OSError, ValueError, KeyError) as error:
            problems.append(str(error))
    print(f"[{name}] eoc_ok={eoc_ok} wd_events={events} remaps={len(remaps)} "
          f"replays={len(replays)} csr_unknown={len(csr)} checks={checks}")
    return problems


class CoreTypeCheckedSimRunner(HeMAiASimRunner):
    """Check the compiled configuration before a long simulation can outlive it."""

    def __init__(self, *, expected_core_types=None, **kwargs):
        super().__init__(**kwargs)
        self.expected_core_types = expected_core_types
        self.core_type_problems = []

    def build_apps_and_stage(self, tasks):
        os.environ["PYTHONHASHSEED"] = "0"
        return super().build_apps_and_stage(tasks)

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


class EarlyExitSimRunner(NoTraceSimRunner):
    """Generate the graph first, then inject GEMM2 using its actual task ID."""

    def __init__(self, *, inject_fault=False, **kwargs):
        super().__init__(**kwargs)
        self.inject_fault = inject_fault
        self.early_exit_task_ids = {}

    def build_apps_and_stage(self, tasks):
        info = super().build_apps_and_stage(tasks)
        graph_csv = (_REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads"
                     "/early_exit_2cluster/final_dfg.csv")
        self.early_exit_task_ids = early_exit_task_ids(graph_csv)
        if self.inject_fault:
            clean_app_builds("early_exit_2cluster")
            task = dict(tasks[0])
            task["extra_user_flags"] = (task.get("extra_user_flags", "") +
                f" -DBINGO_WD_FAULT_GID={self.early_exit_task_ids['gemm2']}"
                " -DBINGO_WD_FAULT_STALL_CYCLES=0 -DBINGO_WD_FAULT_CLUSTER=0 -DBINGO_WD_FAULT_CORE=0")
            info = super().build_apps_and_stage([task])
            if early_exit_task_ids(graph_csv) != self.early_exit_task_ids:
                raise ValueError("early-exit task IDs changed during injection rebuild")
        shutil.copyfile(graph_csv, self.output_dir / "early_exit_final_dfg.csv")
        return info


class TestCfgSimRunner(NoTraceSimRunner):
    """Build once, discover graph IDs, then patch only the staged image data."""

    def __init__(self, *, test_cfg, early_exit=False, early_exit_conf=False, inject_fault=False, **kwargs):
        super().__init__(**kwargs)
        self.test_cfg = dict(test_cfg)
        self.early_exit = early_exit or early_exit_conf
        self.early_exit_conf = early_exit_conf
        self.early_exit_conf_data = {}
        self.inject_fault = inject_fault
        self.early_exit_task_ids = {}
        self.test_cfg_record = {}

    def build_apps_and_stage(self, tasks):
        info = super().build_apps_and_stage(tasks)
        if self.early_exit:
            workload = "early_exit_conf_2cluster" if self.early_exit_conf else "early_exit_2cluster"
            graph_csv = (self.repo_root / "target/sw/host/apps/offload_bingo_hw/single_chip"
                         / "workloads" / workload / "final_dfg.csv")
            self.early_exit_task_ids = (early_exit_conf_task_ids(graph_csv) if self.early_exit_conf else
                                       early_exit_task_ids(graph_csv))
            if self.early_exit_conf:
                with graph_csv.open() as stream:
                    gemm2 = [row for row in csv.DictReader(stream)
                             if int(row["ID"]) == self.early_exit_task_ids["gemm2"]]
                if len(gemm2) != 1:
                    raise ValueError("expected one GEMM2 fault slot in generated graph")
                self.test_cfg.update(fault_cluster=int(gemm2[0]["Cluster"]),
                                     fault_core=int(gemm2[0]["Core"]))
                data_path = graph_csv.with_name("early_exit_conf_data.json")
                self.early_exit_conf_data = json.loads(data_path.read_text())
                shutil.copyfile(data_path, self.output_dir / data_path.name)
            if self.inject_fault:
                self.test_cfg["fault_gid"] = self.early_exit_task_ids["gemm2"]
            shutil.copyfile(graph_csv, self.output_dir / "early_exit_final_dfg.csv")
        if len(info) != 1:
            raise ValueError("T1 runner expects exactly one staged task")
        task_dir, ci_name = info[0]
        elf = self.repo_root / "target/sim/apps" / f"{ci_name}.elf"
        shutil.copyfile(elf, self.output_dir / "test_cfg_host.elf")
        location = test_cfg_elf_location(elf)
        banks = task_dir / "bin/app_chip_0_0"
        unpatched = read_bank_image(banks)
        (self.output_dir / "test_cfg_unpatched.bin").write_bytes(unpatched)
        if unpatched[location["offset"]:location["offset"] + location["size"]] != test_cfg_bytes(
                test_cfg_defaults(self.test_cfg["version"])):
            raise ValueError("unpatched image does not contain the default test configuration")
        self.test_cfg_record = patch_test_cfg(banks, location, self.test_cfg)
        (self.output_dir / "test_cfg.json").write_text(json.dumps(self.test_cfg_record, indent=2) + "\n")
        return info


def run_scenario(name: str, args: argparse.Namespace) -> bool:
    os.environ["PYTHONHASHSEED"] = "0"
    sc = dict(SCENARIOS[name])
    image_flags = t1_image_flags(SCENARIOS[name])
    if sc.get("recovery_hold_from"):
        previous = Path(args.out_root) / sc["recovery_hold_from"]
        logs = list(previous.glob("task_*/bin/sim_run.log"))
        if len(logs) != 1 or not (previous / "result.md").read_text().startswith(
                f"# {sc['recovery_hold_from']}: PASS"):
            raise ValueError("recovery hold requires a completed passing t64")
        sc["recovery_hold"] = recovery_hold_config(logs[0].read_text())["W_cycles"]
    if args.wd_timeout is not None and sc.get("timeout_cycles") is not None:
        # Sweep: other watchdog timeouts (the confirm timeout stays twice as long)
        sc["timeout_cycles"] = args.wd_timeout
        if sc.get("confirm_timeout_cycles"):
            sc["confirm_timeout_cycles"] = 2 * args.wd_timeout
    if sc.get("t1"):
        test_cfg = scenario_test_cfg(sc, args.extra_flags)
    out_dir = Path(args.out_root) / name
    print(f"\n===== {name}: {sc['desc']} =====")
    cfg = make_cfg(sc["cfg"], sc["timeout_cycles"], sc.get("cluster_swap"),
                   sc.get("confirm_timeout_cycles"), sc.get("extra_cfg"), sc.get("cfg_suffix", ""))
    clean_app_builds(sc["workload"])

    task = make_task(host_app_type="offload_bingo_hw", chip_type="single_chip",
                     workload=sc["workload"], dev_app="snax-bingo-offload")
    if sc["fault_stall_cycles"] is not None and not sc.get("early_exit"):
        task["extra_user_flags"] = (f"-DBINGO_WD_FAULT_GID={sc.get('fault_gid', FAULT_GID)} "
                                    f"-DBINGO_WD_FAULT_STALL_CYCLES={sc['fault_stall_cycles']}")
        if not sc.get("fault_any_core") and (
                "victim" in sc or sc.get("substitute") is not None or sc.get("cerf_fallback")):
            # The substitute gets the same task replayed: only the victim misbehaves
            # (fault_any_core, s28: the substitute dies on it as well)
            _, core, cluster = sc["victim"]
            task["extra_user_flags"] += f" -DBINGO_WD_FAULT_CLUSTER={cluster} -DBINGO_WD_FAULT_CORE={core}"
    for flags in (sc.get("extra_flags"), args.extra_flags):
        if flags:
            task["extra_user_flags"] = (task.get("extra_user_flags", "") + " " + flags).strip()

    if sc.get("t1"):
        task["extra_user_flags"] = ("-DBINGO_TEST_CFG=1 " + image_flags).strip()
    runner_cls = (TestCfgSimRunner if sc.get("t1") else
                  EarlyExitSimRunner if sc.get("early_exit") else
                  CoreTypeCheckedSimRunner if args.keep_traces else NoTraceSimRunner)
    runner = runner_cls(
        **({"test_cfg": test_cfg, "early_exit": sc.get("early_exit", False),
            "early_exit_conf": sc.get("early_exit_conf", False),
            "inject_fault": sc["fault_stall_cycles"] is not None} if sc.get("t1") else
           {"inject_fault": sc["fault_stall_cycles"] is not None} if sc.get("early_exit") else {}),
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
        plusargs=["+bingo_dispatch_log"] if sc.get("dispatch_log", False) else [],
    )
    if "evlog_enable" in sc:
        runner.plusargs.append("+evlog_check")
    runner.run([task])
    if sc.get("early_exit") or sc.get("early_exit_conf"):
        sc["early_exit_task_ids"] = runner.early_exit_task_ids
        sc["fault_gid"] = runner.early_exit_task_ids["gemm2"]
        if sc.get("early_exit_conf"):
            sc["early_exit_conf_data"] = runner.early_exit_conf_data

    bin_dir = out_dir / task_dir_name(0, task["ci_name"]) / "bin"
    log_path = bin_dir / "sim_run.log"
    uart_path = bin_dir / "uart_chip_0_0.log"
    log_text = log_path.read_text(errors="replace") if log_path.exists() else ""
    uart_text = uart_path.read_text(errors="replace") if uart_path.exists() else ""
    if sc.get("dispatch_log"):
        graph_csv = (_REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads"
                     / sc["workload"] / "final_dfg.csv")
        sc["dispatch_graph_csv"] = out_dir / "final_dfg.csv"
        shutil.copyfile(graph_csv, sc["dispatch_graph_csv"])
    if sc.get("replay_safety"):
        graph_csv = (_REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads"
                     / sc["workload"] / "final_dfg.csv")
        try:
            sc["replay_safety_task_ids"] = replay_safety_task_ids(graph_csv)
            shutil.copyfile(graph_csv, out_dir / "replay_safety_final_dfg.csv")
            if sc.get("fault_gid", INPLACE_FAULT_GID) != sc["replay_safety_task_ids"]["add"]:
                raise ValueError("fault_gid differs from the generated in-place add task ID")
        except (OSError, ValueError, KeyError) as error:
            print(f"[{name}] cannot load generated replay safety graph: {error}")
    if sc.get("host_fallback"):
        graph_csv = (_REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads"
                     / sc["workload"] / "final_dfg.csv")
        try:
            sc["host_fallback_task_ids"] = host_fallback_task_ids(
                graph_csv, add=sc.get("add_host_fallback", False))
            shutil.copyfile(graph_csv, out_dir / "host_fallback_final_dfg.csv")
            if sc.get("fault_gid", HOST_FALLBACK_FAULT_GID) != sc["host_fallback_task_ids"]["copy"]:
                raise ValueError("fault_gid differs from the generated copy task ID")
        except (OSError, ValueError, KeyError) as error:
            print(f"[{name}] cannot load generated host fallback graph: {error}")
    problems = evaluate(name, sc, log_text, uart_text)
    if sc.get("c2"):
        problems += healthy_problems(log_text, uart_text, sc["dispatch_graph_csv"])
    if sc.get("evlog_enable"):
        slots = (len(sc["expect_core_types"]) if "expect_core_types" in sc
                 else dispatch_host_slot()[1] + 1)
        problems += check_evlog(log_text, uart_text, slots)
        if sc.get("evlog_pair", True):
            reference = Path(args.out_root) / name.removesuffix("e") / task_dir_name(0, task["ci_name"]) / "bin"
            if (reference / "sim_run.log").exists():
                problems += check_evlog_pair((reference / "sim_run.log").read_text(), log_text)
            else:
                problems.append("missing disabled EVLOG pair reference")
    elif "evlog_enable" in sc and "[EVLOG]" in uart_text:
        problems.append("disabled EVLOG printed items")
    problems += runner.core_type_problems
    if sc.get("p8_recovery"):
        problems += check_recovery_hold(log_text, uart_text, sc["recovery_hold"])
    if sc.get("p8_access"):
        level = test_cfg["pm_access_level"] or (6 if test_cfg["pm_access_wake_hold"] else 25)
        problems += check_access_level(log_text, level, sc["dispatch_graph_csv"])
    if sc.get("early_exit_conf_pair"):
        reference = Path(args.out_root) / sc["early_exit_conf_pair"] / task_dir_name(0, task["ci_name"]) / "bin"
        try:
            first_log = (reference / "sim_run.log").read_text()
            first_uart = (reference / "uart_chip_0_0.log").read_bytes()
            problems += check_early_exit_conf_pair(first_log, first_uart, log_text, uart_path.read_bytes())
        except OSError as error:
            problems.append(f"cannot read confidence pair reference: {error}")
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
    if "expect_boost_domains" in sc:
        # Exactly these domains reach the boost level, and only after the fence
        fence_t = [int(m.group(1)) for m in WD_RE.finditer(log_text) if m.group(6) == "1"]
        boosted = [(int(t), int(d)) for t, d, lvl in re.findall(r"\[BINGO_PM\] (\d+) domain=(\d+) level=(\d+)", log_text)
                   if int(lvl) == 3]
        if {d for _, d in boosted} != set(sc["expect_boost_domains"]):
            problems.append(f"boosted domains {sorted({d for _, d in boosted})}, expected {sorted(sc['expect_boost_domains'])}")
        elif boosted and (not fence_t or boosted[0][0] < fence_t[0]):
            problems.append(f"boost at {boosted[0][0]} before the fence")
        print(f"[{name}] boost entries (time, domain): {boosted}")
        host_pm = re.search(r"\[Host\] Bingo PM: .* boost_policy=0x([0-9a-fA-F]+)", uart_text)
        if not host_pm:
            problems.append("no boost_policy in the host PM line")
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
    lines.append("- SW build: `PYTHONHASHSEED=0` (forwarded to the container)")
    lines.append(f"- dispatch_log: {bool(sc.get('dispatch_log', False))}")
    if sc.get("t1"):
        lines += [f"- image_flags: `{image_flags}`",
                  f"- image ID: `{runner.test_cfg_record['image_id']}`",
                  f"- changed bank lines: {runner.test_cfg_record['changed_bank_line_count']}",
                  "- configuration: `" + json.dumps(runner.test_cfg_record["configuration"], sort_keys=True) + "`"]
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

    try:
        check_bingo_bender_pin(_REPO_ROOT, Path(args.bingo_repo))
    except (OSError, ValueError) as exc:
        sys.exit(f"Bingo Bender pin check failed: {exc}")

    if shutil.which("vsim") is None:
        sys.exit("vsim not found: source the Questa setup script first")

    results = {}
    for name in args.scenario:
        results[name] = run_scenario(name, args)
        if not results[name]:
            break
    for family, names in T1_FAMILIES.items():
        selected = [name for name in names if name in results]
        if selected:
            records = [json.loads((Path(args.out_root) / name / "test_cfg.json").read_text())
                       for name in selected]
            check_t1_family(records)
    print("\n===== summary =====")
    for name, ok in results.items():
        print(f"{name}: {'PASS' if ok else 'FAIL'}  ({SCENARIOS[name]['desc']})")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()
