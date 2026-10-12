"""Per-configuration frozen L_ref for the S1b G-release gate (R8).

A scene is compared only with the frozen non-recovery reference of its own
SDC configuration. SDC-on scenes keep the r7 SDC-on/recovery-off reference and
the unchanged bingo_sdc_recover_reference code path. SDC-off scenes use an
SDC-off reference; recovery is off there by construction because the init
kernel copies bingo_test_cfg.sdc_enable into each state's enabled field.
"""
import datetime
import json
import os
from pathlib import Path
import re

import bingo_sdc_recover_reference as base
from bingo_sdc_recover_reference import (CAP_PS, METRIC, ReferenceError, absolute_latencies,
                                         canonical, digest)

CONFIGURATIONS = {1: "sdc_on_recovery_off", 0: "sdc_off_recovery_off"}
# Sealed state ABI: enabled at +8, detect_code at +12 (see s1b_reference_control).
SDC_OFF_STATE_64BIT_HEX = "ffffffff00000000"
CAPTURE_MODE = "REFERENCE_CAPTURE_NO_COMPARISON"


def scene_configuration(scene, cfg):
    sdc = scene.get("sdc_enable")
    if type(sdc) is not int or sdc not in CONFIGURATIONS or cfg.get("sdc_enable") != sdc:
        raise ReferenceError("Scene SDC configuration is ambiguous: STOP_USER")
    return sdc


def valid_provenance(provenance, sdc_enable):
    if sdc_enable == 1:
        return base.valid_provenance(provenance)
    return (sdc_enable == 0
            and type(provenance.get("sdc_enable")) is int and provenance["sdc_enable"] == 0
            and type(provenance.get("recovery_enable")) is int and provenance["recovery_enable"] == 0
            and provenance.get("fault_tx") is None and provenance.get("negative") is False
            and type(provenance.get("recovery_runs_started")) is int
            and provenance["recovery_runs_started"] == 0
            and provenance.get("gates_status") == "PASS")


def binding_configuration(binding):
    sdc = binding.get("sdc_enable") if isinstance(binding, dict) else None
    if type(sdc) is not int or sdc not in CONFIGURATIONS:
        raise ReferenceError("Reference binding has no SDC configuration: STOP_USER")
    return sdc


