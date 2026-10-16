"""Fence fallback: core types whose fenced cores leave their tasks to host backups.

A fenced core's tasks normally go to a live substitute of its type. With the
type's bit in the fence-fallback register the manager gives it no substitute,
so the fence makes the slot stuck and the CERF degradation of the type runs
the host backups. That pays off when the host is the cheaper executor.

A type's bit is set only when, on that chip,
- the type's CERF degradation entry is registered,
- every task of the type has a host backup (exits aside: the degradation
  skips them), and
- every one of them has a measured cost below, at its size, with the host
  backup strictly cheaper than the device.
The degradation switches the whole type, so the live cores of the type hand
their remaining tasks to the host as well. The host therefore has to beat both
the substitute (tasks of the fenced core) and the home core (tasks of a live
one). Anything else leaves the bit off: a kernel or a size without a
measurement keeps the substitute, and type 0 never substitutes.

Costs are DISPATCH to DONE of one task on hemaia_ci, in microseconds:
- int32 add, 512 elements: home core 1817.088 (c4_hfb_one_ok), level-2
  substitute 2290.400 (c4_hfb_one), host backup 287.112 (c4_hfb_both).
- iDMA copy, 256 B: home core 2.744 (c4_chain_h100_ok), level-2 substitute
  9.380-9.632 (c4_chain_h100), host backup 10.668, the first one 13.356
  (p7b_chain_l1_g0_pre). The row holds the slowest substitute and the fastest
  host sample: the host still loses.
"""

EXIT_KERNELS = ("__snax_bingo_kernel_exit", "__host_bingo_kernel_exit")

# kernel -> (size argument, measured size, home core us, level-2 substitute us, host backup us)
KERNEL_COST_US = {
    "__snax_bingo_kernel_int32_add": ("num_elements", 512, 1817.088, 2290.400, 287.112),
    "__snax_bingo_kernel_idma_1d_copy": ("size", 256, 2.744, 9.632, 10.668),
}


# Task-time mode (BINGO_TASK_TIME, bingo_cost.py): a size without a row above may use
# the estimates, with this margin: the host must take below MARGIN times the device
MODEL_MARGIN = 0.8


def model_is_cheaper(node, graph) -> str:
    """Empty when the task-time estimates make the host backup cheaper, else the reason.
    The home core's estimate (warm, the longer cold one for the first execution of
    its kernel) stands for the device: a substitute is never faster than the home
    core (1.14 .. 3.5x slower in the measurements), so the host must take below
    MODEL_MARGIN times the home core's time."""
    import bingo_cost
    from bingo_dfg import _HOST_FALLBACK_KERNELS
    backup = _HOST_FALLBACK_KERNELS.get((node.kernel_name, type(node.kernel_args)))
    if backup is None:
        return f"{node.kernel_name}: no host backup kernel"
    family, key, work, ints = bingo_cost.node_key(node)
    ticks, source = bingo_cost.estimate(node.kernel_name, family, key, work, False,
                                        graph._task_time_platform)
    if ticks is None:
        return f"{node.kernel_name}: no device estimate ({source})"
    host = bingo_cost.host_us(backup[0], ints)
    if host is None:
        return f"{node.kernel_name}: no host estimate for {backup[0]} at this size"
    device = bingo_cost.ticks_to_us(ticks)
    if not host < MODEL_MARGIN * device:
        return (f"{node.kernel_name}: host {host} us is not below {MODEL_MARGIN} x "
                f"the device estimate {device:.3f} us")
    return ""


def host_is_cheaper(node, graph=None) -> str:
    """Empty when the node has a measured cost with the host cheaper, else the reason.
    In task-time mode (GRAPH with task_time) a size without a measured row may use
    the estimates (model_is_cheaper)."""
    row = KERNEL_COST_US.get(node.kernel_name)
    if row is None:
        if graph is not None and getattr(graph, "task_time", False):
            return model_is_cheaper(node, graph)
        return f"{node.kernel_name}: no measured cost"
    argument, size, home, substitute, host = row
    actual = getattr(node.kernel_args, argument, None)
    if actual != size:
        if graph is not None and getattr(graph, "task_time", False):
            return model_is_cheaper(node, graph)
        return f"{node.kernel_name}: measured at {argument} {size}, not {actual}"
    if not host < min(home, substitute):
        return (f"{node.kernel_name}: host {host} us is not cheaper than "
                f"home {home} us and substitute {substitute} us")
    return ""


def fence_fb_report(graph, chip: int) -> list[dict]:
    """One row per non-zero core type with tasks on the chip, after compilation."""
    backups = set(graph._host_fallbacks.values())
    grouped: dict[int, list] = {}
    for node in graph.node_list:
        if (node.assigned_chiplet_id != chip or not node.kernel_name
                or node.kernel_name in EXIT_KERNELS or node in backups):
            continue
        type_id = graph.core_type_ids.get((node.assigned_cluster_id, node.assigned_core_id))
        if isinstance(type_id, int) and 0 < type_id < 32:
            grouped.setdefault(type_id, []).append(node)

    report = []
    for type_id, nodes in sorted(grouped.items()):
        reasons = []
        if (chip, type_id) not in graph._cerf_fallback_table:
            reasons.append("no CERF degradation entry")
        for node in nodes:
            if node not in graph._host_fallbacks:
                reasons.append(f"{node.node_name}: no host backup")
            else:
                reason = host_is_cheaper(node, graph)
                if reason:
                    reasons.append(reason)
        report.append(dict(chip=chip, core_type=type_id, enable=not reasons, reasons=reasons,
                           kernels=sorted({node.kernel_name for node in nodes})))
    return report


def fence_fb_types(graph, chip: int) -> list[int]:
    """Core types of the chip whose bit the generated code sets, in ascending order."""
    return [row["core_type"] for row in fence_fb_report(graph, chip) if row["enable"]]
