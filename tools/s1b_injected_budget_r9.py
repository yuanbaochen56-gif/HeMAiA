"""R9: frozen kernel budget for the single fault-injected producer of an injected scene.

The S1a hook bingo_sdc_inject() runs inside the injected task's DISPATCH->DONE
window: after the copy kernel returns it does a 1-byte DMA read, a 1-byte DMA
write and one printf_safe line. The budget is built from the hook's work and the
already frozen per-operation allowances, not from the observed duration:

    copy kernel             device_dma_each                 (unchanged 64 us)
    two 1-byte DMA + wait   2 * device_dma_each
    one printf_safe line    device_exit_each * N_inject / N_exit

device_exit_each is the frozen allowance for a device kernel whose work is one
printf_safe line of N_exit characters. R8 physical traces measure the hook cost
and a healthy-only per-character model predicts it; both are recorded and
checked against the budget before any simulation. Every other task keeps its
frozen kernel bound.
"""
import ast
import datetime
import json
import math
import os
from pathlib import Path
import re

from bingo_sdc_check import INJECT
from bingo_sdc_recover_check import EVENT
from s1b_system_r1 import sha

W = Path(__file__).resolve().parents[1]
HOOK_SOURCE = "target/sw/device/runtime/src/bingo_sdc_inject.h"
EXIT_SOURCE = "target/sw/device/apps/snax/snax-bingo-offload/libsnaxkernel/offload_hw_kernels/basic.h"
INJECT_FORMAT = r"[BINGO_SDC_INJECT] gid=%u cluster=%u core=%u offset=%u mask=%u\r\n"
EXIT_FORMAT = r"[Cluster %d Core %d]: Exiting with code %d\r\n"
# Testharness (non-PLL): 500 MHz peripheral clock; init_uart(addr, 32, 1) -> divisor 2.
UART_CHAR_PS = 10 * 16 * 2 * 2000
BASE_KEY = "device_dma_each"
LINE_KEY = "device_exit_each"
LABEL = "injected_task"
R8_RESULTS = W / "results/GateS1bR8"
R8_HEALTHY_RUNS = ("s1b_reference_sdc_off", "s1b_healthy_off", "s1b_healthy_on")


class BudgetError(ValueError):
    pass


def rendered_length(fmt, values):
    """Length of the printf_safe output for %u/%d with the given integer values."""
    text = fmt.replace(r"\r", "\r").replace(r"\n", "\n")
    parts = re.split(r"%[ud]", text)
    if len(parts) != len(values) + 1:
        raise BudgetError("Format/value count mismatch")
    return sum(map(len, parts)) + sum(len(str(int(v))) for v in values)


def source_formats(sealed):
    """The two format strings, read from the sealed sources."""
    for path, fmt in ((HOOK_SOURCE, INJECT_FORMAT), (EXIT_SOURCE, EXIT_FORMAT)):
        if sha(W / path) != sealed["source_hashes"][path]:
            raise BudgetError(f"Sealed source changed: {path}")
        if (W / path).read_text().count('"' + fmt + '"') != 1:
            raise BudgetError(f"Format string not found exactly once in {path}")
    return dict(inject=INJECT_FORMAT, exit=EXIT_FORMAT)


def injected_site(scene):
    """(task, cluster, core, offset, mask) of the single injected task, or None."""
    if scene.get("recovery_fault_tx") is None:
        return None
    return tuple(int(scene[key]) for key in ("fault_sdc_gid", "fault_sdc_cluster", "fault_sdc_core",
                                             "fault_sdc_offset", "fault_sdc_mask"))


def events(log):
    dispatch, done, slot = {}, {}, {}
    for kind, stamp, _, task, core, cluster in EVENT.findall(log):
        (dispatch if kind == "DISPATCH" else done)[int(task)] = int(stamp)
        if kind == "DONE":
            slot[int(task)] = (int(cluster), int(core))
    return dispatch, done, slot


def dma_hart(cluster, core):
    # Hart 0 is the host; each cluster has two cores (core 1 is the DM core).
    return 1 + 2 * cluster + core


