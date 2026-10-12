"""Replay actual tagged SET/CHECK events without assigning DONE to skipped tasks."""
from collections import defaultdict


def readiness(text, descriptors, period_ps):
    by_id = {node["task"]: node for node in descriptors}
    grouped = defaultdict(list)
    for line in text.splitlines():
        if line.startswith("SCHEMA "):
            assert line in ("SCHEMA 1 C=3 CL=2 TAG=4",
                            "SCHEMA 2 C=3 CL=2 TAG=4 IDW=12 DW=62")
            continue
        fields = line.split()
        if not fields:
            continue
        stamp = int(fields[0])
        assert stamp >= 0
        grouped[stamp].append((fields[1], [int(value) for value in fields[2:]]))
    live = {}
    dep_ready = {}
    last_dispatch = {}
    done = {}
    dispatch = {}
    eligibility = {}
    retire = {}
    checks = []
    starts = []
    clock_edges = []
    sets = []
    for stamp, events in sorted(grouped.items()):
        # Checks see pre-edge registered tags; same-edge sets become visible after this edge.
        for kind, data in events:
            if kind == "START":
                assert not starts
                starts.append(stamp)
            elif kind == "CLOCK":
                clock_edges.append(stamp)
            elif kind == "CHECK":
                task, cluster, core, code, tag = data
                node = by_id[task]
                assert (cluster, core, code, tag) == (
                    node["cluster"], node["core"], node["check_code"], node["check_tag"])
                assert task not in dep_ready and node["check_en"]
                keys = [(cluster, core, col, tag) for col in range(3) if code & (1 << col)]
                assert keys and all(key in live for key in keys), ("Missing real tag", task, keys)
                dep_ready[task] = max(live.pop(key) for key in keys)
                checks.append(dict(task=task, check_ps=stamp, ready_ps=dep_ready[task], cells=keys))
        for kind, data in events:
            if kind == "SET":
                cluster, col, code, tag = data
                for row in range(3):
                    if code & (1 << row):
                        key = cluster, row, col, tag
                        assert key not in live, ("Live tag reuse", key, stamp)
                        live[key] = stamp
                sets.append(dict(stamp_ps=stamp, cluster=cluster, col=col, code=code, tag=tag))
            elif kind == "DONE":
                task, cluster, core = data
                assert task in dispatch and task not in done
                assert by_id[task]["cluster"] == cluster and by_id[task]["core"] == core
                assert dispatch[task] < stamp
                done[task] = stamp
        for kind, data in events:
            if kind == "DISPATCH":
                task, cluster, core = data
                node = by_id[task]
                assert task not in dispatch and node["type"] != 1
                assert (cluster, core) == (node["cluster"], node["core"])
                if node["check_en"]:
                    assert task in dep_ready
                    ready = dep_ready[task]
                else:
                    assert starts, "No observed start anchor"
                    ready = starts[0]
                slot = cluster, core
                if slot in last_dispatch:
                    older = last_dispatch[slot]
                    assert older in done, ("Overlapping execution", older, task)
                    ready = max(ready, done[older])
                eligibility[str(task)] = ready
                dispatch[task] = stamp
                last_dispatch[slot] = task
            elif kind == "CHECKOUT_RETIRE":
                task, cluster, core, actual_type = data
                assert task not in retire
                node = by_id[task]
                assert (cluster, core) == (node["cluster"], node["core"])
                if actual_type == 1:
                    assert node["type"] == 1 or node["cond"]
                    assert task not in dispatch and task not in done
                else:
                    assert task in done, "Executing checkout retired without DONE"
                retire[task] = dict(stamp_ps=stamp, type=actual_type)
            elif kind == "WAIT_RETIRE":
                task, core, cluster, skipped = data
                node = by_id[task]
                assert (cluster, core) == (node["cluster"], node["core"])
                assert skipped in (0, 1) and (not skipped or node["cond"])
            elif kind not in ("START", "CLOCK", "CHECK", "SET", "DONE"):
                raise AssertionError(("Unknown observed event", kind))
    waits = {str(task): dispatch[task] - eligibility[str(task)] for task in dispatch}
    # Capture the same absolute metric for both reference and recovery runs.
    # The frozen-reference checker applies the approved extra-wait cap.
    assert all(value >= 0 for value in waits.values()), ("Early dispatch", waits)
    if clock_edges:
        assert len(clock_edges) == 4
        assert all(b - a == period_ps for a, b in zip(clock_edges, clock_edges[1:]))
    return dict(status="PASS", eligibility=eligibility, dispatch=dispatch, done=done,
                waits_ps=waits, checks=checks, sets=sets, checkout_retire=retire,
                remaining_cells=sorted(live), start_ps=starts, clock_edges_ps=clock_edges)
