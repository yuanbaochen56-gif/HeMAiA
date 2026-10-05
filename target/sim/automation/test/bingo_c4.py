"""C4 recovery phases and same-image fault costs (DESIGN 9.24.5)."""
from fractions import Fraction
import re

from bingo_c2 import _parse, graph_tasks
from bingo_evlog import ITEM, check_evlog, simulation_events
from pm_ticks import integrate_ticks


def run_boundaries(log, uart, graph_csv):
    graph = graph_tasks(graph_csv)
    tasks, streams, pm = _parse(log)
    dispatches = [event for event in tasks if event["kind"] == "DISPATCH"]
    exits = [event for event in dispatches
             if graph[event["task"]]["kernel"] == "__host_bingo_kernel_exit"]
    evlog = re.findall(r"\[EVLOG_BEGIN\] (\d+)", log)
    eoc = re.findall(r"All chips finished successfully at (\d+)", log)
    if not dispatches or len(exits) != 1 or len(evlog) != 1 or len(eoc) != 1:
        raise ValueError("C4 requires dispatches and one host exit, EVLOG_BEGIN and successful EOC")
    start = dispatches[0]["time_ps"]
    return dict(T0_ps=start, T_exit_ps=exits[0]["time_ps"], T_evlog_ps=int(evlog[0]),
                T_eoc_ps=int(eoc[0]), makespan_hw_ps=exits[0]["time_ps"] - start,
                host_view_ps=int(evlog[0]) - start, n_dispatch=len(dispatches),
                n_done=len(tasks) - len(dispatches), evlog_items=len(ITEM.findall(uart)),
                graph=graph, task_events=tasks, streams=streams, pm=pm)


def _decoded_events(log, uart, slots, period):
    problems = check_evlog(log, uart, slots, period_ps=period)
    if problems:
        raise ValueError("C4 EVLOG correspondence failed: " + "; ".join(problems))
    items = [tuple((int(ts), int(code, 16), int(cluster), int(core), int(arg, 16)))
             for ts, code, cluster, core, arg in ITEM.findall(uart)]
    expected = simulation_events(log, slots)
    first = items[0]
    matching = [time for time, *signature in expected if tuple(signature) == first[1:]]
    if not matching:
        raise ValueError("First EVLOG item has no exact simulation anchor")
    offset = min(matching) - first[0] * period
    if any(int(ts*period + offset) != ts*period + offset for ts, *_ in items):
        raise ValueError("EVLOG time is not an integral picosecond")
    return [dict(time_ps=int(ts*period + offset), code=code, cluster=cluster, core=core, arg=arg,
                 raw=f"[EVLOG] ts={ts} code=0x{code:02x} slot={cluster}:{core} arg=0x{arg:04x}")
            for ts, code, cluster, core, arg in items]


def _unique_time(events, description):
    if len(events) != 1:
        raise ValueError(f"Expected one {description}, got {len(events)}")
    return events[0]["time_ps"]


