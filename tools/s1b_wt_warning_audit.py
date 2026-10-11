"""Offline audit of every recorded WT warning, byte store, and LBU retirement."""
import hashlib
import json
from pathlib import Path
import re

W = Path(__file__).resolve().parents[1]
E = W / "evidence/GateCS1bR1"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    bins, = (W / "results/SystemS1bR4").glob("*/task_*/bin")
    log = bins / "sim_run.log"
    trace = bins / "logs/trace_chip_00_hart_00000.log"
    warnings = []
    lines = log.read_text().splitlines()
    for i, line in enumerate(lines):
        if "reading invalid data on port" in line:
            assert "data=xxxxxxxxxxxxxx00" in line and "port 1" in line
            stamp = int(re.search(r"Time: (\d+) ns", lines[i+1])[1])
            assert "a_invalid_read_data" in lines[i+1]
            warnings.append(dict(log_line=i+1, warning_ns=stamp, text=line,
                                 assertion_location=lines[i+1]))
    assert len(warnings) == 10
    loads, stores = [], []
    for i, line in enumerate(trace.open(), 1):
        if "PA: 00000070007f50" not in line:
            continue
        stamp = int(re.match(r"\s*(\d+)ns", line)[1])
        if "lbu " in line:
            assert "0000000080000922" in line and "a2  :0000000000000000" in line
            loads.append(dict(trace_line=i, ns=stamp, instruction=line.rstrip()))
        elif re.search(r"\bsb\s+x0, 360\(sp\)", line):
            stores.append(dict(trace_line=i, ns=stamp, instruction=line.rstrip()))
    assert len(loads) == len(stores) == 10
    names = ["input", "P0", "S0", "T0", "C0", "P1", "S1", "T1", "C1", "sink"]
    for warning, load, store, name in zip(warnings, loads, stores, names):
        assert warning["warning_ns"] + 56 == load["ns"]
        assert store["ns"] < warning["warning_ns"]
        warning.update(buffer=name, terminating_byte_store=store, byte_load_retirement=load,
                       response_to_retire_ps=56000)
    root = W / "evidence/PreparationS1bR4/HeMAiA/.bender/git/checkouts/cva6-9085b713cd7e9f8b"
    sources = [
        (root / "core/cache_subsystem/wt_cache_subsystem.sv", 293, 303),
        (root / "core/load_unit.sv", 475, 487),
        (W / "target/sw/shared/runtime/uart.c", 215, 238),
        (W / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_chain_sdc_recover_2cluster/main_bingo.py", 60, 70),
    ]
    claims = []
    for path, start, end in sources:
        lines = path.read_text().splitlines()
        claims.append(dict(path=str(path), sha256=sha(path), first_line=start, last_line=end,
                           excerpt="\n".join(lines[start-1:end])))
    result = dict(status="STOP_USER", classification="PRODUCT_SIDE_ASSERTION_AND_STACK_PADDING",
        count=10, warnings=warnings, sources=claims,
        inputs={str(p): sha(p) for p in (log, trace)},
        cause="Ten char hex[129] dumps store a NUL at 0x70007f50. vprintf LBU returns zero. "
              "The seven uninitialized bytes after the NUL are X in the raw 64-bit response. "
              "The WT assertion reduction checks the entire unmasked word, which yields X.",
        inference_limit="Retire trace and exact assertion identify the byte/whole-word mismatch. "
                        "No internal cache waveform exists; no claim of general memory safety or system PASS.",
        observer_side=False, product_changed=False, warning_waiver=False,
        user_action_required="Choose/authorize a product fix (e.g. initialize full padded dump buffer, "
                             "or validate the assertion against the requested load bytes). No fix applied.")
    (E / "wt_warning_root_cause.json").write_text(json.dumps(result, indent=2)+"\n")
    print("WT warnings: 10/10 store->response->LBU matches; product-side; STOP_USER")


if __name__ == "__main__":
    main()
