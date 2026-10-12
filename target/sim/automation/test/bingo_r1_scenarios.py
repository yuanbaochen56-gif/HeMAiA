"""R1: cost-aware choice of the executor that takes over a fenced core's work.

R1a keeps the C4 int32-add image and T1 data and only changes the RTL cfg:
on default (level-1) hardware a fenced DM has no live substitute, so the CERF
table sends the add to its host backup at the first fence instead of a
level-2 replay on the other cluster.
"""
from copy import deepcopy


def r1a_scenarios(original):
    """c4_hfb_one / c4_hfb_one_ok on default hardware (no level-2 cfg key)."""
    scenes = {}
    for name, template in (("r1a_hfb_one_l1", "c4_hfb_one"),
                           ("r1a_hfb_one_l1_ok", "c4_hfb_one_ok")):
        scene = deepcopy(original[template])
        scene.pop("extra_cfg", None)
        scene["cfg_suffix"] = ""
        scene["r1_template"] = template
        scenes[name] = scene
    fault = scenes["r1a_hfb_one_l1"]
    fault.pop("substitute_cluster", None)
    fault.update(substitute=None, expect_takeover=False, expect_replay_stuck=True,
                 expect_host_stuck=True, host_fallback_fence_cerf=True,
                 desc="R1a: one DM dies on the int32 add, default hardware: "
                      "no substitute, CERF to the host add at the fence")
    control = scenes["r1a_hfb_one_l1_ok"]
    control.update(same_as="r1a_hfb_one_l1",
                   desc="R1a healthy control: int32 add image on default hardware")
    return scenes


# Core type of the DM cores on hemaia_ci: the type both workloads protect
R1B_DM_MASK = 1 << 2
R1B_IMAGE_FLAGS = "-DBINGO_FENCE_FB"


def r1b_scenarios(original):
    """Fence fallback on level-2 hardware: one flag image per workload.

    The generated code writes the register as (types the compiler allows) &
    (the fence_fb word), so the word turns the choice on or off in one image.
    int32 add (the compiler allows the DM type): off keeps the level-2 replay,
    on sends the add to its host backup at the fence, as R1a did by hardware.
    512 B copy with a host backup (the compiler allows no type): the word asks
    for the DM type and nothing changes.
    """
    scenes = {}

    def add(name, template, word, desc, **changes):
        scene = deepcopy(original[template])
        scene.update(image_flags=R1B_IMAGE_FLAGS, fence_fb=word, r1_template=template, desc=desc)
        scene.update(changes)
        scenes[name] = scene
        return scene

    add("r1b_hfb_off", "c4_hfb_one", 0,
        "R1b off: one DM dies on the int32 add, level-2 replay")
    on = add("r1b_hfb_on", "c4_hfb_one", R1B_DM_MASK,
             "R1b on: one DM dies on the int32 add, host backup at the fence",
             substitute=None, expect_takeover=False, expect_replay_stuck=True,
             expect_host_stuck=True, host_fallback_fence_cerf=True)
    on.pop("substitute_cluster", None)
    add("r1b_hfb_off_ok", "c4_hfb_one_ok", 0,
        "R1b healthy control, register 0", same_as="r1b_hfb_off")
    add("r1b_hfb_on_ok", "c4_hfb_one_ok", R1B_DM_MASK,
        "R1b healthy control, DM type set: nothing is fenced", same_as="r1b_hfb_on")

    copy = deepcopy(original["s35"])
    copy.update(t1=True, extra_flags="", dispatch_log=True, evlog_enable=1, evlog_pair=False,
                evlog_allow_empty=False)
    for name, word, desc in (
            ("r1b_copy_off", 0, "R1b copy, register 0: the DM copy is replayed on cluster 1"),
            ("r1b_copy_req", R1B_DM_MASK,
             "R1b copy, DM type requested: the compiler refuses, same level-2 replay")):
        scene = deepcopy(copy)
        scene.update(image_flags=R1B_IMAGE_FLAGS, fence_fb=word, r1_template="s35", desc=desc)
        scenes[name] = scene
    return scenes