def phase_metrics(log, uart, graph_csv, pm_config, family, fault_task, victim, substitute):
    """Use exact EVLOG times for suspect/fence/action, real dispatch/done for execution.

    Slots use the driver's (chip, core, cluster) order. Replay substitutes are
    explicit slots, never inferred from the static graph's logical placement.
    """
    run = run_boundaries(log, uart, graph_csv)
    slots = max(task["core"] for task in run["graph"].values()) + 1
    recorded = _decoded_events(log, uart, slots, pm_config.clock_period_ps)
    tasks = run["task_events"]

    def task_events(kind, slot=None, after=None):
        return [event for event in tasks if event["kind"] == kind and event["task"] == fault_task
                and (slot is None or (event["chip"], event["core"], event["cluster"]) == tuple(slot))
                and (after is None or event["time_ps"] >= after)]

    def segment(slot, replay, destination=None, host_add=False):
        chip, core, cluster = slot
        td = _unique_time(task_events("DISPATCH", slot), f"fault dispatch on {slot}")
        suspect = [event for event in recorded if event["code"] == 1
                   and (event["core"], event["cluster"]) == (core, cluster)
                   and event["time_ps"] >= td]
        fence = [event for event in recorded if event["code"] == 3
                 and (event["core"], event["cluster"]) == (core, cluster)
                 and event["time_ps"] >= td]
        ts, tf = _unique_time(suspect, "victim suspicion"), _unique_time(fence, "victim fence")
        actions = [event for event in recorded if event["time_ps"] >= tf and (
            event["code"] == 4 and (event["core"], event["cluster"]) == (core, cluster)
            and event["arg"] & 0xFFF == fault_task if replay else event["code"] == 10)]
        if not actions:
            raise ValueError("No post-fence recovery action")
        ta = min(event["time_ps"] for event in actions)
        if replay:
            rd = _unique_time(task_events("DISPATCH", destination, ta), "replayed task dispatch")
            done = task_events("DONE", destination, rd)
            # The first stage of the two-dead-core case intentionally has no DONE.
            rdone = _unique_time(done, "replayed task DONE") if family != "hfb_both" else None
        else:
            candidates = [event for event in tasks if event["kind"] == "DISPATCH"
                          and event["time_ps"] >= ta]
            if host_add:
                candidates = [event for event in candidates
                              if run["graph"][event["task"]]["kernel"] == "__host_bingo_kernel_add_i32"]
            if not candidates:
                raise ValueError("No post-action dispatch")
            rd, rdone = min(event["time_ps"] for event in candidates), None
        if not td <= ts <= tf <= ta <= rd <= run["T_exit_ps"]:
            raise ValueError("C4 recovery phase times are not ordered")
        result = dict(victim=list(slot), substitute=list(destination) if destination else None,
                      t_d_ps=td, t_s_ps=ts, t_f_ps=tf, t_act_ps=ta, t_rd_ps=rd,
                      t_rdone_ps=rdone, T_exit_ps=run["T_exit_ps"], T_evlog_ps=run["T_evlog_ps"],
                      detect_ps=ts-td, fence_total_ps=tf-td, confirm_ps=tf-ts,
                      act_ps=ta-tf, restart_ps=rd-ta, host_tail_ps=run["T_evlog_ps"]-run["T_exit_ps"],
                      exec_ps=rdone-rd if rdone is not None else None,
                      tail_ps=run["T_exit_ps"]-rdone if rdone is not None else None,
                      rest_ps=run["T_exit_ps"]-rd if not replay else None)
        domain = pm_config.domain(core, cluster)
        pm_events = run["pm"].get(domain, [])
        for stage, left, right in (("detect", td, ts), ("fence_total", td, tf)):
            ticks = integrate_ticks(left, right, pm_events, pm_config)
            result[stage + "_ticks"] = ticks["ticks"]
            result[stage + "_ticks_exact"] = ticks["ticks_exact"]
        for stage in ("confirm", "act", "restart", "exec", "tail", "rest", "host_tail"):
            value = result[stage + "_ps"]
            nominal = Fraction(value, 1) / pm_config.clock_period_ps if value is not None else None
            result[stage + "_quad_ticks"] = float(nominal) if nominal is not None else None
            result[stage + "_quad_ticks_exact"] = (
                dict(numerator=nominal.numerator, denominator=nominal.denominator) if nominal is not None else None)
        return result

    replay = family in ("H", "A5", "A2", "hfb_one", "hfb_both")
    phases = [segment(tuple(victim), replay, tuple(substitute) if substitute else None)]
    if family == "hfb_both":
        phases.append(segment(tuple(substitute), False, host_add=True))
    first_dispatch = phases[0]["t_d_ps"]
    timeline = []
    for line in log.splitlines():
        match = re.search(r"\[BINGO_[A-Z0-9_]+\] (\d+)", line)
        if match:
            time = int(match[1])
            timeline.append(dict(time_ps=time, rel_ps=time-first_dispatch, source="sim", line=line))
    for event in recorded:
        timeline.append(dict(time_ps=event["time_ps"], rel_ps=event["time_ps"]-first_dispatch,
                             source="evlog", line=event["raw"]))
    timeline.sort(key=lambda row: row["time_ps"])
    return dict(**{key: value for key, value in run.items()
                   if key not in ("graph", "task_events", "streams", "pm")},
                phases=phases, events=timeline)


def control_cost(fault_run, control_run):
    """Subtract absolute times only after image ID and fault-field protection."""
    if fault_run["image_id"] != control_run["image_id"]:
        raise ValueError("C4 fault/control image IDs differ")
    fields = ("fault_cluster", "fault_core", "fault_stall_cycles",
              "fault_pre_stall_cycles", "fault_after_kernel")
    first, second = fault_run["test_cfg_json"], control_run["test_cfg_json"]
    if any(first[field] != second[field] for field in fields):
        raise ValueError("C4 fault/control fields differ beyond fault_gid")
    return dict(cost_hw_ps=fault_run["T_exit_ps"]-control_run["T_exit_ps"],
                cost_host_ps=fault_run["T_evlog_ps"]-control_run["T_evlog_ps"])
