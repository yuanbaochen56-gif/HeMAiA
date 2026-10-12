"""Profile switching, generated-artifact identity, full-word initialization and binding."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from s1b_padded_rebind_r4 import PROFILE, fingerprint, validate_profile
from s1b_system_r5 import require_clean_product_log, scene_plan

W = Path(__file__).resolve().parents[1]
APP = W / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/dma_chain_sdc_recover_2cluster"
PLATFORM = W / "evidence/BuildS1bR3/images/dma_chain_sdc_recover_2cluster_recover/HeMAiA/target/sw/shared/platform/generated/occamy.h"
sys.path.insert(0, str(APP))
spec = importlib.util.spec_from_file_location("padded_workload", APP / "main_bingo.py")
workload = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workload)


class PaddedDumpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0",
                   MPLBACKEND="Agg", MPLCONFIGDIR=str(cls.root / "mpl"))
        for name, flags in (("default", []), ("padded", ["--padded-dumps"])):
            result = subprocess.run([sys.executable, "-B", str(APP / "main_bingo.py"),
                "--output_dir", str(cls.root / name), "-c", str(APP / "params.hjson"),
                "--platformcfg", str(PLATFORM), *flags], env=env, capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_default_generation_byte_identical_to_current_image(self):
        # Use the staged products of the exact current S1b image.
        baseline = W / "evidence/BuildS1bR3/images/dma_chain_sdc_recover_2cluster_recover/staged/generated"
        names = {p.name for p in baseline.iterdir() if p.is_file()}
        self.assertTrue(names <= {p.name for p in (self.root / "default").iterdir() if p.is_file()})
        for name in names:
            self.assertEqual((baseline / name).read_bytes(), (self.root / "default" / name).read_bytes(), name)
        # Staging intentionally omits the two PNGs. Check the complete generator
        # output against the prior current-default generation as well.
        baseline = W / "evidence/GateCS1bR1/r2/product_generation/generated"
        names = {p.name for p in baseline.iterdir() if p.is_file()}
        self.assertEqual(names, {p.name for p in (self.root / "default").iterdir() if p.is_file()})
        for name in names:
            self.assertEqual((baseline / name).read_bytes(), (self.root / "default" / name).read_bytes(), name)

    def test_padded_generation_changes_only_all_ten_buffer_declarations(self):
        names = {p.name for p in (self.root / "default").iterdir() if p.is_file()}
        self.assertEqual(names, {p.name for p in (self.root / "padded").iterdir() if p.is_file()})
        for name in names:
            expected = (self.root / "default" / name).read_bytes()
            if name == "offload_bingo_hw.h":
                self.assertEqual(expected.count(b"char hex[129] = {0};"), 10)
                expected = expected.replace(b"char hex[129] = {0};", b"char hex[136] __attribute__((aligned(8))) = {0};")
            self.assertEqual((self.root / "padded" / name).read_bytes(), expected, name)

    def test_actual_generated_dump_code_zeroes_every_complete_word(self):
        class Handle:
            def get_c_var_name(self):
                return "(uintptr_t)s1b_input"
        code = workload.golden_checks({name: Handle() for name in ("P0", "S0", "T0", "C0", "P1", "S1", "T1", "C1", "sink")}, True)
        code = "\n".join(line for line in code if not line.startswith("asm volatile"))
        source = r'''
#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static uint8_t s1b_input[64], s1b_golden[64];
static uintptr_t ptr_s1b_sink;
static unsigned calls;
__attribute__((noinline)) static void printf_safe(const char *format, ...) {
  if (!strstr(format, "SDC_BUFFER")) return;
  va_list ap; va_start(ap,format); const char *hex=va_arg(ap,const char *); va_end(ap);
  assert((uintptr_t)hex % 8 == 0 && strlen(hex) == 128);
  uint64_t words[17]; memcpy(words,hex,sizeof(words));
  const char *digits="0123456789abcdef";
  for(unsigned i=0;i<64;++i) {
    assert(hex[2*i]==digits[s1b_input[i]>>4]);
    assert(hex[2*i+1]==digits[s1b_input[i]&15]);
  }
  assert(words[16] == 0); /* NUL plus all seven padded bytes */
  ++calls;
}
__attribute__((noinline)) static int checks(void) {
''' + code + r'''
  return 0;
}
int main(void) {
  volatile unsigned char poison[2048];
  for(unsigned i=0;i<sizeof(poison);++i) poison[i]=0xa5;
  assert(poison[0]==0xa5); ptr_s1b_sink=(uintptr_t)s1b_input;
  for(unsigned round=0;round<2;++round) {
    for(unsigned i=0;i<64;++i) s1b_golden[i]=s1b_input[i]=(uint8_t)(i*37+11+round);
    assert(checks()==0);
  }
  assert(calls==20); puts("20 dump objects: all 17 full words initialized, NUL word zero");
}
'''
        path = self.root / "words.c"
        path.write_text(source)
        for opt in ("-O0", "-O2"):
            exe = self.root / ("words" + opt)
            result = subprocess.run(["cc", "-std=gnu11", "-Wall", "-Wextra", "-Werror", opt, str(path), "-o", str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(exe)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("all 17 full words initialized", result.stdout)

    def test_make_switches_profiles_in_both_directions_and_rejects_invalid_values(self):
        root = self.root / "make_switch"
        root.mkdir()
        (root / "Makefile").write_text((APP / "Makefile").read_text().replace("include ../../../../common.mk", ""))
        for name in ("params.hjson", "sdc_recover_datagen.py", "platform.h"):
            (root / name).write_text("{}\n")
        (root / "main_bingo.py").write_text('''import pathlib,sys
r=pathlib.Path(__file__).parent
value="padded" if "--padded-dumps" in sys.argv else "default"
for name in ("sdc_recover_data.h","offload_bingo_hw.h"): (r/name).write_text(value)
with (r/"calls").open("a") as f: f.write(value+"\\n")
''')
        args = ["make", "gen-offload", "SNITCH_ROOT=/unused", "PLATFORM_H=" + str(root / "platform.h")]
        for option, value, count in ((None, "default", 1), ("1", "padded", 2), ("1", "padded", 2), ("0", "default", 3), (None, "default", 3)):
            result = subprocess.run(args + ([] if option is None else ["S1B_PADDED_DUMPS=" + option]), cwd=root, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual((root / "offload_bingo_hw.h").read_text(), value)
            self.assertEqual(len((root / "calls").read_text().splitlines()), count)
        for option in ("2", "true", "0 1", ""):
            result = subprocess.run(args + ["S1B_PADDED_DUMPS=" + option], cwd=root, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be 0 or 1", result.stderr)

    def test_reference_order_and_warning_gate_remain_strict(self):
        sealed = json.loads((W / "evidence/PredictionsS1bR5/predictions.json").read_text())
        plan = scene_plan(sealed)
        self.assertEqual(plan[0][2]["sdc_enable"], 1)
        self.assertEqual(plan[0][2]["recovery_enable"], 0)
        self.assertTrue(plan[0][3])
        self.assertEqual([r[0] for r in plan[1:]], list(sealed["scenes"]))
        with self.assertRaisesRegex(ValueError, "Product WT.*STOP_USER"):
            require_clean_product_log("** Warning: [l1 dcache] reading invalid data on port 0")

    def test_reference_envelope_rejects_default_profile_and_tampering(self):
        path = self.root / "binding.txt"
        path.write_bytes(b"padded-fixture")
        output = self.root / "image"
        (output / "generated").mkdir(parents=True)
        image = output / "test_cfg_unpatched.bin"
        image.write_bytes(b"padded-image-fixture")
        checksum = hashlib.sha256(image.read_bytes()).hexdigest()
        header = output / "generated/offload_bingo_hw.h"
        header.write_text("char hex[136] __attribute__((aligned(8))) = {0};\n" * 10)
        sealed = dict(padded_profile=PROFILE, source_hashes={}, default_image_id="default",
            image=dict(padded_dumps=True, image_id=checksum, unpatched_sha256=checksum, part_hashes={}, output=str(output)),
            bindings={str(path): hashlib.sha256(path.read_bytes()).hexdigest(),
                      str(image): checksum, str(header): hashlib.sha256(header.read_bytes()).hexdigest()})
        sealed["profile_fingerprint_sha256"] = fingerprint(sealed)
        validate_profile(sealed)
        for key, value in (("padded_profile", dict(PROFILE, default=True)),
                           ("image", dict(sealed["image"], padded_dumps=False)),
                           ("image", dict(sealed["image"], image_id="default")),
                           ("profile_fingerprint_sha256", "0" * 64)):
            bad = copy.deepcopy(sealed); bad[key] = value
            with self.assertRaises(ValueError):
                validate_profile(bad)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "binding changed"):
            validate_profile(sealed)
        path.write_bytes(b"padded-fixture")
        image.write_bytes(b"default-image-fixture")
        with self.assertRaisesRegex(ValueError, "image bytes"):
            validate_profile(sealed)
        image.write_bytes(b"padded-image-fixture")
        header.write_text("char hex[129] = {0};\n" * 10)
        with self.assertRaisesRegex(ValueError, "all ten complete"):
            validate_profile(sealed)


if __name__ == "__main__":
    unittest.main()
