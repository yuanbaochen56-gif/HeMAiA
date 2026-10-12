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
