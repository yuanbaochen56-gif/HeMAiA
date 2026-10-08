"""Code-generated P9 healthy controls and a parameterized G4 family."""
from copy import deepcopy

A7_ON = 0x7D0147
P9_RECOMMENDED = (1000 << 12) | (1 << 1) | 1


def late_control(sh_l=1, floor=1000, enabled=True):
    if type(sh_l) is not int or sh_l not in (1, 2):
        raise ValueError("Evaluation SH_L must be 1 or 2")
    if type(floor) is not int or not 0 <= floor <= 1000:
        raise ValueError("Evaluation LFLOOR must be in [0, 1000]")
    return (floor << 12) | (sh_l << 1) | int(bool(enabled))


def healthy_scenarios(original, l2):
    result = {}
    for workload, template in (("chain", "c4_chain_h100_ok"),
                               ("eec", "c4_eec_ok"), ("moe2", "c4_moe2_ok")):
        for enabled in (False, True):
            name = f"p9_{workload}_ok_{'on' if enabled else 'off'}"
            scene = deepcopy(original[template])
            for key in ("same_as", "early_exit_conf_pair"):
                scene.pop(key, None)
            scene.update(t1=True, image_flags="-DBINGO_WD_LATE", wd_adapt=A7_ON,
                         wd_late=P9_RECOMMENDED if enabled else 0,
                         fault_pre_stall_from=0, dispatch_log=True,
                         evlog_enable=1, evlog_pair=False, evlog_allow_empty=True,
                         risk_late=0, risk_policy=0x12, risk_epoch=0,
                         extra_flags="-DBINGO_RISK_LATE=0 -DBINGO_RISK_POLICY=0x12 -DBINGO_RISK_EPOCH=0",
                         p9_healthy=True, p9_workload=workload,
                         extra_cfg=deepcopy(l2), cfg_suffix="_l2",
                         timeout_cycles=100000, confirm_timeout_cycles=200000,
                         desc=f"P9 healthy {workload}, prediction {'on' if enabled else 'off'}")
            result[name] = scene
    return result


def evaluation_scenarios(original, l2, *, a, g, pre_stall_cycles,
                         static_late, sh_l=1, floor=1000):
    """Caller must justify and seal these parameters before running any G4."""
    if type(a) is not int or a < 1 or type(g) is not int or g < a + 3:
        raise ValueError("G4 requires a >= 1 and g >= a+3")
    for value in (a, g, pre_stall_cycles, static_late):
        if type(value) is not int or not 0 < value < 1 << 32:
            raise ValueError("G4 parameters must be positive uint32 words")
    prediction = late_control(sh_l, floor)
    result = {}
    for name in ("d_static", "d_a7", "d_p9", "d_p9_noad", "d_p9_boost",
                 "d_p9_derate", "s_a7", "s_p9"):
        scene = deepcopy(original["c4_chain_h100"])
        for key in ("same_as", "expect_chain_clusters", "expect_late", "expect_takeover",
                    "expect_risk", "expect_type_confirm", "expect_wd", "c4"):
            scene.pop(key, None)
        p9_on = "p9" in name
        no_hang = name.startswith("s_")
        park = p9_on and name != "d_p9_derate"
        scene.update(t1=True, image_flags="-DBINGO_WD_LATE",
                     wd_adapt=A7_ON & ~1 if name in ("d_static", "d_p9_noad") else A7_ON,
                     wd_late=prediction if p9_on else 0,
                     fault_cluster=0, fault_core=1, fault_pre_stall_from=a,
                     fault_pre_stall_cycles=pre_stall_cycles, fault_after_kernel=0,
                     fault_gid=(1 << 32) - 1 if no_hang else g, fault_stall_cycles=0,
                     risk_late=static_late, risk_policy=0xc22 if name == "d_p9_derate" else 0x12,
                     risk_epoch=0, dispatch_log=True, evlog_enable=1,
                     evlog_pair=False, evlog_allow_empty=park or no_hang,
                     expect_eoc=True, expect_fence=not (park or no_hang),
                     p9_evaluation=True, p9_name=name, desc=f"P9 evaluation {name}",
                     extra_cfg=deepcopy(l2), cfg_suffix="_l2", park=park,
                     timeout_cycles=100000, confirm_timeout_cycles=200000)
        # Existing T1 controls are data macros, not direct scene dictionary keys.
        scene["extra_flags"] = (
            f"-DBINGO_WD_FAULT_PRE_STALL_CYCLES={pre_stall_cycles} "
            f"-DBINGO_WD_FAULT_AFTER_KERNEL=0 -DBINGO_RISK_LATE={static_late} "
            f"-DBINGO_RISK_POLICY={scene['risk_policy']:#x} -DBINGO_RISK_EPOCH=0")
        if name == "d_p9_boost":
            scene.update(pm_boost_power_level=3, boost_policy=0x10101)
            scene["extra_flags"] += " -DBINGO_PM_BOOST_POWER_LEVEL=3 -DBINGO_BOOST_POLICY=0x10101"
        if name == "d_p9_derate":
            scene["expect_derate_level"] = 12
        result[f"p9_{name}"] = scene
    return result