def dma_requests(path):
    """[(request_ps, length, busy_end_ps)] from a backend DMA trace (time in ns)."""
    rows, busy = [], False
    for line in Path(path).read_text().splitlines():
        entry = ast.literal_eval(line)
        stamp = entry["meta"]["time"] * 1000
        backend = entry["backend"]
        if backend["req_valid"] and backend["req_ready"]:
            rows.append([stamp, backend["req_length"], None])
        now = any(entry["busy"].values())
        if busy and not now:
            for row in rows:
                if row[2] is None:
                    row[2] = stamp
        busy = now
    return [tuple(row) for row in rows]


def decompose(bins, task, cluster, core, dispatch, done, injected):
    """Split one producer window into kernel copy, hook DMA and the rest, from the DMA trace."""
    trace = Path(bins) / f"dma_trace_{dma_hart(cluster, core):05d}_00000.log"
    rows = [row for row in dma_requests(trace) if dispatch <= row[0] <= done]
    lengths = [row[1] for row in rows]
    if lengths != ([64, 1, 1] if injected else [64]) or any(row[2] is None for row in rows):
        raise BudgetError(f"Unexpected DMA requests in task {task} window: {lengths}")
    copy_end = rows[0][2]
    result = dict(task=task, cluster=cluster, core=core, trace=str(trace), duration_ps=done - dispatch,
                  dispatch_to_copy_end_ps=copy_end - dispatch, requests=rows)
    if injected:
        result.update(copy_end_to_hook_read_request_ps=rows[1][0] - copy_end,
                      hook_dma_read_modify_write_ps=rows[2][2] - rows[1][0],
                      hook_write_end_to_done_ps=done - rows[2][2])
    else:
        result.update(copy_end_to_done_ps=done - copy_end)
    return result


def run_dir(root, name):
    bins, = Path(root, name).glob("task_*/bin")
    return bins


def healthy_print_rates(root=R8_RESULTS, runs=R8_HEALTHY_RUNS):
    """Per-character cost of one device printf_safe line from the healthy device-exit tasks."""
    samples = []
    for name in runs:
        bins = run_dir(root, name)
        log = (bins / "sim_run.log").read_text()
        uart = (bins / "uart_chip_0_0.log").read_text()
        if INJECT.findall(uart):
            raise BudgetError("Healthy print-rate source contains an injection")
        dispatch, done, slot = events(log)
        for task in (11, 12, 13, 14):
            cluster, core = slot[task]
            chars = rendered_length(EXIT_FORMAT, (cluster, core, 0))
            if uart.count(f"[Cluster {cluster} Core {core}]: Exiting with code 0") != 1:
                raise BudgetError("Exit line missing from UART")
            samples.append(dict(run=name, task=task, cluster=cluster, core=core, chars=chars,
                                duration_ps=done[task] - dispatch[task],
                                ps_per_char=(done[task] - dispatch[task]) / chars))
    return samples


def r8_measurement(root=R8_RESULTS):
    """Hook cost of the R8 stage0_primary_on producer, measured from its physical traces."""
    injected = run_dir(root, "s1b_stage0_primary_on")
    healthy = run_dir(root, "s1b_healthy_on")
    inj_log, ref_log = (injected / "sim_run.log").read_text(), (healthy / "sim_run.log").read_text()
    (inj_d, inj_e, inj_slot), (ref_d, ref_e, ref_slot) = events(inj_log), events(ref_log)
    task, cluster, core = 1, 0, 1
    if inj_slot[task] != (cluster, core) or ref_slot[task] != (cluster, core):
        raise BudgetError("R8 producer slot differs")
    hook = decompose(injected, task, cluster, core, inj_d[task], inj_e[task], True)
    base = decompose(healthy, task, cluster, core, ref_d[task], ref_e[task], False)
    return dict(
        source=dict(injected=str(injected), healthy=str(healthy),
                    injected_log_sha256=sha(injected / "sim_run.log"),
                    healthy_log_sha256=sha(healthy / "sim_run.log")),
        injected=hook, healthy_same_task=base,
        hook_cost_ps=hook["duration_ps"] - base["duration_ps"],
        note=("Device instruction traces are off. The DMA trace fixes the copy and the two hook "
              "transfers; the time after the hook write is the printf_safe line plus the normal "
              "end-of-task heartbeat and DONE write."))


