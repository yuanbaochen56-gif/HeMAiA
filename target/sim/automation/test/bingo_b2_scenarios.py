"""B2: a second offload in the same boot after a fault in the first one.

The workload dma_chain_twice_2cluster runs the DMA chain of dma_chain_2cluster
twice in one boot, both times through the cold path of offload_bingo_hw.c, and
turns the test fault off before the second offload. The scenes reuse the C4
and C3 chain templates (same T1 data, level-2 hardware) and only change the
workload, so the first offload of each scene is the template's run.
"""
from copy import deepcopy

B2_WORKLOAD = "dma_chain_twice_2cluster"
# Two offloads and a bounded wait for the second one
B2_SIM_TIMEOUT_S = 3600


def b2_0_scenarios(original):
    """B2-0, no interface change: healthy, permanent hang, transient stall."""
    scenes = {}
    for name, template, desc in (
            ("b20_hang", "c4_chain_h100",
             "B2-0: cluster-0 DM hangs for good on copy 3 in offload 1, then offload 2"),
            ("b20_ok", "c4_chain_h100_ok",
             "B2-0: two healthy offloads of the DMA chain"),
            ("b20_zombie", "c3_a_g6_zombie",
             "B2-0: cluster-1 DM stalls 700000 cycles on copy 6 in offload 1, then offload 2")):
        scene = deepcopy(original[template])
        scene.update(workload=B2_WORKLOAD, b2_template=template, desc=desc,
                     sim_timeout_s=B2_SIM_TIMEOUT_S)
        scene.pop("expect_chain_clusters", None)
        scenes[name] = scene
    scenes["b20_ok"]["same_as"] = "b20_hang"
    return scenes


# V3: the quadrant and all clusters are reset between the two offloads
B2_V3_CFG = {"acc_reset": "true"}
B2_V3_SUFFIX = "_accrst"
B2_HANG, B2_OK = "c4_chain_h100", "c4_chain_h100_ok"
# The C3 stall of the cluster-1 DM on its copy (task 6), as a C4-style scene:
# the cluster-0 DM takes the copy over, the chain stays on cluster 0
B2_ZOMBIE = dict(victim=(0, 1, 1), fault_gid=6, fault_stall_cycles=700000,
                 substitute=1, substitute_cluster=0, expect_chain_clusters=[0] * 6)
# Host cycles to wait before the boundary so that the stalled core is back
B2_ZOMBIE_BACK_WAIT = 300000


def b2_v3_scenarios(original):
    """V3 on level-2 hardware with the accelerator reset (s1_quadrant.acc_reset).

    b2_offloads lists, per offload, the single-offload scene it repeats
    (template plus changes), the exit word the host must read and the dropped
    done lines; bingo_b2_check judges the run with it.
    """
    scenes = {}

    def cfg_of(scene):
        extra_cfg = dict(scene.get("extra_cfg") or {})
        extra_cfg.update(B2_V3_CFG)
        return dict(extra_cfg=extra_cfg, cfg_suffix=scene.get("cfg_suffix", "") + B2_V3_SUFFIX)

    def twice(name, desc, first, second, **changes):
        scene = deepcopy(original[first["template"]])
        scene.update(first.get("changes", {}))
        scene.update(cfg_of(scene), workload=B2_WORKLOAD, desc=desc, sim_timeout_s=B2_SIM_TIMEOUT_S,
                     b2_offloads=[first, second])
        for key in ("same_as", "expect_chain_clusters"):
            scene.pop(key, None)
        scene.update(changes)
        scenes[name] = scene

    ok = dict(template=B2_OK, exit=1)
    hang = dict(template=B2_HANG, exit=0)
    zombie = dict(template=B2_HANG, changes=B2_ZOMBIE, exit=0)
    twice("b2v3_ok", "V3: two healthy offloads with the accelerator reset between", ok, ok)
    twice("b2v3_hang",
          "V3: cluster-0 DM hangs for good on copy 3, accelerator reset, healthy offload 2", hang, ok)
    twice("b2v3_zombie",
          "V3: cluster-1 DM stalls 700000 cycles on copy 6, accelerator reset during the stall, "
          "healthy offload 2", zombie, ok)
    twice("b2v3_zombie_back",
          "V3: as b2v3_zombie, but the host waits until the stalled core is back in its ready "
          "read before the accelerator reset",
          dict(zombie, dropped_done=[(0, 1, 1, 6)]), ok, t1_user=[0, B2_ZOMBIE_BACK_WAIT, 0, 0])
    twice("b2v3_hang_again",
          "V3: the same DM hangs on copy 3 in both offloads (T1 user word 0 bit 0 keeps the fault)",
          hang, hang, t1_user=[1, 0, 0, 0])
    # The hardware with the reset, never requested: the single-offload chain run
    present = deepcopy(original[B2_OK])
    present.update(cfg_of(present), b2_template=B2_OK, sim_timeout_s=B2_SIM_TIMEOUT_S,
                   desc="V3 hardware, one healthy offload of dma_chain_2cluster (no reset request)")
    present.pop("same_as", None)
    scenes["b2v3_present_ok"] = present
    return scenes
