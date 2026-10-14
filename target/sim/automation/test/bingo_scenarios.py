"""The scenes of 3_start_bingo_watchdog_sim.py.

A scene is a dict of settings (cfg, workload, fault, T1 controls) and
expectations (expect_*). The s- and t-series are written out below, most of
them as dict(SCENARIOS[base], ...) of an earlier scene; the evaluation
families (C2-C6 here, P9, R1 and B2 in their own modules) are generated from
those templates. The driver's docstring describes the s-series.
"""
from typing import Dict

from bingo_b2_scenarios import b2_0_scenarios, b2_v3_scenarios
from bingo_p9_scenarios import healthy_scenarios as p9_healthy_scenarios
from bingo_r1_scenarios import r1a_scenarios, r1b_scenarios

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
                    dispatch_log=True, evlog_enable=1, evlog_pair=False, evlog_allow_empty=True,
                    expect_eoc=True,
                    timeout_cycles=None, confirm_timeout_cycles=None, extra_cfg=None,
                    cfg_suffix="", c2_workload=label)
        variants = ("off", "nofb") if label == "dmafb" else ("off", "hw", "on", "nohb")
        if label in ("chain", "dummy"):
            variants += ("cerf",)
        if label == "moe2":
            variants += ("nofb",)
        k = calibration.get(label, 1)
        if not isinstance(k, int) or not 1 <= k <= 10:
            raise ValueError(f"C2 calibration k outside 1..10: {label}={k}")
        for variant in variants:
            scene = dict(base, c2_variant=variant,
                         c2_part=1 if variant in ("off", "hw", "on", "cerf") and label != "dmafb"
                         else 2 if variant == "nohb" else 3,
                         desc=f"C2 no-fault {label} {variant}")
            if variant in ("hw", "on", "cerf"):
                scene.update(timeout_cycles=100000 * k, confirm_timeout_cycles=200000 * k,
                             extra_cfg=dict(L2_CFG_KEYS), cfg_suffix="_l2")
            if variant == "on":
                h, c = [0] * 16, [0] * 16
                h[2], c[2] = 20000 * k, 40000 * k
                scene.update(wd_type_h=h, wd_type_c=c, recovery_hold=1000)
                controls = {
                    "BINGO_RISK_LATE": 30000 * k, "BINGO_RISK_POLICY": 0x02,
                    "BINGO_RISK_CONFIRM": 125000 * k, "BINGO_PM_BOOST_POWER_LEVEL": 3,
                    "BINGO_BOOST_POLICY": 0x10101,
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
            if variant in ("on", "cerf") and label in ("chain", "dummy"):
                controls = {"BINGO_CERF_FB_CLUSTER": 0, "BINGO_CERF_FB_CORE": 1,
                            "BINGO_CERF_FB_CLEAR": 31, "BINGO_CERF_FB_SET": 30}
                scene["extra_flags"] = (scene.get("extra_flags", "") + " " +
                                        " ".join(f"-D{name}={value}" for name, value in controls.items())).strip()
            scenes[f"c2_{label}_{variant}"] = scene
    return scenes


SCENARIOS.update(c2_scenarios())

def c2b_scenarios() -> Dict[str, dict]:
    """Layout-preserving heartbeat and inactive-type CERF controls."""
    scenes = {}
    for label in ("chain", "dummy", "moe2"):
        scenes[f"c2_{label}_nophb"] = dict(
            SCENARIOS[f"c2_{label}_off"], c2_variant="nophb", c2_part=2,
            image_flags="-DBINGO_WD_NOP_HEARTBEAT",
            desc=f"C2b no-fault {label} layout-preserving heartbeat control")
    scenes["c2_chain_cerf0"] = dict(
        SCENARIOS["c2_chain_hw"], c2_variant="cerf0",
        extra_flags="-DBINGO_CERF_FB_CLUSTER=0 -DBINGO_CERF_FB_CORE=2 "
                    "-DBINGO_CERF_FB_CLEAR=31 -DBINGO_CERF_FB_SET=30",
        desc="C2b CERF table on the masked host type")
    return scenes


SCENARIOS.update(c2b_scenarios())

def c4_scenarios() -> Dict[str, dict]:
    """Generate the C4.2 matrix from the accepted fault/healthy templates."""
    scenes = {}

    def fault(name, template, family, **changes):
        scene = dict(template, c4=True, c4_family=family, c4_role="fault",
                     dispatch_log=True, evlog_enable=1, evlog_pair=False,
                     evlog_allow_empty=False)
        scene.update(changes)
        scenes[name] = scene
        return scene

    def control(name, fault_name, template=None):
        scene = dict(template if template is not None else scenes[fault_name],
                     c4=True, c4_family=scenes[fault_name]["c4_family"],
                     c4_role="control", c4_control=True, fault_gid=0xFFFFFFFF,
                     same_as=fault_name, dispatch_log=True, evlog_enable=1,
                     evlog_pair=False, evlog_allow_empty=True)
        if template is None:
            for key in ("cerf_fallback", "host_fallback", "dma_cerf"):
                scene.pop(key, None)
            scene.update(expect_fence=False, expect_takeover=False,
                         expect_host_stuck=False, expect_type_confirm=False,
                         expect_wd=[], substitute=None)
            if scene["workload"] == "dma_chain_2cluster":
                scene["expect_chain_clusters"] = list(SCENARIOS["tch0"]["expect_chain_clusters"])
        scenes[name] = scene

    for h in (50000, 100000, 200000):
        name = f"c4_chain_h{h // 1000}"
        fault(name, SCENARIOS["tch3"], "H", timeout_cycles=h, confirm_timeout_cycles=2*h)
        control(name + "_ok", name)
    for h in (20000, 40000):
        name = f"c4_chain_a5_{h // 1000}"
        th, tc = [0]*16, [0]*16
        th[2], tc[2] = h, 2*h
        fault(name, scenes["c4_chain_h100"], "A5", wd_type_h=th, wd_type_c=tc,
              expect_type_confirm=True)
        control(name + "_ok", name)
    for r, template in ((0, "t40"), (125000, "t41")):
        fault(f"c4_chain_r{r // 1000}", SCENARIOS[template], "A2")
    fault("c4_eec_fault", SCENARIOS["t47"], "eec")
    control("c4_eec_ok", "c4_eec_fault", SCENARIOS["t46"])
    fault("c4_moe2_fault", SCENARIOS["s16"], "moe2", t1=True)
    control("c4_moe2_ok", "c4_moe2_fault")
    fault("c4_hfb_both", SCENARIOS["t53"], "hfb", c4_variant="both")
    control("c4_hfb_both_ok", "c4_hfb_both", SCENARIOS["t55"])
    fault("c4_hfb_one", SCENARIOS["t54"], "hfb", c4_variant="one")
    control("c4_hfb_one_ok", "c4_hfb_one")
    return scenes


SCENARIOS.update(c4_scenarios())

# C5 (DESIGN 9.24.7): PM knobs as T1 data fields on the C4 chain image.
C5_BOOST_ON = dict(pm_boost_power_level=3, boost_policy=0x10101)
C5_WAKE_HOLD = 1000


def c5_scenarios() -> Dict[str, dict]:
    """Generate the C5 matrix: F (fault W x boost x D), H (healthy), SV (servo)."""
    base = c4_scenarios()
    scenes = {}

    def add(group, fault, w=0, boost=False, d=0, a=0, s=0):
        flags = []
        if boost:
            flags += [f"-DBINGO_PM_BOOST_POWER_LEVEL={C5_BOOST_ON['pm_boost_power_level']}",
                      f"-DBINGO_BOOST_POLICY={C5_BOOST_ON['boost_policy']:#x}"]
        if d:
            flags.append(f"-DBINGO_PM_IDLE_ENTRY_DELAY={d}")
        if a:
            flags.append(f"-DBINGO_PM_ACCESS_WAKE_HOLD={a}")
        name = (f"c5_{'f' if fault else 'h'}_w{w}_b{int(boost)}_d{d}"
                + (f"_a{a}_s{s}" if a or s else ""))
        template = base["c4_chain_h100" if fault else "c4_chain_h100_ok"]
        scene = dict(template, c5=True, c5_group=group, c5_fault=fault,
                     c5_knobs=dict(W=w, boost=int(boost), D=d, A=a, S=s),
                     extra_flags=" ".join(flags), recovery_hold=w, pm_access_level=s)
        scene["desc"] = f"C5 {group}: {'fault' if fault else 'healthy'} W={w} boost={int(boost)} D={d} A={a} S={s}"
        if fault and w:
            scene["p8_recovery"] = True
        if not fault:
            # Same image and fault fields as the fault scene with the same knobs.
            scene["same_as"] = name.replace("c5_h_", "c5_f_", 1)
        scenes[name] = scene

    for w in (0, 1000, 10000):
        for boost in (False, True):
            for d in (0, 1000):
                add("F", True, w, boost, d)
    for boost in (False, True):
        for d in (0, 1000):
            add("H", False, 0, boost, d)
    add("H", False, 10000)
    for s in (0, 12):
        add("SV", True, a=C5_WAKE_HOLD, s=s)
        add("SV", False, a=C5_WAKE_HOLD, s=s)
    return scenes


SCENARIOS.update(c5_scenarios())

# C6 (DESIGN 9.24.8): hardware vs software CERF degradation vs none. One image
# per workload (BINGO_C6_SW_CERF); the T1 word cerf_fb_mode picks the variant.
C6_MODES = {"hw": 0, "sw": 1, "none": 2}
C6_POLL_CYCLES = 10000


def c6_scenarios() -> Dict[str, dict]:
    """The C4 moe2 and confidence early-exit fault/control pairs, per variant.

    Faults: hw, sw (poll 0 and 10000 host cycles), none (fail-stop). Healthy
    controls for hw and both sw polls, each the same-image pair of its fault.
    """
    base = c4_scenarios()
    scenes = {}
    for workload, fault, control in (("moe2", "c4_moe2_fault", "c4_moe2_ok"),
                                     ("eec", "c4_eec_fault", "c4_eec_ok")):
        for label, mode, poll in (("hw", "hw", 0), ("sw0", "sw", 0),
                                  (f"sw{C6_POLL_CYCLES}", "sw", C6_POLL_CYCLES), ("none", "none", 0)):
            name = f"c6_{workload}_{label}"
            for scene_name, template in ((name, base[fault]), (name + "_ok", base[control])):
                if mode == "none" and template is base[control]:
                    continue  # without a fault the table never fires: same as the hw control
                scene = dict(template, c6=True, c6_workload=workload, c6_mode=mode,
                             c6_fault=template is base[fault],
                             image_flags="-DBINGO_C6_SW_CERF",
                             cerf_fb_mode=C6_MODES[mode], cerf_poll_cycles=poll)
                if template is base[control]:
                    scene["same_as"] = name
                kind = "healthy" if template is base[control] else "fault"
                scene["desc"] = f"C6 {workload} {mode} poll={poll} {kind}"
                scenes[scene_name] = scene
        # Fail-stop: the stuck branch is never degraded and the run never ends
        scenes[f"c6_{workload}_none"].update(expect_eoc=False, sim_timeout_s=900)
        scenes[f"c6_{workload}_none"].pop("cerf_fallback", None)
    return scenes


SCENARIOS.update(c6_scenarios())

def c3_scenarios() -> Dict[str, dict]:
    """C3.2 coverage matrix, in the authorized healthy -> A -> ... -> E order.

    Task IDs/slots are checked against each staged graph by the C3 harness;
    confidence GEMM2 is resolved by the existing TestCfgSimRunner.
    """
    scenes = {}
    configs = (
        ("chain_l2", "tch0", L2_CFG_KEYS, "_l2"),
        ("chain_l1", "tch0", {"bingo_substitute_level_mask": "1"}, "_cerf_l1"),
        ("moe2_l1", "s17", {"bingo_substitute_level_mask": "1"}, "_cerf_l1"),
        ("moe2_l2", "s17", L2_CFG_KEYS, "_l2"),
        ("eec_l1", "t46", {"bingo_substitute_level_mask": "1"}, "_cerf_l1"),
        ("hfb_l2", "t55", L2_CFG_KEYS, "_l2"),
        ("inplace_l2", "s39", L2_CFG_KEYS, "_l2"),
    )

    def add(name, template, family, fault_type, **changes):
        scene = dict(SCENARIOS[template], t1=True, c3=True, c3_family=family,
                     c3_fault_type=fault_type, dispatch_log=True, extra_flags="",
                     timeout_cycles=100000, confirm_timeout_cycles=200000,
                     sim_timeout_s=1200)
        for key in ("same_as", "early_exit_conf_pair", "expect_chain_clusters"):
            scene.pop(key, None)
        scene.update(changes)
        scene["desc"] = f"C3 {family} {name}"
        if fault_type == "post":
            scene["extra_flags"] = "-DBINGO_WD_FAULT_AFTER_KERNEL=1"
        scenes[name] = scene

    for label, template, cfg, suffix in configs:
        add(f"c3_ok_{label}", template, "healthy", "healthy", extra_cfg=dict(cfg),
            cfg_suffix=suffix, fault_stall_cycles=None, fault_gid=0xFFFFFFFF,
            fault_any_core=False, expect_eoc=True, expect_fence=False,
            expect_host_stuck=False, expect_takeover=False, expect_wd=[],
            substitute=None, healthy_test_cfg=True)
        control = scenes[f"c3_ok_{label}"]
        # Fault-specific branch checkers treat any configured stall as a fault.
        # Use their unchanged healthy templates. Confidence slot resolution is
        # only needed for faults; the healthy T1 record keeps position ANY.
        control.pop("fault_slot_from_gemm2", None)
        if label == "eec_l1":
            control["early_exit_conf"] = False
        if label.startswith("chain"):
            control["expect_chain_clusters"] = [0] * 6
    stalls = dict(pre=0, post=0, slow=350000, zombie=700000)
    for gid in (0, 3, 5, 6):
        for kind in stalls:
            cluster = int(gid == 6)
            add(f"c3_a_g{gid}_{kind}", "tch3", "A", kind, extra_cfg=dict(L2_CFG_KEYS),
                cfg_suffix="_l2", fault_gid=gid, victim=(0, 1, cluster),
                fault_stall_cycles=stalls[kind], substitute_cluster=1-cluster)
    for gid in (0, 6):
        add(f"c3_al1_g{gid}_pre", "tch3", "A-L1", "pre",
            extra_cfg={"bingo_substitute_level_mask": "1"}, cfg_suffix="_cerf_l1",
            fault_gid=gid, victim=(0, 1, int(gid == 6)), fault_stall_cycles=0,
            substitute=None, expect_eoc=False, sim_timeout_s=900)
    for mask, suffix in ((1, "_cerf_l1"), (3, "_l2")):
        for gid, core in ((2, 1), (4, 0), (5, 1)):
            for kind in ("pre", "post", "zombie"):
                add(f"c3_b_m{mask}_g{gid}_{kind}", "s16", "B", kind,
                    extra_cfg={"bingo_substitute_level_mask": str(mask)}, cfg_suffix=suffix,
                    fault_gid=gid, victim=(0, core, 0), fault_stall_cycles=stalls[kind],
                    sim_timeout_s=900 if mask == 1 and core == 1 else 1200)
    for sample in (0, 1):
        for kind in ("pre", "post", "zombie"):
            add(f"c3_c_s{sample}_{kind}", "t47", "C", kind,
                fault_stall_cycles=stalls[kind], t1_user=[sample, 0, 0, 0])
    for both, template in ((False, "t54"), (True, "t53")):
        for kind in ("pre", "post", "zombie"):
            add(f"c3_d_{'both' if both else 'one'}_{kind}", template, "D", kind,
                fault_stall_cycles=stalls[kind], fault_any_core=both)
    for kind in ("pre", "post", "zombie"):
        add(f"c3_e_{kind}", "s37", "E", kind, fault_gid=INPLACE_FAULT_GID,
            fault_stall_cycles=stalls[kind])
    return scenes


SCENARIOS.update(c3_scenarios())


def c3_e2_scenarios() -> Dict[str, dict]:
    scenes = {"c3_ok_inplace_e2": dict(
        SCENARIOS["c3_ok_inplace_l2"], inplace_branch_check=True)}
    for kind in ("pre", "post", "zombie"):
        scenes[f"c3_e2_{kind}"] = dict(SCENARIOS[f"c3_e_{kind}"], inplace_branch_check=True)
    for name, scene in scenes.items():
        scene["desc"] = f"C3 E2 branch-aware in-place check: {name}"
    return scenes


SCENARIOS.update(c3_e2_scenarios())
SCENARIOS.update(p9_healthy_scenarios(SCENARIOS, L2_CFG_KEYS))
SCENARIOS.update(r1a_scenarios(SCENARIOS))
SCENARIOS.update(r1b_scenarios(SCENARIOS))
SCENARIOS.update(b2_0_scenarios(SCENARIOS))
SCENARIOS.update(b2_v3_scenarios(SCENARIOS))


# Power-domain counters (BINGO_PM_COUNT): scenes of the s-series with the host
# print of the counters (-DBINGO_PM_COUNT_LOG), checked against the level log
# and the dispatch-to-done intervals (bingo_pm_count). pm_count: the clusters.
def pm_count_scenarios() -> Dict[str, dict]:
    scenes = {}
    # (clusters, cores per cluster without the extra host / tied-off slot)
    for name, base, clusters, cores, flags, what in (
            ("pmc_s1", "s1", 2, 2, "", "healthy"),
            ("pmc_s1_servo", "s1", 2, 2, "-DBINGO_PM_ACCESS_WAKE_HOLD=1000 -DBINGO_PM_ACCESS_LEVEL=12",
             "healthy, access wake and servo level 12"),
            ("pmc_s7", "s7", 1, 3, "", "fence, replay in the same cluster"),
            ("pmc_s25", "s25", 2, 2, "", "fence, capacity boost of the survivor's domain")):
        scene = dict(SCENARIOS[base], pm_count=clusters, pm_count_cores=cores)
        scene["extra_flags"] = " ".join(f for f in (scene.get("extra_flags", ""), "-DBINGO_PM_COUNT_LOG", flags) if f)
        scene["desc"] = f"{base} with the power-domain counters printed and checked: {what}"
        scenes[name] = scene
    return scenes


SCENARIOS.update(pm_count_scenarios())
