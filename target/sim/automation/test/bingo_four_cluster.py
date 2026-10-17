"""Four-cluster runs (hemaia_ci_4cluster): where a dead core's tasks go when one
victim follows another.

A scene with takeover_victims = [(chip, core, cluster), ...] (in fence order) is
judged by the substitute rule of the hardware (bingo_hw_manager_substitute_sel):
a task of logical slot (core, L) runs on (core, L) while that core lives;
otherwise on the same core of the lowest-numbered spare cluster (sub_spare of a
BINGO_SUB_CFG scene) that is still alive, else of the lowest-numbered other
cluster that is still alive (the clusters of hemaia_ci_4cluster are identical, so the same core index
is the same core type, and a cluster has one core of each device type). The
fence set at the time of each [BINGO_REPLAY] / [BINGO_REMAP] decides where the
task must go. The logical cluster of a task comes from the generated graph.
"""
import csv
from pathlib import Path
import re
from typing import Dict, List, Optional, Tuple

from bingo_log import CERF_FB_RE, CHECK_RE, DISPATCH_RE, REMAP_FIELDS_RE, RETIRED_RE, WD_RE

FOUR_CLUSTER_CFG = "target/rtl/cfg/hemaia_ci_4cluster.hjson"
NUM_CLUSTERS = 4
REPLAY_TIME_RE = re.compile(r"\[BINGO_REPLAY\] (\d+) chip=(\d+) task=(\d+) type=\d+ logical_core=(\d+) "
                            r"from=(\d+) to=(\d+) cluster=(\d+) to_cluster=(\d+)")
REMAP_TIME_RE = re.compile(r"\[BINGO_REMAP\] (\d+) chip=(\d+) task=(\d+) logical_core=(\d+) "
                           r"-> physical_core=(\d+) cluster=(\d+)")


def graph_slots(graph_csv) -> Dict[int, Tuple[int, int]]:
    """{task id: (cluster, core)} of the generated graph (final_dfg.csv)."""
    with Path(graph_csv).open() as stream:
        return {int(r["ID"]): (int(r["Cluster"]), int(r["Core"])) for r in csv.DictReader(stream)}


def substitute(core: int, logical: int, fenced: set, spare: int = 0) -> Optional[int]:
    """Cluster that runs logical slot (core, logical) with the cores in `fenced` dead,
    the spare clusters (bit mask) first."""
    if (core, logical) not in fenced:
        return logical
    for first in (True, False):
        for cl in range(NUM_CLUSTERS):
            if cl != logical and (core, cl) not in fenced and (not first or spare >> cl & 1):
                return cl
    return None


def fence_times(log_text: str) -> List[Tuple[int, int, int]]:
    """(time, core, cluster) of every first fence."""
    out, seen = [], set()
    for m in WD_RE.finditer(log_text):
        time, _, core, cluster, _, fenced = (int(x) if x is not None else 0 for x in m.groups())
        if fenced and (core, cluster) not in seen:
            seen.add((core, cluster))
            out.append((time, core, cluster))
    return out


def takeover_chain_problems(sc: dict, log_text: str, graph_csv) -> List[str]:
    problems: List[str] = []
    victims = [tuple(v) for v in sc["takeover_victims"]]
    if graph_csv is None or not Path(graph_csv).exists():
        return ["takeover_victims needs the generated graph (dispatch_log)"]
    slots = graph_slots(graph_csv)
    fences = fence_times(log_text)
    if [(0, core, cluster) for _, core, cluster in fences] != victims:
        problems.append(f"fences {fences}, expected the victims {victims} in this order")

    def fenced_at(time: int) -> set:
        return {(core, cluster) for t, core, cluster in fences if t <= time}

    moves = []
    for m in REPLAY_TIME_RE.finditer(log_text):
        time, chip, task, logical_core, _, to, _, to_cluster = map(int, m.groups())
        moves.append(("replay", time, task, logical_core, to, to_cluster))
    for m in REMAP_TIME_RE.finditer(log_text):
        time, chip, task, logical_core, physical, cluster = map(int, m.groups())
        moves.append(("remap", time, task, logical_core, physical, cluster))
    for kind, time, task, logical_core, core, cluster in moves:
        if task not in slots:
            problems.append(f"{kind} of task {task}: not in the generated graph")
            continue
        logical = slots[task][0]
        want = substitute(logical_core, logical, fenced_at(time), sc.get("sub_spare", 0))
        if (core, cluster) != (logical_core, want):
            problems.append(f"{kind} of task {task} (logical core {logical_core} cluster {logical}) at {time} "
                            f"to core {core} cluster {cluster}, expected cluster {want}")
    if not any(kind == "replay" and task == sc["fault_gid"] for kind, _, task, *_ in moves):
        problems.append(f"task {sc['fault_gid']} was not replayed")
    retired = [tuple(int(x) for x in m.groups()) for m in RETIRED_RE.finditer(log_text)]
    if retired != victims:
        problems.append(f"[BINGO_RETIRED] {retired}, expected {victims}")
    return problems


