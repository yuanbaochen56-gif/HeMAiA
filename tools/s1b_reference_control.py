"""Reference-only runtime control; the default image and descriptor graph stay fixed."""
import copy
import re

from bingo_sdc_recover_reference import ReferenceError


def reference_scene(sealed):
    scene = copy.deepcopy(sealed["scenes"]["s1b_healthy_on"])
    cfg = sealed["predictions"]["s1b_healthy_on"]["cfg"]
    if (scene["sdc_enable"] != 1 or cfg["sdc_enable"] != 1
            or scene["recovery_fault_tx"] is not None or scene.get("recovery_negative")
            or scene["recovery_graph"] != sealed["graph"]):
        raise ReferenceError("No clean same-graph dual-execution reference scene")
    scene.update(recovery_enable=0, desc="SDC dual execution ON, recovery OFF reference")
    return scene


def reference_probe(sealed):
    # Reuse the sealed sixteen-bank wide-memory geometry, never a stale address.
    pattern = r"(.*)/gen_banks\[(\d+)\]/i_data_mem/i_tc_sram/sram\[(\d+)\]"
    match = re.fullmatch(pattern, sealed["negative_probe"]["memory"])
    if not match:
        raise ReferenceError("Missing sealed wide-memory geometry")
    prefix, bank, row = match.groups()
    base = sealed["addresses"]["status"] - (int(row) * 128 + int(bank) * 8)
    state = sealed["addresses"]["state"]
    if not 0 <= int(bank) < 16 or base % (16 * 1024**2) or state % 8:
        raise ReferenceError("Invalid recovery-state memory geometry")
    targets = []
    for tx in (0, 1):
        address = state + tx * 96 + 8  # Sealed ABI: enabled at 8, detect_code at 12.
        offset = address - base
        if not 0 <= offset < 16 * 1024**2:
            raise ReferenceError("Recovery state outside sealed wide scratchpad")
        targets.append(dict(tx=tx, address=address,
            memory=f"{prefix}/gen_banks[{offset // 8 % 16}]/i_data_mem/i_tc_sram/sram[{offset // 128}]"))
    return dict(trigger=sealed["release_observation"]["manager_hierarchy"] +
                "/i_s1b_readonly_observer/init_completed", targets=targets,
                before_64bit_hex="ffffffff00000001", after_64bit_hex="ffffffff00000000")


def reference_control_tcl(probe):
    text = ('when -label S1B_REFERENCE_OFF {' + probe["trigger"] + ' == 1} {\n'
            '  echo "S1B_REFERENCE_TRIGGER time=[s1b_time_ps]"\n')
    for target in probe["targets"]:
        tx = target["tx"]
        text += ('  set p {' + target["memory"] + '}\n'
                 '  set before [examine -radix hex $p]\n'
                 '  if {![regexp -nocase {^' + probe["before_64bit_hex"] + '$} $before]} {\n'
                 '    error "Reference recovery state is not freshly initialized"\n  }\n'
                 '  force -deposit $p 16#' + probe["after_64bit_hex"] + '\n'
                 '  set after [examine -radix hex $p]\n'
                 '  if {![regexp -nocase {^' + probe["after_64bit_hex"] + '$} $after]} {\n'
                 '    error "Reference recovery disable did not take effect"\n  }\n'
                 f'  echo "S1B_REFERENCE_OFF time=[s1b_time_ps] tx={tx} before=$before after=$after"\n')
    return text + '  nowhen S1B_REFERENCE_OFF\n}\n'


def check_reference_control(log, sealed, dispatch, done):
    trigger = re.findall(r"S1B_REFERENCE_TRIGGER time=(\d+)", log)
    rows = re.findall(r"S1B_REFERENCE_OFF time=(\d+) tx=(\d+) before=([0-9a-fA-F]+) after=([0-9a-fA-F]+)", log)
    if len(trigger) != 1 or len(rows) != 2 or [int(row[1]) for row in rows] != [0, 1]:
        raise ReferenceError("Missing or duplicated physical recovery-disable evidence")
    stamp = int(trigger[0])
    init = next(int(task) for task, role in sealed["graph"]["roles"].items() if role == "init")
    producers = [stage[key] for stage in sealed["graph"]["stages"] for key in ("primary", "shadow")]
    if init not in done or any(task not in dispatch for task in producers):
        raise ReferenceError("Reference init or dual execution is unobservable")
    if not done[init] <= stamp < min(dispatch[task] for task in producers):
        raise ReferenceError("Recovery disable is outside the post-init/pre-execution safe point")
    if any(int(time) != stamp or before.lower() != "ffffffff00000001"
           or after.lower() != "ffffffff00000000" for time, _, before, after in rows):
        raise ReferenceError("Recovery-disable memory delta differs from the two enable fields")
    return dict(status="PASS", time_ps=stamp, probe=reference_probe(sealed),
                sdc_enable=1, recovery_enable=0, changed_state_fields=["state0.enabled", "state1.enabled"])


def reference_prediction(sealed):
    """Pre-run causal prediction with unchanged model code and descriptors."""
    from s1b_model_r6 import ByteEngine, Scheduler, byte_oracle
    scene = reference_scene(sealed)
    initial = bytes(((i * 37 + 11) ^ (i >> 2)) & 255 for i in range(64))
    engine = ByteEngine(sealed["graph"], initial, True)

    def handler(descriptor, model):
        result = engine.handler(descriptor, model)
        if sealed["graph"]["roles"].get(str(descriptor["task"])) == "init":
            engine.enabled = False
            for state in engine.states:
                state["enabled"] = 0
            model.event("REFERENCE_RECOVERY_DISABLED", descriptor, transactions=[0, 1])
        return result

    model = Scheduler(sealed["descriptors"], sealed["graph"]["roles"], handler,
                      enabled=True, group=sealed["graph"]["cerf_group"],
                      post_execute=lambda: 0 if bytes(engine.mem["sink"]) == initial else 2).run()
    done = {}
    for event in model["events"]:
        if event["kind"] in ("HOST_DONE_WRITE", "DEVICE_DONE_WRITE"):
            done.setdefault(":".join(map(str, event["slot"])), []).append(event["task"])
    oracle = byte_oracle(initial, False, {})
    if model["terminal"] != "EOC" or list(engine.mem["sink"]) != oracle["sink"]:
        raise ReferenceError("Dual-execution/recovery-off reference model failed")
    return dict(scene=scene, cfg=copy.deepcopy(sealed["predictions"]["s1b_healthy_on"]["cfg"]),
                model=model, done_by_slot=done, states=engine.states, oracle=oracle,
                bytes={key: list(value) for key, value in engine.mem.items()},
                control=reference_probe(sealed))