def freeze_reference(directory, eligibility, dispatch, context, provenance):
    """Write-once SDC-off reference; same file format and rules as the r7 freeze."""
    sdc = provenance.get("sdc_enable")
    if sdc not in CONFIGURATIONS or not valid_provenance(provenance, sdc):
        raise ReferenceError("Reference must be clean, recovery OFF and pre-scenario")
    if sdc == 1:
        return dict(base.freeze_reference(directory, eligibility, dispatch, context, provenance),
                    sdc_enable=1)
    rows = absolute_latencies(eligibility, dispatch)
    directory = Path(directory)
    directory.mkdir()  # No exist_ok: never refreeze, even after partial failure.
    path = directory / "L_ref.json"
    value = dict(schema=1, metric=METRIC, frozen_at=datetime.datetime.now().astimezone().isoformat(),
                 context=context, context_sha256=digest(canonical(context)),
                 provenance=provenance, tasks=rows)
    data = json.dumps(value, indent=2, sort_keys=True).encode() + b"\n"
    with path.open("xb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    checksum = digest(data)
    hash_path = directory / "L_ref.sha256"
    with hash_path.open("x") as stream:
        stream.write(checksum + "  L_ref.json\n")
        stream.flush()
        os.fsync(stream.fileno())
    path.chmod(0o444)
    hash_path.chmod(0o444)
    return dict(path=str(path.resolve()), sha256=checksum, hash_path=str(hash_path.resolve()),
                sdc_enable=0)


def load_reference(binding, context):
    """Verify pinned hash, sidecar, read-only mode, context and configuration."""
    sdc = binding_configuration(binding)
    if sdc == 1:
        value = base.load_reference(binding, context)
    else:
        try:
            data = Path(binding["path"]).read_bytes()
            expected = binding["sha256"]
            sidecar = Path(binding["hash_path"]).read_text()
            if digest(data) != expected or sidecar != expected + "  L_ref.json\n":
                raise ReferenceError("Frozen L_ref SHA256 mismatch")
            if any(Path(binding[key]).stat().st_mode & 0o222 for key in ("path", "hash_path")):
                raise ReferenceError("Frozen L_ref and SHA256 sidecar must remain read-only")
            value = json.loads(data)
            if (value["schema"] != 1 or value["metric"] != METRIC
                    or value["context"] != context
                    or value["context_sha256"] != digest(canonical(context))):
                raise ReferenceError("Reference graph/platform/metric does not match")
        except (OSError, KeyError, TypeError, ValueError) as error:
            raise ReferenceError(f"Frozen reference unavailable: {error}") from error
    if not valid_provenance(value["provenance"], sdc) or value["provenance"]["sdc_enable"] != sdc:
        raise ReferenceError("Reference provenance differs from its declared configuration")
    return value


def capture_binding(sdc_enable):
    if sdc_enable != 0:
        raise ReferenceError("Only the SDC-off reference is captured in R8")
    return dict(capture=CAPTURE_MODE, sdc_enable=0)


def compare_release(eligibility, dispatch, binding, context):
    """Drop-in for compare_release that honours the binding's configuration.

    SDC-on bindings use the unchanged r7 function. A capture binding only
    validates the exact executing set; the caller records it as a capture.
    """
    sdc = binding_configuration(binding)
    if binding.get("capture") is not None:
        if binding != capture_binding(sdc):
            raise ReferenceError("Invalid reference capture binding")
        rows = absolute_latencies(eligibility, dispatch)
        return dict(status="PASS", mode=CAPTURE_MODE, cap_ps=CAP_PS, metric=METRIC,
                    configuration=CONFIGURATIONS[sdc], reference=None, tasks=rows)
    if sdc == 1:
        return dict(base.compare_release(eligibility, dispatch, binding, context),
                    configuration=CONFIGURATIONS[1])
    reference = load_reference(binding, context)
    rows = absolute_latencies(eligibility, dispatch)
    for task, row in rows.items():
        if task not in reference["tasks"]:
            raise ReferenceError(f"Missing reference task {task}: unobservable; STOP_USER")
        baseline = reference["tasks"][task]
        ref = baseline["latency_ps"]
        if (type(ref) is not int or ref < 0
                or baseline["dispatch_ps"] - baseline["eligible_ps"] != ref):
            raise ReferenceError(f"Invalid reference latency for task {task}")
        row.update(L_ref_ps=ref, extra_ps=max(0, row["latency_ps"]-ref))
        row["pass"] = row["extra_ps"] <= CAP_PS
    return dict(status="PASS" if all(r["pass"] for r in rows.values()) else "FAIL",
                cap_ps=CAP_PS, metric=METRIC, configuration=CONFIGURATIONS[0],
                reference=dict(binding), tasks=rows)


def require_reference_tasks(binding, context, tasks):
    reference = load_reference(binding, context)
    missing = sorted(set(map(str, tasks)) - set(reference["tasks"]), key=int)
    if missing:
        raise ReferenceError(f"Missing reference tasks {missing}: unobservable; STOP_USER")


def select_reference(bindings, scene, cfg):
    sdc = scene_configuration(scene, cfg)
    if sdc not in bindings:
        raise ReferenceError(f"{CONFIGURATIONS[sdc]} reference not frozen before scenario launch")
    if binding_configuration(bindings[sdc]) != sdc or bindings[sdc].get("capture") is not None:
        raise ReferenceError("Reference configuration differs from scene configuration")
    return bindings[sdc]


def sdc_off_control_tcl(probe):
    """Read-only check: both recovery states are disabled before any producer runs."""
    text = ('when -label S1B_REFERENCE_SDC_OFF {' + probe["trigger"] + ' == 1} {\n'
            '  echo "S1B_REFERENCE_TRIGGER time=[s1b_time_ps]"\n')
    for target in probe["targets"]:
        text += ('  set p {' + target["memory"] + '}\n'
                 '  set value [examine -radix hex $p]\n'
                 '  if {![regexp -nocase {^' + SDC_OFF_STATE_64BIT_HEX + '$} $value]} {\n'
                 '    error "SDC-off reference recovery state is not disabled"\n  }\n'
                 f'  echo "S1B_REFERENCE_SDC_OFF time=[s1b_time_ps] tx={target["tx"]} value=$value"\n')
    return text + '  nowhen S1B_REFERENCE_SDC_OFF\n}\n'


def check_sdc_off_control(log, sealed, probe, dispatch, done):
    trigger = re.findall(r"S1B_REFERENCE_TRIGGER time=(\d+)", log)
    rows = re.findall(r"S1B_REFERENCE_SDC_OFF time=(\d+) tx=(\d+) value=([0-9a-fA-F]+)", log)
    if len(trigger) != 1 or len(rows) != 2 or [int(row[1]) for row in rows] != [0, 1]:
        raise ReferenceError("Missing or duplicated physical recovery-state evidence")
    if "S1B_REFERENCE_OFF" in log:
        raise ReferenceError("SDC-off reference must not deposit recovery state")
    stamp = int(trigger[0])
    init = next(int(task) for task, role in sealed["graph"]["roles"].items() if role == "init")
    stages = sealed["graph"]["stages"]
    primaries = [stage["primary"] for stage in stages]
    disabled = [stage[key] for stage in stages for key in ("shadow", "compare")]
    if init not in done or any(task not in dispatch for task in primaries):
        raise ReferenceError("Reference init or primary execution is unobservable")
    if any(task in dispatch or task in done for task in disabled):
        raise ReferenceError("SDC-off reference executed a shadow or compare task")
    if not done[init] <= stamp < min(dispatch[task] for task in primaries):
        raise ReferenceError("Recovery-state check is outside the post-init/pre-execution safe point")
    if any(int(time) != stamp or value.lower() != SDC_OFF_STATE_64BIT_HEX for time, _, value in rows):
        raise ReferenceError("Recovery state is not disabled in the SDC-off reference")
    return dict(status="PASS", time_ps=stamp, probe=probe, sdc_enable=0, recovery_enable=0,
                deposits=0, observed_state_fields=["state0.enabled=0", "state1.enabled=0"])
