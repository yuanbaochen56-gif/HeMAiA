import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

W = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(W / "target/sim/automation/test"))
from bingo_sdc_recover_reference import ReferenceError
from s1b_reference_control import (reference_scene, reference_probe, reference_control_tcl,
                                   check_reference_control, reference_prediction)
from s1b_system_r1 import tcl_observer


class ReferenceControlTests(unittest.TestCase):
    def setUp(self):
        self.sealed = json.loads((W / "evidence/PredictionsS1bR4/predictions.json").read_text())
        self.probe = reference_probe(self.sealed)
        self.dispatch = {1: 200, 2: 210, 5: 500, 6: 510, 4: 300, 8: 600}
        self.done = {0: 100}
        self.log = "S1B_REFERENCE_TRIGGER time=100\n" + "".join(
            f"S1B_REFERENCE_OFF time=100 tx={tx} before=ffffffff00000001 after=ffffffff00000000\n"
            for tx in (0, 1))

    def test_dual_execution_reference_covers_every_scenario_task(self):
        prediction = reference_prediction(self.sealed)
        def tasks(prediction):
            return {e["task"] for e in prediction["model"]["events"] if e["kind"] == "DISPATCH"}
        observed = tasks(prediction)
        required = set().union(*(tasks(p) for p in self.sealed["predictions"].values()))
        self.assertTrue({2, 3, 6, 7} <= observed)
        self.assertTrue(required <= observed)
        self.assertEqual(prediction["done_by_slot"], self.sealed["predictions"]["s1b_healthy_on"]["done_by_slot"])
        self.assertEqual(prediction["cfg"]["sdc_enable"], 1)
        self.assertEqual(prediction["scene"]["recovery_enable"], 0)
        for state in prediction["states"]:
            self.assertEqual([state[k] for k in ("enabled", "detect_valid", "third_count", "commit_count", "report_count")],
                             [0, 0, 0, 1, 1])
        self.assertEqual(prediction["bytes"]["sink"], prediction["bytes"]["input"])
        self.assertNotIn("latency_ps", prediction)  # Causal model is not physical L_ref.

    def test_reference_scene_rejects_wrong_graph_or_fault_or_disabled_DMR(self):
        for key, value in (("sdc_enable", 0), ("recovery_fault_tx", 0),
                           ("recovery_negative", True), ("recovery_graph", {})):
            sealed = copy.deepcopy(self.sealed)
            sealed["scenes"]["s1b_healthy_on"][key] = value
            with self.assertRaises(ReferenceError):
                reference_scene(sealed)

    def test_geometry_matches_independent_address_decode(self):
        for target in self.probe["targets"]:
            address = self.sealed["addresses"]["state"] + 96 * target["tx"] + 8
            offset = address - 0x80000000
            self.assertEqual(target["address"], address)
            self.assertIn(f"gen_banks[{(offset >> 3) & 15}]", target["memory"])
            self.assertTrue(target["memory"].endswith(f"sram[{offset >> 7}]"))

    def test_physical_probe_evidence_is_required_and_ordered(self):
        result = check_reference_control(self.log, self.sealed, self.dispatch, self.done)
        self.assertEqual(result["changed_state_fields"], ["state0.enabled", "state1.enabled"])
        for log in ("", self.log + self.log, self.log.replace("tx=1", "tx=0"),
                    self.log.replace("ffffffff00000001", "0000000000000001"),
                    self.log.replace("ffffffff00000000", "0000000000000000"),
                    self.log.replace("time=100", "time=99"),
                    self.log.replace("time=100", "time=200")):
            with self.assertRaises(ReferenceError):
                check_reference_control(log, self.sealed, self.dispatch, self.done)
        with self.assertRaises(ReferenceError):
            check_reference_control(self.log, self.sealed, {1: 200}, self.done)

    def run_tcl(self, body):
        stubs = r'''
set callbacks [dict create]
set memory [dict create]
set deposits 0
proc when {args} {global callbacks; dict set callbacks [lindex $args 1] [lindex $args 3]}
proc nowhen {label} {global callbacks; dict unset callbacks $label}
proc echo {text} {puts $text}
proc examine {args} {global memory; set p [lindex $args end];
    if {![dict exists $memory $p]} {dict set memory $p ffffffff00000001}; return [dict get $memory $p]}
proc force {args} {global memory deposits; incr deposits;
    dict set memory [lindex $args 1] [string range [lindex $args 2] 3 end]}
'''
        script = stubs + tcl_observer(self.sealed, False) + reference_control_tcl(self.probe) + body
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(["tclsh"], input=script, cwd=directory, text=True,
                                    capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def test_tcl_clears_only_two_enable_words_and_keeps_ps(self):
        output = self.run_tcl(r'''
set ::now 13381710000
eval [dict get $callbacks S1B_REFERENCE_OFF]
if {$deposits != 2 || [dict exists $callbacks S1B_REFERENCE_OFF]} {error "bad one-shot control"}
close $S1B_CLOCK_FILE
''')
        self.assertIn("S1B_REFERENCE_TRIGGER time=13381710000", output)
        self.assertEqual(output.count("before=ffffffff00000001 after=ffffffff00000000"), 2)

    def test_tcl_rejects_uninitialized_or_already_disabled_state(self):
        for word in ("xxxxxxxx00000001", "ffffffff00000000", "0000000000000001"):
            body = 'set ::now 100\ndict set memory {' + self.probe["targets"][0]["memory"] + '} ' + word + r'''
if {![catch {eval [dict get $callbacks S1B_REFERENCE_OFF]} error_text]} {error "bad state accepted"}
if {$deposits != 0} {error "deposit before validation"}
puts "STATE_REJECTED $error_text"
close $S1B_CLOCK_FILE
'''
            self.assertIn("STATE_REJECTED", self.run_tcl(body))

    def test_actual_C_resolver_with_dual_output_and_recovery_off(self):
        source = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define BINGO_SDC_RECOVER
#define BINGO_SDC
#define BINGO_SDC_QUIET
#define BINGO_TEST_CFG 1
#define BINGO_RET_SUCC 0
#define BINGO_RET_FAIL 2
#define printf_safe printf
#include "host_sdc_recover.h"
static uint8_t src[64], p[64], shadow[64], third[64], canonical[64];
static uint32_t status[2];
static bingo_sdc_recovery_state_t states[2];
#define ADDR(p) ((uint64_t)(uintptr_t)(p))
int main(void) {
  __host_bingo_kernel_sdc_recover_init_args_t init={ADDR(status),ADDR(status+1),ADDR(states),2,1,0};
  assert(__host_bingo_kernel_sdc_recover_init(&init)==0);
  for (unsigned i=0;i<64;++i) src[i]=(uint8_t)(i*37+11);
  memcpy(p,src,64); memcpy(shadow,src,64); memset(third,0xa5,64);
  for (unsigned tx=0;tx<2;++tx) {
    uint64_t before; memcpy(&before,&states[tx].enabled,8);
    assert(before==UINT64_C(0xffffffff00000001));
    states[tx].enabled=0;
    uint64_t after; memcpy(&after,&states[tx].enabled,8);
    assert(after==UINT64_C(0xffffffff00000000));
    status[tx]=0; /* real quiet compare of the two completed equal outputs */
    __host_bingo_kernel_sdc_resolve_copy_args_t args={ADDR(src),ADDR(p),ADDR(shadow),ADDR(third),
        ADDR(canonical),ADDR(status+tx),ADDR(states+tx),64,tx,0};
    assert(__host_bingo_kernel_sdc_resolve_copy(&args)==0);
    assert(states[tx].enabled==0 && states[tx].third_count==0 && states[tx].detect_valid==0);
    assert(states[tx].detect_code==UINT32_MAX && states[tx].outcome==1);
    assert(states[tx].commit_count==1 && states[tx].report_count==1 && states[tx].phase==2);
    assert(status[tx]==0 && !memcmp(canonical,src,64));
    for(unsigned i=0;i<64;++i) assert(third[i]==0xa5);
  }
  puts("DUAL_EXECUTION_RECOVERY_OFF_C_PASS");
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "reference.c"); path.write_text(source)
            exe = Path(directory, "reference")
            build = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", "-O2",
                "-I", str(W / "target/sw/host/runtime"),
                "-I", str(W / "target/sw/host/runtime/libbingo/include"), str(path), "-o", str(exe)],
                capture_output=True, text=True, timeout=30)
            self.assertEqual(build.returncode, 0, build.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DUAL_EXECUTION_RECOVERY_OFF_C_PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
