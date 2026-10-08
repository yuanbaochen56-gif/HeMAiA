"""A7's flag-only third T1 row, MMIO setter and CSR wiring."""
import importlib.util
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "watchdog_wd_adapt", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
ROOT = Path(__file__).resolve().parents[4]


class AdaptiveConfigurationTests(unittest.TestCase):
    def test_v3_c_initializer_is_identical_to_python_layout(self):
        source = ('#include <stddef.h>\n#include <stdio.h>\n#include "bingo_test_cfg.h"\n'
            'int main(void) { bingo_test_cfg_t cfg = BINGO_TEST_CFG_INITIALIZER;\n'
            '_Static_assert(offsetof(bingo_test_cfg_t, wd_adapt)==256, "third row");\n'
            '_Static_assert(_Alignof(bingo_test_cfg_t)==128, "bank row alignment");\n'
            'return fwrite(&cfg, 1, sizeof(cfg), stdout)!=sizeof(cfg); }\n')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "layout.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-DBINGO_WD_ADAPT", "-I", str(ROOT / "target/sw/shared/runtime"),
                str(root / "layout.c"), "-o", str(root / "layout")], check=True)
            actual = subprocess.check_output([str(root / "layout")])
        self.assertEqual(actual, watchdog.test_cfg_bytes(watchdog.test_cfg_defaults(3)))
        self.assertEqual(len(actual), 384)

    def test_version_selection_requires_image_flag(self):
        scene = dict(watchdog.SCENARIOS["tch0"], image_flags="-DBINGO_WD_ADAPT", wd_adapt=0x7D0147)
        self.assertEqual(watchdog.t1_image_flags(scene), "-DBINGO_WD_ADAPT")
        cfg = watchdog.scenario_test_cfg(scene)
        self.assertEqual(cfg["version"], 3)
        self.assertEqual(struct.unpack_from("<I", watchdog.test_cfg_bytes(cfg), 256)[0], scene["wd_adapt"])
        with self.assertRaises(ValueError):
            watchdog.scenario_test_cfg(dict(scene, image_flags=""))
        for version in (1, 2):
            with self.assertRaises(ValueError):
                watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(version), wd_adapt=0))
        for bad in (-1, 1 << 32, True, "1"):
            with self.assertRaises(ValueError):
                watchdog.test_cfg_bytes(dict(cfg, wd_adapt=bad))

    def test_v3_patch_changes_only_third_row_and_round_trips(self):
        from test_bingo_test_cfg import write_banks
        cfg = watchdog.test_cfg_defaults(3)
        data = bytes(range(128)) + watchdog.test_cfg_bytes(cfg) + bytes(range(128))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_banks(root, data)
            changed = dict(cfg, wd_adapt=0x7D0147)
            record = watchdog.patch_test_cfg(root, dict(offset=128, size=384), changed)
            self.assertEqual(record["changed_rows"], [3])
            self.assertEqual(watchdog.read_bank_image(root),
                data[:128] + watchdog.test_cfg_bytes(changed) + data[512:])
            watchdog.patch_test_cfg(root, dict(offset=128, size=384), cfg)
            self.assertEqual(watchdog.read_bank_image(root), data)

    def test_setter_preserves_all_ctrl_bits_and_init_is_flag_only(self):
        api = (ROOT / "target/sw/host/runtime/libbingo/src/bingo_api.c").read_text()
        body = re.search(r"void bingo_wd_adapt_set\([^)]*\) \{[\s\S]*?\n\}", api).group(0)
        body = body.replace('asm volatile("fence" ::: "memory");', "")
        source = ('#include <stdint.h>\n#include <assert.h>\n'
            'static uint32_t value; static uintptr_t addr;\n'
            'static uint64_t chiplet_addr_transform(uint64_t a) { return a+0x10000000; }\n'
            'static uintptr_t quad_ctrl_bingo_wd_adapt_addr(void) { return 0x204; }\n'
            'static void writew(uint32_t v, uintptr_t a) { value=v; addr=a; }\n')
        source += body + '\nint main(void) { bingo_wd_adapt_set(UINT32_MAX); assert(value==UINT32_MAX && addr==0x10000204); return 0; }\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "api.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                str(root / "api.c"), "-o", str(root / "api")], check=True)
            subprocess.run([str(root / "api")], check=True)
        self.assertIn("#ifdef BINGO_WD_ADAPT\n    bingo_wd_adapt_set(bingo_test_cfg.wd_adapt);\n#endif", api)
        self.assertLess(api.index("bingo_wd_type_set(type,"), api.index("bingo_wd_adapt_set(bingo_test_cfg"))

    def test_csr_appended_and_wired_to_top(self):
        reg = (ROOT / "hw/occamy/quad_periph/occamy_quad_periph_reg.hjson.tpl").read_text()
        self.assertLess(reg.index('name: "BINGO_HOST_READY_PENDING"'), reg.index('name: "BINGO_WD_ADAPT"'))
        self.assertIn('bits: "31:12", name: "FLOOR", resval: "0"', reg)
        template = (ROOT / "hw/occamy/occamy_quad_ctrl.sv.tpl").read_text()
        self.assertIn(".wd_adapt_i", template)
        self.assertIn(".bingo_hw_manager_wd_adapt_o", template)
        self.assertIn("quad_ctrl_bingo_wd_adapt_addr", (
            ROOT / "target/sw/shared/platform/template/occamy_memory_map.h.tpl").read_text())


if __name__ == "__main__":
    unittest.main()