def fault_slot_problems(sc: dict, graph_csv) -> List[str]:
    """The fault task of the scene runs on the victim in the generated graph."""
    if sc.get("fault_stall_cycles") is None or "victim" not in sc or graph_csv is None:
        return []
    slots = graph_slots(graph_csv)
    _, core, cluster = sc["victim"]
    if slots.get(sc["fault_gid"]) != (cluster, core):
        return [f"fault task {sc['fault_gid']} is on {slots.get(sc['fault_gid'])} in the graph, "
                f"the victim is (cluster {cluster}, core {core})"]
    return []


# moe4_4cluster: expert e on cluster e is tasks 1 + 4e .. 4 + 4e (ldA, ldB, gemm, stD);
# the router selects experts 1 and 3; expert 1's GEMM falls back to expert 0.
MOE4_PRIMARY, MOE4_BACKUP, MOE4_SELECTED = 1, 0, (1, 3)


def moe4_tasks(expert: int) -> List[int]:
    return [1 + 4 * expert + k for k in range(4)]


def evaluate_moe4_cerf(sc: dict, log_text: str, uart_text: str) -> List[str]:
    """moe4: the router's experts 1 and 3 run and the others are skipped; with
    expert 1's GEMM core stuck (the victim is a GEMM core, core 0), one CERF
    fallback of type 1 moves expert 1's group to expert 0's, whose branch then
    runs (after the switch), and the host checks experts 0 and 3. A DM fault
    (core 1) does not touch the CERF: its tasks move to a substitute DM core
    (takeover checks), the experts stay 1 and 3. The GEMMs never leave their
    cluster (the GEMM type is bound to it)."""
    problems: List[str] = []
    fault = sc["fault_stall_cycles"] is not None and tuple(sc.get("victim", (0, 1, 0)))[1] == 0
    fb = [tuple(map(int, m.groups())) for m in CERF_FB_RE.finditer(log_text)]
    if len(fb) != int(fault) or any(e[1] != 1 for e in fb):
        problems.append(f"CERF fallback events {fb}, expected {'one of type 1' if fault else 'none'}")
    dispatch = {}
    for m in DISPATCH_RE.finditer(log_text):
        time, _, task, core, cluster = map(int, m.groups())
        dispatch.setdefault(task, []).append((time, core, cluster))
    ran = {e for e in range(4) if all(t in dispatch for t in moe4_tasks(e))}
    started = {e for e in range(4) if any(t in dispatch for t in moe4_tasks(e))}
    if fault:
        want_ran, want_started = {MOE4_BACKUP, 3}, {MOE4_BACKUP, MOE4_PRIMARY, 3}
    else:
        want_ran = want_started = set(MOE4_SELECTED)
    if ran != want_ran or started != want_started:
        problems.append(f"experts run {sorted(ran)} / started {sorted(started)}, expected "
                        f"{sorted(want_ran)} / {sorted(want_started)}")
    for e in started:
        gemm = moe4_tasks(e)[2]
        if any(cl != e for _, _, cl in dispatch.get(gemm, [])):
            problems.append(f"expert {e} GEMM task {gemm} ran outside cluster {e}: {dispatch[gemm]}")
    if fault and len(fb) == 1:
        switched = fb[0][0]
        if any(time <= switched for t in moe4_tasks(MOE4_BACKUP) for time, _, _ in dispatch.get(t, [])):
            problems.append("backup expert dispatched before the CERF fallback")
    checks = dict(CHECK_RE.findall(uart_text))
    want = {f"expert_{e}_D": "PASS" for e in want_ran}
    want["softmax"] = "PASS"
    if checks != want:
        problems.append(f"host checks {checks}, expected {want}")
    return problems
