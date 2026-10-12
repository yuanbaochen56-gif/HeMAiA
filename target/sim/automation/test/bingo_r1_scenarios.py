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
