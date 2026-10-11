"""New recovery experiments; no entries are added to frozen C3 families."""
from copy import deepcopy


def recovery_scenarios(template, report, graph):
    selected = [r for r in report["nodes"] if r["selected"]]
    if [r["status_index"] for r in selected] != [0, 1]:
        raise ValueError("Recovery requires the sealed contiguous status map")
    if len(graph["stages"]) != 2:
        raise ValueError("Recovery requires two stages")
    by_primary = {r["node_id"]: r for r in selected}
    scenes = {}
    cases = (("s1b_healthy_off", 0, None, False),
             ("s1b_healthy_on", 1, None, False),
             ("s1b_stage0_primary_on", 1, 0, False),
             ("s1b_stage1_primary_on", 1, 1, False),
             ("s1b_stage0_primary_off", 0, 0, False),
             ("s1b_stage0_shadow_on", 1, 0, True))
    for name, enabled, tx, shadow in cases:
        sc = deepcopy(template)
        for key in ("same_as", "expect_chain_clusters", "healthy_test_cfg", "sdc"):
            sc.pop(key, None)
        sc.update(desc=name, workload="dma_chain_sdc_recover_2cluster",
                  t1=True, sdc_recover=True,
                  image_flags="-DBINGO_SDC -DBINGO_SDC_QUIET -DBINGO_SDC_RECOVER",
                  extra_flags="", fault_stall_cycles=None, fault_gid=0xFFFFFFFF,
                  sdc_enable=enabled, fault_sdc_gid=0xFFFFFFFF,
                  fault_sdc_cluster=0xFFFFFFFF, fault_sdc_core=0xFFFFFFFF,
                  fault_sdc_offset=17, fault_sdc_mask=4, dispatch_log=True,
                  evlog_enable=1, evlog_allow_empty=True, evlog_pair=False,
                  recovery_graph=deepcopy(graph),
                  recovery_fault_tx=tx, recovery_fault_shadow=shadow,
                  recovery_correct=not (tx is not None and not enabled and not shadow),
                  sim_timeout_s=1200)
        sc["expect_eoc"] = sc["recovery_correct"]
        if tx is not None:
            row = graph["stages"][tx]
            pair = by_primary[row["primary"]]
            if pair["shadow_id"] != row["shadow"] or pair["compare_id"] != row["compare"]:
                raise ValueError("Recovery graph/report binding differs")
            sc.update(fault_sdc_gid=row["shadow"] if shadow else row["primary"],
                      fault_sdc_cluster=1 if shadow else 0, fault_sdc_core=1)
        scenes[name] = sc
    return scenes
