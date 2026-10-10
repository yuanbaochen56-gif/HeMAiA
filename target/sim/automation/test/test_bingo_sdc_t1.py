"""S1a fixed v3 reserved-word layout, without modifying legacy T1 tests."""
import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from test_bingo_test_cfg import watchdog

ROOT = Path(__file__).resolve().parents[4]
FIELDS = ("sdc_enable", "fault_sdc_gid", "fault_sdc_cluster", "fault_sdc_core",
          "fault_sdc_offset", "fault_sdc_mask")


class SdcT1Tests(unittest.TestCase):
    def scene(self, **changes):
        return dict(watchdog.SCENARIOS["tch0"], t1=True, image_flags="-DBINGO_SDC", **changes)

    def test_flag_implies_v3_and_default_fault_is_disabled(self):
        cfg = watchdog.scenario_test_cfg(self.scene())
        self.assertEqual(watchdog.t1_image_flags(self.scene()), "-DBINGO_SDC -DBINGO_WD_ADAPT")
        self.assertEqual(cfg["version"], 3)
        self.assertEqual(cfg["sdc_enable"], 0)
        self.assertEqual([cfg[f] for f in FIELDS[1:4]], [0xFFFFFFFF] * 3)
        self.assertEqual(len(watchdog.test_cfg_bytes(cfg)), 384)

    def test_stager_defaults_equal_the_actual_compiled_initializer(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "defaults.c"
            source.write_text('#include <stdio.h>\n#include "bingo_test_cfg.h"\n'
                'int main(void) { bingo_test_cfg_t c=BINGO_TEST_CFG_INITIALIZER; '
                'return fwrite(&c, 1, sizeof(c), stdout)!=sizeof(c); }\n')
            exe = Path(directory) / "defaults"
            subprocess.run(["cc", "-std=c11", "-DBINGO_TEST_CFG=1", "-DBINGO_SDC",
                "-DBINGO_WD_ADAPT", "-I", str(ROOT / "target/sw/shared/runtime"),
                str(source), "-o", str(exe)], check=True, capture_output=True)
            defaults = watchdog.test_cfg_image_defaults(3, "-DBINGO_SDC -DBINGO_WD_ADAPT")
            self.assertEqual(subprocess.check_output([str(exe)]), watchdog.test_cfg_bytes(defaults))
        for version, flags in ((1, ""), (2, ""), (3, "-DBINGO_WD_ADAPT"),
                               (3, "-DBINGO_WD_ADAPT -DBINGO_WD_LATE")):
            self.assertEqual(watchdog.test_cfg_image_defaults(version, flags),
                             watchdog.test_cfg_defaults(version))

    def test_native_c_layout_and_initializer_for_all_flag_combinations(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "layout.c"
            source.write_text(
                '#include <stddef.h>\n#include "bingo_test_cfg.h"\n'
                '_Static_assert(sizeof(bingo_test_cfg_t)==384, "size");\n' +
                "".join(f'_Static_assert(offsetof(bingo_test_cfg_t,{field})=={268 + 4 * i}, "{field}");\n'
                        for i, field in enumerate(FIELDS)) +
                'int main(void) { bingo_test_cfg_t c=BINGO_TEST_CFG_INITIALIZER; '
                'return c.version!=3 || c.sdc_enable!=0 || c.fault_sdc_gid!=UINT32_MAX '
                '|| c.fault_sdc_cluster!=UINT32_MAX || c.fault_sdc_core!=UINT32_MAX; }\n')
            for flags in (["-DBINGO_SDC"], ["-DBINGO_SDC", "-DBINGO_WD_ADAPT"],
                          ["-DBINGO_SDC", "-DBINGO_WD_LATE"]):
                with self.subTest(flags=flags):
                    exe = Path(directory) / "layout"
                    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
                                    "-I", str(ROOT / "target/sw/shared/runtime"),
                                    str(source), "-o", str(exe)], check=True, capture_output=True)
                    subprocess.run([str(exe)], check=True)

    def test_serialized_offsets_do_not_touch_legacy_fields(self):
        values = (1, 5, 1, 1, 17, 0x80)
        cfg = watchdog.scenario_test_cfg(self.scene(**dict(zip(FIELDS, values))))
        data = watchdog.test_cfg_bytes(cfg)
        self.assertEqual(struct.unpack_from("<6I", data, 268), values)
        self.assertEqual(data[292:], bytes(92))
        legacy = watchdog.test_cfg_defaults(3)
        before = watchdog.test_cfg_bytes(legacy)
        after = watchdog.test_cfg_bytes(dict(legacy, **dict(zip(FIELDS, values))))
        self.assertEqual(before[:268], after[:268])
        self.assertEqual(before[292:], after[292:])

    def test_fields_require_the_image_flag_and_v3(self):
        for field in FIELDS:
            with self.subTest(field=field):
                sc = dict(watchdog.SCENARIOS["tch0"], **{field: 0})
                with self.assertRaisesRegex(ValueError, "BINGO_SDC"):
                    watchdog.scenario_test_cfg(sc)
                for version in (1, 2):
                    with self.assertRaisesRegex(ValueError, "layout"):
                        watchdog.test_cfg_bytes(dict(watchdog.test_cfg_defaults(version), **{field: 0}))

    def test_validation_rejects_bad_values(self):
        for field in FIELDS:
            for value in (-1, 0x100000000, True, "1"):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, "uint32"):
                        watchdog.scenario_test_cfg(self.scene(**{field: value}))
        for field, value in (("sdc_enable", 2), ("fault_sdc_mask", 256)):
            with self.assertRaises(ValueError):
                watchdog.scenario_test_cfg(self.scene(**{field: value}))

    def test_legacy_serializer_and_scenarios_are_byte_identical(self):
        # This comparison also works outside W13: skip only the external
        # reference binding, never the self-contained offset/validation tests.
        reference = ROOT.parent.parent / "p7b_20261009/HeMAiA/target/sim/automation/test/3_start_bingo_watchdog_sim.py"
        if not reference.is_file():
            self.skipTest("W12 frozen serializer is not present")
        spec = importlib.util.spec_from_file_location("sdc_legacy_watchdog", reference)
        old = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(old)
        for version in (1, 2, 3):
            for late in (False, True):
                if late and version != 3:
                    continue
                cfg = old.test_cfg_defaults(version)
                if late:
                    cfg.update(wd_late=0x3E8003, fault_pre_stall_from=2)
                self.assertEqual(watchdog.test_cfg_bytes(cfg), old.test_cfg_bytes(cfg))
        for name, scene in old.SCENARIOS.items():
            if not scene.get("t1"):
                continue
            with self.subTest(scene=name):
                cfg = old.scenario_test_cfg(scene)
                self.assertEqual(watchdog.scenario_test_cfg(scene), cfg)
                self.assertEqual(watchdog.test_cfg_bytes(cfg), old.test_cfg_bytes(cfg))


if __name__ == "__main__":
    unittest.main()
