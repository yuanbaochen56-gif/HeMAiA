"""Write-once, same-graph reference for the approved S1b G-release metric."""
import datetime
import hashlib
import json
import os
from pathlib import Path

CAP_PS = 1_000_000
METRIC = "earliest-real-tag-and-slot-readiness-to-DISPATCH;START-entry;no-ingress-HOL-exemption;skip-is-not-DONE"


class ReferenceError(ValueError):
    """Unobservable or invalid reference: the runner must STOP_USER."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def reference_context(sealed):
    # Same graph, exact task IDs, software, hardware, cache and clocks. The
    # Scenario configuration and reference-only recovery control are provenance;
    # graph, task IDs, default image, platform and metric must remain identical.
    return {key: sealed[key] for key in
            ("graph", "descriptors", "source_hashes", "clocks", "cache")} | {
                "image_id": sealed["image"]["image_id"], "metric": METRIC}


def absolute_latencies(eligibility, dispatch):
    dispatch = {str(k): v for k, v in dispatch.items()}
    if set(eligibility) != set(dispatch):
        raise ReferenceError("Readiness does not cover the exact executing set")
    result = {}
    for task, stamp in dispatch.items():
        ready = eligibility[task]
        if type(ready) is not int or type(stamp) is not int or not 0 <= ready <= stamp:
            raise ReferenceError(f"Invalid readiness/DISPATCH for task {task}")
        result[task] = dict(eligible_ps=ready, dispatch_ps=stamp, latency_ps=stamp-ready)
    if not result:
        raise ReferenceError("Empty reference/observation is unobservable")
    return result


def valid_provenance(provenance):
    return (type(provenance.get("sdc_enable")) is int and provenance["sdc_enable"] == 1
            and type(provenance.get("recovery_enable")) is int and provenance["recovery_enable"] == 0
            and provenance.get("fault_tx") is None and provenance.get("negative") is False
            and type(provenance.get("recovery_runs_started")) is int
            and provenance["recovery_runs_started"] == 0
            and provenance.get("gates_status") == "PASS")


def freeze_reference(directory, eligibility, dispatch, context, provenance):
    """Only called after clean dual execution with recovery off, before any scene.

    Exclusive directory/files prevent reuse or replacement, including resume.
    The returned digest is held by the runner and recorded before scene launch.
    """
    if not valid_provenance(provenance):
        raise ReferenceError("Reference must be clean, SDC dual execution ON, recovery OFF and pre-scenario")
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
    return dict(path=str(path.resolve()), sha256=checksum, hash_path=str(hash_path.resolve()))


def load_reference(binding, context):
    """Verify pinned hash AND sidecar before parsing or comparing any task."""
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
        p = value["provenance"]
        if not valid_provenance(p):
            raise ReferenceError("Invalid reference provenance")
        return value
    except (OSError, KeyError, TypeError, ValueError) as error:
        raise ReferenceError(f"Frozen reference unavailable: {error}") from error


def compare_release(eligibility, dispatch, binding, context):
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
                cap_ps=CAP_PS, metric=METRIC, reference=dict(binding), tasks=rows)


def require_reference_tasks(binding, context, tasks):
    reference = load_reference(binding, context)
    missing = sorted(set(map(str, tasks)) - set(reference["tasks"]), key=int)
    if missing:
        raise ReferenceError(f"Missing reference tasks {missing}: unobservable; STOP_USER")
