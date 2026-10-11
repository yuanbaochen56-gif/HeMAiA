"""Freeze exact recovery addresses, seven predictions and the unchanged R6 caps."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

W = Path(__file__).resolve().parents[1]
E = W / "evidence/PredictionsS1bR1"
sys.path[:0] = [str(W / "tools"), str(W / "target/sim/automation/test")]
from s1b_model_r6 import ByteEngine, Scheduler, byte_oracle, read_header
from bingo_sdc_recover_scenarios import recovery_scenarios


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def elf_symbols(path):
    data = Path(path).read_bytes()
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    assert data[:6] == b"\x7fELF\x02\x01"
    sections = [struct.unpack_from("<IIQQQQIIQQ", data, header[6] + i * header[11])
                for i in range(header[12])]
    symbols = {}
    for section in sections:
        if section[1] != 2:
            continue
        strings = sections[section[6]]
        names = data[strings[4]:strings[4] + strings[5]]
        for offset in range(section[4], section[4] + section[5], section[9]):
            name, info, other, index, address, size = struct.unpack_from("<IBBHQQ", data, offset)
            key = names[name:].split(b"\0", 1)[0].decode()
            if key:
                symbols[key] = dict(address=address, size=size)
    return symbols


def return_pcs(path, symbol):
    data = Path(path).read_bytes()
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    lo, hi = symbol["address"], symbol["address"] + symbol["size"]
    code = None
    for index in range(header[12]):
        section = struct.unpack_from("<IIQQQQIIQQ", data, header[6] + index * header[11])
        if section[3] <= lo and hi <= section[3] + section[5]:
            offset = section[4] + lo - section[3]
            code = data[offset:offset + symbol["size"]]
            break
    assert code is not None
    pcs, cursor = [], 0
    while cursor < len(code):
        half, = struct.unpack_from("<H", code, cursor)
        width = 4 if half & 3 == 3 else 2
        instruction = int.from_bytes(code[cursor:cursor + width], "little")
        if instruction in (0x8082, 0x8067):  # c.jr ra, jalr x0,0(ra)
            pcs.append(lo + cursor)
        cursor += width
    assert cursor == len(code) and pcs
    return pcs


def main():
    E.mkdir()
    build_path = W / "evidence/BuildS1bR2/progress.json"
    build = json.loads(build_path.read_text())
    assert build["status"] == "PASS" and len(build["images"]) == 17
    assert all(row["byte_identical"] and row["parts_byte_identical"] for row in build["images"][:-1])
    preparation_path = W / "evidence/PreparationS1bR3/progress.json"
    preparation = json.loads(preparation_path.read_text())
    assert preparation["status"] == "PASS_COMPILE_ONLY" and preparation["actual_system_runs"] == 0
    assert not (W / "evidence/SystemS1bR1").exists()
    assert json.loads((W / "evidence/ValidationS1bR2/result.json").read_text())["status"] == "PASS"
    row, = [row for row in build["images"] if row["mode"] == "recover"]
    assert row["warning_gate"]["rejected_c_warning_count"] == 0
    hardware = Path(preparation["product_build_copy"])
    cva6 = hardware / ".bender/git/checkouts/cva6-9085b713cd7e9f8b"
    cache_files = [
        hardware / "target/rtl/src/occamy_cva6.sv",
        hardware / "target/rtl/src/occamy_chip.sv",
        hardware / "target/sim/testharness/testharness.sv",
        hardware / "target/sim/testharness/util/check_finish_rtl.sv",
        cva6 / "core/include/ariane_pkg.sv",
        cva6 / "core/cache_subsystem/wt_dcache.sv",
        cva6 / "core/cache_subsystem/wt_dcache_wbuffer.sv",
        cva6 / "core/controller.sv",
    ]
    configuration = cache_files[0].read_text()
    assert "CVA6ConfigDcacheType = config_pkg::WT" in configuration
    assert "CVA6ConfigWtDcacheWbufDepth = 8" in configuration
    assert "INVALIDATE_ON_FLUSH = 1'b1" in cache_files[4].read_text()
    output = Path(row["output"])
    symbols = elf_symbols(output / "test_cfg_host.elf")
    returns = return_pcs(output / "test_cfg_host.elf",
                        symbols["__host_bingo_kernel_sdc_resolve_copy"])
    heap_start = (symbols["__l3_heap_start"]["address"] + 1023) & ~1023
    graph = json.loads((output / "generated/recovery_graph.json").read_text())
    header = output / "generated/offload_bingo_hw.h"
    descriptors = read_header(header)
    r6 = json.loads((W / "evidence/S1bPreflightR6/proposed_graph.json").read_text())
    assert [(node["id"], node["descriptor_hex"]) for node in graph["nodes"]] == [
        (node["id"], node["descriptor_hex"]) for node in r6["nodes"]]
    # Execute the actual fixed-width allocator, then cross-check the monotone fragment arithmetic.
    allocator = W / "target/sw/shared/vendor/bingo_alloc/bingo_alloc"
    (E / "uart.h").write_text("/* Allocator has no UART use in this replay. */\n")
    requests = [224, 112, 112, 64, 64, 8, *([64] * 7), 192]
    harness = E / "allocator_replay.c"
    harness.write_text(
        '#include <assert.h>\n#include <stdint.h>\n#include <stdio.h>\n#include <stdlib.h>\n'
        '#include "bingo_alloc.h"\nint main(void) { void *p; '
        'assert(posix_memalign(&p,1024,1048576)==0); uint64_t base=(uintptr_t)p; '
        'assert(bingoHeapInit(base,1048576)==base); '
        'printf("metadata %zu\\n",sizeof(BingoHeapInstance)); '
        'uint64_t sizes[]={' + ",".join(map(str, requests)) + '}; '
        'for (unsigned i=0;i<sizeof(sizes)/sizeof(sizes[0]);++i) '
        'printf("%u %lu\\n",i,bingoHeapMalloc(base,sizes[i])-base); free(p); }\n')
    with (E / "allocator_compile.log").open("x") as stream:
        subprocess.run(["gcc", "-D__riscv_xlen=64", "-Wall", "-Wextra", "-Werror",
                        "-I", str(E), "-I", str(allocator), str(harness),
                        str(allocator / "bingo_alloc.c"), "-o", str(E / "allocator_replay")],
                       stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=60)
    replay = subprocess.check_output([str(E / "allocator_replay")], text=True)
    (E / "allocator_replay.log").write_text(replay)
    lines = replay.splitlines()
    assert lines[0] == "metadata 560"
    cursor = 640
    offsets = []
    for index, amount in enumerate(requests):
        offsets.append(cursor + 128)
        assert lines[index + 1] == f"{index} {offsets[-1]}"
        cursor += (amount + 128 + 255) & ~255
    names = ["descriptors", "device_map", "host_map", "S0", "S1", "status",
             "C0", "C1", "P0", "P1", "T0", "T1", "sink", "state"]
    addresses = {name: heap_start + offset for name, offset in zip(names, offsets)}
    addresses["input"] = symbols["s1b_input"]["address"]
    assert addresses["input"] + 64 <= heap_start
    graph["status_addresses"] = [addresses["status"], addresses["status"] + 4]
    import runpy
    module = runpy.run_path(str(W / "target/sim/automation/test/3_start_bingo_watchdog_sim.py"))
    scenes = recovery_scenarios(module["SCENARIOS"]["tch0"],
                                json.loads((output / "generated/sdc_report.json").read_text()), graph)
    for scene in scenes.values():
        scene.update(timeout_cycles=None, confirm_timeout_cycles=None, extra_cfg=None, cfg_suffix="")
    negative = dict(scenes["s1b_healthy_on"], desc="same-image invalid-status safe rejection",
                    recovery_negative=True, recovery_correct=False, expect_eoc=False)
    scenes["s1b_invalid_status"] = negative
    initial = bytes(((i * 37 + 11) ^ (i >> 2)) & 255 for i in range(64))
    predictions = {}
    for name, scene in scenes.items():
        tx = scene["recovery_fault_tx"]
        faults = {} if tx is None else {
            ("S" if scene["recovery_fault_shadow"] else "P") + str(tx): [(17, 4)]}
        engine = ByteEngine(graph, initial, bool(scene["sdc_enable"]), faults,
                            status_override={0: 0xFFFFFFFF} if scene.get("recovery_negative") else None)
        model = Scheduler(descriptors, graph["roles"], engine.handler,
                          enabled=bool(scene["sdc_enable"]), group=graph["cerf_group"],
                          post_execute=lambda: 0 if bytes(engine.mem["sink"]) == initial else 2).run()
        oracle = None if scene.get("recovery_negative") else byte_oracle(
            initial, bool(scene["sdc_enable"]), faults)
        if oracle:
            assert list(engine.mem["sink"]) == oracle["sink"]
        done = {}
        for event in model["events"]:
            if event["kind"] in ("HOST_DONE_WRITE", "DEVICE_DONE_WRITE"):
                slot = ":".join(map(str, event["slot"]))
                done.setdefault(slot, []).append(event["task"])
        predictions[name] = dict(cfg=module["scenario_test_cfg"](scene), model=model,
                                 done_by_slot=done, states=engine.states, oracle=oracle,
                                 bytes={key: list(value) for key, value in engine.mem.items()})
    budgets = json.loads((W / "evidence/S1bPreflightR6/gate_proposal.json").read_text())["temporal_budgets"]
    # 8592 us in the archived R6 proposal was an unmeasured estimate.
    # The 18:30 CEST decision changes only the healthy envelope to 8700 us.
    from s1b_envelope_r7 import rebase_healthy_budget, HEALTHY_LIMIT_US
    budgets = rebase_healthy_budget(budgets)
    assert budgets["normal_all_healthy_envelope"] == HEALTHY_LIMIT_US
    assert budgets["normal_two_mismatch_envelope"] == 10640
    assert budgets["failure_after_FAIL_return_to_completion_word"] == 2048
    # Address geometry is the actual chip0 sixteen-bank 16MiB wide scratchpad.
    platform = Path(row["build_copy"]) / "target/sw/shared/platform/generated/occamy_base_addr.h"
    text = platform.read_text()
    match = re.search(r"#define SPM_WIDE_BASE_ADDR\s+\(?\s*(0x[0-9a-fA-F]+)", text)
    assert match, "Cannot bind actual wide-memory base"
    base = int(match[1], 16)
    offset = addresses["status"] - base
    assert 0 <= offset < 16777216 and offset % 8 == 0
    memory = ("sim:/testharness/i_dut/i_hemaia_0_0/i_occamy_chip/i_hemaia_mem_system/"
              f"i_hemaia_mem/gen_banks[{offset // 8 % 16}]/i_data_mem/i_tc_sram/sram[{offset // 128}]")
    manager = ("sim:/testharness/i_dut/i_hemaia_0_0/i_occamy_chip/i_occamy/"
               "i_occamy_soc/i_occamy_quad/i_occamy_quad_ctrl/i_bingo_hw_manager")
    envelope = dict(status="FROZEN_BEFORE_SYSTEM", sealed=datetime.datetime.now().astimezone().isoformat(),
                    simulations_before_seal=0, source_hashes=build["source_hashes"],
                    image=row, image_gate=dict(path=str(build_path), sha256=sha(build_path)),
                    preparation_gate=dict(path=str(preparation_path), sha256=sha(preparation_path)),
                    graph=graph, descriptors=descriptors, addresses=addresses, heap_start=heap_start,
                    allocator_requests=requests, allocator_offsets=offsets,
                    clocks=dict(master_period_ps=2000, host_mcycle_period_ps=28000,
                                manager_period_ps=28000, host_default_division=14),
                    cache=dict(kind="WT", write_buffer_depth=8, invalidate_on_flush=True,
                        visibility="Device wait/fence before DONE; host acquire fence invalidates WT lines and drains writes. Canonical publish fence precedes SUCC."),
                    simulation_guards=dict(master_max_cycles=15000000, absolute_timeout_us=30000,
                                           wall_timeout_seconds=3600),
                    host_resolver_return_pcs=returns,
                    completion_memory=("sim:/testharness/i_dut/i_hemaia_0_0/i_occamy_chip/"
                        "i_hemaia_mem_system/i_hemaia_mem/gen_banks[15]/i_data_mem/"
                        "i_tc_sram/sram[131071][63:32]"),
                    budgets=budgets, scenes=scenes, predictions=predictions,
                    release_observation=dict(manager_hierarchy=manager, threshold_ps=1000000,
                        method="Passive input-only bind plus Tcl clock samples; real SET/CHECK and checkout retirement. Earliest tag readiness, not delayed CHECK; previous executing DONE; start anchor for the sole no-dependency entry. No ingress/HOL exemption."),
                    negative_probe=dict(trigger=manager + "/i_s1b_readonly_observer/compare0_completed",
                        after_compare_task=3, before_resolver_task=4, status_addr=addresses["status"],
                        memory=memory, deposit_64bit_hex="00000000ffffffff",
                        expected=dict(failure=2, code=0xFFFFFFFF, third=0, commit=0, reports=1,
                                      resolver_done=False, downstream_dispatch=False, return_code=2,
                                      completion_word=5, simulator_fatal=True)),
                    completion_word_to_sim_end_ps=2000,
                    bindings={str(path): sha(path) for path in cache_files + [
                        output / "test_cfg_host.elf", output / "test_cfg_unpatched.bin", header,
                        output / "generated/recovery_graph.json", platform,
                        W / "tools/s1b_readonly_observer_r2.sv", W / "tools/s1b_observations.py",
                        W / "tools/s1b_system_r1.py",
                        W / "target/sim/automation/test/bingo_sdc_recover_check.py",
                        W / "tools/s1b_model_r6.py", W / "tools/s1b_envelope_r7.py", Path(__file__)]})
    write(E / "predictions.json", envelope)
    manifest = {str(path): sha(path) for path in E.iterdir() if path.is_file()}
    write(E / "manifest.json", manifest)
    write(E / "result.json", dict(status="PASS", task="S1b-2 pre-run prediction freeze",
                                  manifest_sha256=sha(E / "manifest.json"),
                                  predictions_sha256=sha(E / "predictions.json"),
                                  scenes=7, descriptors=len(descriptors), simulations=0))
    print("Predictions frozen", sha(E / "predictions.json"))


if __name__ == "__main__":
    main()
