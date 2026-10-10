"""Generate six same-image T1 scenarios from the actual compiler report."""
from copy import deepcopy


def sdc_scenarios(template, report):
    selected = {row["node_id"]: row for row in report["nodes"] if row["selected"]}
    assert set(selected) == set(range(6))
    scenes = {}
    cases = (
        ("s1_healthy_off", 0, None, False, "recovered", True),
        ("s1_healthy_on", 1, None, False, "recovered", True),
        ("s1_copy2_on", 1, 2, False, "detected-sdc", False),
        ("s1_copy5_on", 1, 5, False, "detected-sdc", False),
        ("s1_copy2_off", 0, 2, False, "wrong", False),
        ("s1_shadow2_on", 1, 2, True, "detected-sdc-shadow", True),
    )
    for name, enabled, primary, shadow, category, checks_pass in cases:
        scene = deepcopy(template)
        for key in ("same_as", "expect_chain_clusters", "healthy_test_cfg"):
            scene.pop(key, None)
        scene.update(
            desc=f"S1a {name}", workload="dma_chain_sdc_2cluster",
            t1=True, sdc=True, image_flags="-DBINGO_SDC", extra_flags="",
            fault_stall_cycles=None, fault_gid=0xFFFFFFFF,
            sdc_enable=enabled, fault_sdc_gid=0xFFFFFFFF,
            fault_sdc_cluster=0xFFFFFFFF, fault_sdc_core=0xFFFFFFFF,
            fault_sdc_offset=17, fault_sdc_mask=0x80,
            dispatch_log=True, evlog_enable=1, expect_eoc=checks_pass,
            sdc_expect_class=category, sdc_expect_mismatches=[],
            sdc_expect_checks_pass=checks_pass, sim_timeout_s=1200)
        if primary is not None:
            pair = selected[primary]
            scene.update(fault_sdc_gid=pair["shadow_id"] if shadow else primary,
                         fault_sdc_cluster=1 if shadow else 0, fault_sdc_core=1)
            if enabled:
                scene["sdc_expect_mismatches"] = [dict(
                    primary=primary, shadow=pair["shadow_id"],
                    compare=pair["compare_id"], code=18)]
        scenes[name] = scene
    return scenes
