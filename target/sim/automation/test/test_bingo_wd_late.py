"""P9's flag-only two reserved words, CSR wiring and late-setting API."""
import importlib.util
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "watchdog_wd_late", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
ROOT = Path(__file__).resolve().parents[4]


class PredictiveConfigurationTests(unittest.TestCase):
    def test_late_flag_implies_adaptive_image_and_preserves_v3_layout(self):
        source = ('#include <stddef.h>\n#include <stdio.h>\n#include "bingo_test_cfg.h"\n'
            'int main(void) { bingo_test_cfg_t cfg = BINGO_TEST_CFG_INITIALIZER;\n'
            '_Static_assert(offsetof(bingo_test_cfg_t, wd_adapt)==256, "adapt");\n'
            '_Static_assert(offsetof(bingo_test_cfg_t, wd_late)==260, "late");\n'
            '_Static_assert(offsetof(bingo_test_cfg_t, fault_pre_stall_from)==264, "from");\n'
            '_Static_assert(sizeof(bingo_test_cfg_t)==384, "third row");\n'
            '_Static_assert(_Alignof(bingo_test_cfg_t)==128, "bank row alignment");\n'
            'return fwrite(&cfg, 1, sizeof(cfg), stdout)!=sizeof(cfg); }\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layout.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-DBINGO_WD_LATE", "-I", str(ROOT / "target/sw/shared/runtime"),
                str(root / "layout.c"), "-o", str(root / "layout")], check=True)
            actual = subprocess.check_output([str(root / "layout")])
        self.assertEqual(actual, watchdog.test_cfg_bytes(watchdog.test_cfg_defaults(3)))
        scene = dict(watchdog.SCENARIOS["tch0"], image_flags="-DBINGO_WD_LATE",
                     wd_late=0x3E8003, fault_pre_stall_from=2)
        self.assertEqual(watchdog.t1_image_flags(scene), "-DBINGO_WD_LATE -DBINGO_WD_ADAPT")
        cfg = watchdog.scenario_test_cfg(scene)
        self.assertEqual(cfg["version"], 3)
        self.assertEqual(struct.unpack_from("<II", watchdog.test_cfg_bytes(cfg), 260),
                         (0x3E8003, 2))

    def test_only_two_reserved_words_change_and_patch_round_trips(self):
        from test_bingo_test_cfg import write_banks
        cfg = watchdog.test_cfg_defaults(3)
        binary = watchdog.test_cfg_bytes(cfg)
        changed = dict(cfg, wd_late=0x3E8003, fault_pre_stall_from=2)
        value = watchdog.test_cfg_bytes(changed)
        self.assertEqual(binary[:260], value[:260])
        self.assertEqual(binary[268:], value[268:])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_banks(root, binary)
            record = watchdog.patch_test_cfg(root, dict(offset=0, size=384), changed)
            self.assertEqual(record["changed_rows"], [2])
            self.assertEqual(watchdog.read_bank_image(root), value)
            watchdog.patch_test_cfg(root, dict(offset=0, size=384), cfg)
            self.assertEqual(watchdog.read_bank_image(root), binary)

    def test_late_fields_require_flag_v3_uint32_and_zero_reserved_bits(self):
        scene = dict(watchdog.SCENARIOS["tch0"], wd_late=3)
        with self.assertRaises(ValueError):
            watchdog.scenario_test_cfg(scene)
        for version in (1, 2):
            for field in ("wd_late", "fault_pre_stall_from"):
                with self.assertRaises(ValueError):
                    watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(version), **{field: 0}))
        for field in ("wd_late", "fault_pre_stall_from"):
            for bad in (-1, 1 << 32, True, "1"):
                with self.assertRaises(ValueError):
                    watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(3), **{field: bad}))
        with self.assertRaises(ValueError):
            watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(3), wd_late=0x10))

    def test_learning_rule_fields_are_accepted_and_the_rest_stays_reserved(self):
        # [6] LOCAL_ONLY and [8:7] MOVED_SH may be set; [5:4] and [11:9] may not
        for good in (0x40, 0x80, 0x100, 0x1c0, 0x3E81c3):
            watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(3), wd_late=good))
        for bad in (0x10, 0x20, 0x200, 0x400, 0x800):
            with self.assertRaises(ValueError):
                watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(3), wd_late=bad))
        reg = (ROOT / "hw/occamy/quad_periph/occamy_quad_periph_reg.hjson.tpl").read_text()
        body = reg[reg.index('name: "BINGO_WD_LATE"'):reg.index('name: "BINGO_FENCE_FB"')]
        for field in ('bits: "6", name: "LOCAL_ONLY"', 'bits: "8:7", name: "MOVED_SH"'):
            self.assertIn(field, body)
        periph = (ROOT / "hw/occamy/quad_periph/occamy_quad_periph.sv").read_text()
        self.assertIn("reg2hw.bingo_wd_late.lfloor.q, 3'b0,\n"
                      "      reg2hw.bingo_wd_late.moved_sh.q, reg2hw.bingo_wd_late.local_only.q, 2'b0,", periph)

    def test_pre_stall_start_is_a_same_image_fault_field(self):
        first = dict(watchdog.test_cfg_defaults(3), fault_pre_stall_from=2)
        watchdog.check_same_ps_fault_cfg(first, dict(first, fault_gid=5))
        with self.assertRaises(ValueError):
            watchdog.check_same_ps_fault_cfg(first, dict(first, fault_pre_stall_from=3))

    def test_setter_masks_reserved_bits_and_init_write_is_flag_only(self):
        api = (ROOT / "target/sw/host/runtime/libbingo/src/bingo_api.c").read_text()
        body = re.search(r"void bingo_wd_late_set\([^)]*\) \{[\s\S]*?\n\}", api).group(0)
        body = body.replace('asm volatile("fence" ::: "memory");', "")
        source = ('#include <stdint.h>\n#include <assert.h>\n'
            'static uint32_t value; static uintptr_t addr;\n'
            'static uint64_t chiplet_addr_transform(uint64_t a) { return a+0x10000000; }\n'
            'static uintptr_t quad_ctrl_bingo_wd_late_addr(void) { return 0x208; }\n'
            'static void writew(uint32_t v, uintptr_t a) { value=v; addr=a; }\n')
        source += body + '\nint main(void) { bingo_wd_late_set(UINT32_MAX); assert(value==0xfffff1cfu && addr==0x10000208); return 0; }\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                str(root / "api.c"), "-o", str(root / "api")], check=True)
            subprocess.run([str(root / "api")], check=True)
        self.assertIn("#ifdef BINGO_WD_LATE\n    bingo_wd_late_set(bingo_test_cfg.wd_late);\n#endif", api)

    def test_csr_is_appended_with_the_approved_fields_and_connected(self):
        reg = (ROOT / "hw/occamy/quad_periph/occamy_quad_periph_reg.hjson.tpl").read_text()
        self.assertLess(reg.index('name: "BINGO_WD_ADAPT"'), reg.index('name: "BINGO_WD_LATE"'))
        body = reg[reg.index('name: "BINGO_WD_LATE"'):]
        for field in ('bits: "0", name: "EN"', 'bits: "3:1", name: "SH_L"',
                      'bits: "31:12", name: "LFLOOR"'):
            self.assertIn(field, body)
        template = (ROOT / "hw/occamy/occamy_quad_ctrl.sv.tpl").read_text()
        self.assertIn(".bingo_hw_manager_wd_late_i", template)
        self.assertIn(".bingo_hw_manager_wd_late_o", template)
        device = (ROOT / "target/sw/device/runtime/src/bingo.h").read_text()
        self.assertIn("#ifdef BINGO_WD_LATE\n                       && cur_global_task_id >= fault_pre_stall_from\n#endif", device)
        self.assertIn("uint32_t fault_pre_stall_from = cfg->fault_pre_stall_from;", device)
        self.assertNotIn("bingo_test_cfg.fault_pre_stall_from", device)
        self.assertLess(device.index("if (cur_global_task_id == fault_gid)"),
                        device.index("cur_global_task_id >= fault_pre_stall_from"))


if __name__ == "__main__":
    unittest.main()