def derive(sealed, measurement, rates):
    """Per-scene budgets from frozen allowances, plus the pre-run consistency checks."""
    formats = source_formats(sealed)
    bounds = sealed["budgets"]["kernel_bounds"]
    base_us, line_us = bounds[BASE_KEY], bounds[LINE_KEY]
    if (base_us, line_us) != (64, 512):
        raise BudgetError("Frozen kernel bounds differ from R6/R8")
    exit_chars = rendered_length(EXIT_FORMAT, (0, 0, 0))
    worst = max(rates, key=lambda row: row["ps_per_char"])
    fastest = min(rates, key=lambda row: row["ps_per_char"])
    scenes = {}
    for name, scene in sealed["scenes"].items():
        site = injected_site(scene)
        if site is None:
            continue
        task, cluster, core, offset, mask = site
        role = sealed["graph"]["roles"][str(task)]
        node = next(node for node in sealed["graph"]["nodes"] if node["id"] == task)
        if not role.startswith(("P", "S")) or node["kernel"] != "__snax_bingo_kernel_idma_1d_copy":
            raise BudgetError(f"{name}: injected task is not a device DMA producer")
        chars = rendered_length(INJECT_FORMAT, site)
        exact_us = base_us + 2 * base_us + line_us * chars / exit_chars
        budget_us = math.ceil(exact_us)
        # Healthy-only prediction of the hook: R8 pre-hook + DMA time plus chars * worst rate.
        hook_dma_ps = (measurement["injected"]["copy_end_to_hook_read_request_ps"]
                       + measurement["injected"]["hook_dma_read_modify_write_ps"])
        predicted_hook_ps = hook_dma_ps + chars * worst["ps_per_char"]
        scenes[name] = dict(
            task=task, cluster=cluster, core=core, role=role, kernel=node["kernel"],
            inject_line_chars=chars, budget_us=budget_us, budget_ps=budget_us * 1_000_000,
            exact_us=exact_us,
            terms_us=dict(copy_kernel=base_us, hook_two_one_byte_dma=2 * base_us,
                          hook_print_line=line_us * chars / exit_chars),
            predicted_hook_ps=dict(low=hook_dma_ps + chars * fastest["ps_per_char"],
                                   high=predicted_hook_ps),
            uart_line_rate_ps=chars * UART_CHAR_PS)
        if budget_us * 1_000_000 - base_us * 1_000_000 < predicted_hook_ps:
            raise BudgetError(f"{name}: hook allowance below the healthy-rate prediction")
    names = sorted(scenes)
    if names != ["s1b_stage0_primary_off", "s1b_stage0_primary_on", "s1b_stage0_shadow_on",
                 "s1b_stage1_primary_on"]:
        raise BudgetError("Injected scene set differs from the frozen family")
    return dict(
        schema=1, label=LABEL, base_key=BASE_KEY, line_key=LINE_KEY,
        rule=("budget_us = ceil(device_dma_each + 2*device_dma_each + device_exit_each*N_inject/N_exit); "
              "applies only to the task named here, only when its DONE slot is the injected cluster:core "
              "and the UART carries exactly that one [BINGO_SDC_INJECT] line. Every other task, including "
              "every other DMA, keeps its frozen kernel bound (device_dma_each = 64 us)."),
        rationale=dict(
            copy_kernel="The injected task still runs the same idma_1d_copy kernel; it keeps the 64 us allowance.",
            hook_dma=("bingo_sdc_inject() starts and waits for two 1-byte DMA transfers (read, then XOR "
                      "write-back); each gets the frozen per-DMA-task allowance."),
            hook_print=(f"The hook prints one printf_safe line of N_inject characters ({INJECT_FORMAT!r}); "
                        f"device_exit_each is the frozen allowance for a device kernel whose work is one "
                        f"printf_safe line of N_exit={exit_chars} characters ({EXIT_FORMAT!r}), scaled per character."),
            uart=("The UART line rate is not the limit: 500 MHz peripheral clock, divisor 2, 8N1 gives "
                  f"{UART_CHAR_PS} ps per character. Device prints cost several us per character, which is "
                  "core-side printf_safe/MMIO time (inferred; device instruction traces are off)."),
            measured=("R8 traces: the hook adds the measured hook_cost_ps to the same task's healthy "
                      "duration. The budget is not derived from this number; it is only required to stay "
                      "above the healthy-only prediction.")),
        exit_line_chars=exit_chars, formats=formats,
        frozen_allowances_us={BASE_KEY: base_us, LINE_KEY: line_us},
        healthy_print_rates=rates, worst_ps_per_char=worst["ps_per_char"],
        fastest_ps_per_char=fastest["ps_per_char"],
        r8_measurement=measurement, scenes=scenes,
        sources={path: sealed["source_hashes"][path] for path in (HOOK_SOURCE, EXIT_SOURCE)})


def freeze(directory, record):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "injected_task_budgets.json"
    record = dict(record, frozen_at=datetime.datetime.now().astimezone().isoformat())
    data = json.dumps(record, indent=2, sort_keys=True).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    hash_path = directory / "injected_task_budgets.sha256"
    with hash_path.open("x") as stream:
        stream.write(sha(path) + "  injected_task_budgets.json\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path), sha256=sha(path), hash_path=str(hash_path))


def load(binding):
    path, hash_path = Path(binding["path"]), Path(binding["hash_path"])
    if sha(path) != binding["sha256"] or hash_path.read_text() != binding["sha256"] + "  injected_task_budgets.json\n":
        raise BudgetError("Frozen injected-task budget SHA256 mismatch")
    if any(p.stat().st_mode & 0o222 for p in (path, hash_path)):
        raise BudgetError("Frozen injected-task budget must stay read-only")
    record = json.loads(path.read_text())
    if record["schema"] != 1 or record["label"] != LABEL or record["base_key"] != BASE_KEY:
        raise BudgetError("Unknown injected-task budget record")
    return record


def apply(judged, name, scene, log, uart, sealed, record):
    """Replace only the injected producer's device_dma_each check by its frozen budget."""
    judged = dict(judged, problems=list(judged["problems"]))
    site = injected_site(scene)
    declared = record["scenes"].get(name)
    if site is None:
        if declared is not None:
            raise BudgetError(f"{name}: budget declared for a scene without injection")
        judged["injected_task_budget"] = None
        return judged
    task, cluster, core, offset, mask = site
    if declared is None or (declared["task"], declared["cluster"], declared["core"]) != (task, cluster, core):
        raise BudgetError(f"{name}: injected task differs from the frozen declaration")
    dispatch, done, slot = events(log)
    if task not in dispatch or task not in done:
        raise BudgetError(f"{name}: injected task never completed")
    duration = done[task] - dispatch[task]
    base_ps = sealed["budgets"]["kernel_bounds"][BASE_KEY] * 1_000_000
    label = f"Task{task} {BASE_KEY} budget exceeded"
    if (label in judged["problems"]) != (duration > base_ps):
        raise BudgetError(f"{name}: unchanged judge disagrees with the 64 us check")
    activations = [tuple(map(int, row)) for row in INJECT.findall(uart)]
    observed = slot[task] == (cluster, core) and activations == [site]
    if observed:
        judged["problems"] = [p for p in judged["problems"] if p != label]
        if duration > declared["budget_ps"]:
            judged["problems"].append(f"Task{task} {LABEL} budget exceeded")
    judged["injected_task_budget"] = dict(
        task=task, cluster=cluster, core=core, applied=observed, budget_us=declared["budget_us"],
        measured_ps=duration, measured_us=duration / 1_000_000,
        passed=observed and duration <= declared["budget_ps"])
    judged["status"] = "FAIL" if judged["problems"] else "PASS"
    return judged
