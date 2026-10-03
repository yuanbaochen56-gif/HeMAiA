"""Shared C layout, data-only conversion, ELF geometry and bank patching."""
import importlib.util
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "watchdog_test_cfg", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
ROOT = Path(__file__).resolve().parents[4]


def write_banks(path, data):
    path.mkdir(parents=True, exist_ok=True)
    data += b"\0" * (-len(data) % 128)
    for bank in range(16):
        words = [int.from_bytes(data[row + bank * 8:row + bank * 8 + 8], "little")
                 for row in range(0, len(data), 128)]
        (path / f"bank_{bank}.hex").write_text("".join(f"{word:08X}\n" for word in words))


def write_elf(path, base=0x90000000, address=None, section_type=1):
    address = base + 256 if address is None else address
    data = bytearray(1024)
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    struct.pack_into("<16sHHIQQQIHHHHHH", data, 0, ident, 2, 243, 1, base,
                     64, 128, 0, 64, 56, 1, 64, 4, 0)
    struct.pack_into("<IIQQQQQQ", data, 64, 1, 6, 512, base, base, 512, 512, 128)
    struct.pack_into("<IIQQQQIIQQ", data, 128 + 64, 0, section_type, 3,
                     address, 768, 128, 0, 0, 128, 0)
    struct.pack_into("<IIQQQQIIQQ", data, 128 + 128, 0, 3, 0, 0, 400, 16, 0, 0, 1, 0)
    struct.pack_into("<IIQQQQIIQQ", data, 128 + 192, 0, 2, 0, 0, 432, 24, 2, 0, 8, 24)
    data[400:416] = b"\0bingo_test_cfg\0\0"
    struct.pack_into("<IBBHQQ", data, 432, 1, 0x11, 0, 1, address, 128)
    path.write_bytes(data)


class TestConfigurationTests(unittest.TestCase):
    def test_native_layout_and_initializer_match(self):
        fields = watchdog.TEST_CFG_FIELDS
        source = '#include <stddef.h>\n#include <stdio.h>\n#include "bingo_test_cfg.h"\n'
        source += "int main(void) { bingo_test_cfg_t cfg = BINGO_TEST_CFG_INITIALIZER;\n"
        source += 'printf("%zu %zu\\n", sizeof(cfg), _Alignof(bingo_test_cfg_t));\n'
        for field in fields:
            source += f'printf("{field} %zu %u\\n", offsetof(bingo_test_cfg_t, {field}), cfg.{field});\n'
        source += 'printf("user %zu\\n", offsetof(bingo_test_cfg_t, user));\n'
        source += 'printf("reserved %zu\\n", offsetof(bingo_test_cfg_t, reserved));\n'
        source += 'printf("wd_type_h %zu\\n", offsetof(bingo_test_cfg_t, wd_type_h));\n'
        source += 'printf("wd_type_c %zu\\n", offsetof(bingo_test_cfg_t, wd_type_c));\n'
        source += 'for (int i=0; i<16; ++i) if (cfg.wd_type_h[i] || cfg.wd_type_c[i]) return 1;\n'
        source += 'for (int i=0; i<4; ++i) if (cfg.user[i]) return 1;\n'
        source += 'for (int i=0; i<5; ++i) if (cfg.reserved[i]) return 1;\nreturn 0; }\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "layout.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            "-I", str(ROOT / "target/sw/shared/runtime"),
                            str(path / "layout.c"), "-o", str(path / "layout")], check=True)
            output = subprocess.check_output([str(path / "layout")], text=True).splitlines()
        self.assertEqual(output[0], "256 128")
        defaults = watchdog.test_cfg_defaults()
        for index, line in enumerate(output[1:1 + len(fields)]):
            name, offset, value = line.split()
            self.assertEqual((name, int(offset), int(value)),
                             (fields[index], index * 4, defaults[name]))
        self.assertEqual(output[-4:], ["user 92", "reserved 108", "wd_type_h 128", "wd_type_c 192"])

    def test_user_defaults_preserve_v1_and_v2_bytes(self):
        for version in (1, 2):
            cfg = watchdog.test_cfg_defaults(version)
            self.assertEqual(cfg["user"], [0] * 4)
            words = [cfg[field] for field in watchdog.TEST_CFG_FIELDS] + [0] * 9
            if version == 2:
                words += [0] * 32
            self.assertEqual(watchdog.test_cfg_bytes(cfg), struct.pack("<" + "I" * len(words), *words))
            self.assertEqual(watchdog.test_cfg_bytes(cfg),
                             watchdog.test_cfg_bytes({k: v for k, v in cfg.items() if k != "user"}))

    def test_user_converter_and_validation(self):
        values = [1, 7, 0xFFFFFFFF, 0]
        sc = dict(watchdog.SCENARIOS["tch0"], t1_user=values)
        cfg = watchdog.scenario_test_cfg(sc)
        self.assertEqual(cfg["user"], values)
        self.assertIsNot(cfg["user"], values)
        self.assertEqual(struct.unpack_from("<4I", watchdog.test_cfg_bytes(cfg), 92), tuple(values))
        for bad in ([1], [0] * 5, [-1, 0, 0, 0], [0x100000000, 0, 0, 0], ["1", 0, 0, 0]):
            with self.assertRaisesRegex(ValueError, "user must have four uint32 words"):
                watchdog.scenario_test_cfg(dict(sc, t1_user=bad))

    def test_user_patch_is_confined_to_first_configuration_row(self):
        for version in (1, 2):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory)
                cfg = watchdog.test_cfg_defaults(version)
                data = bytes(range(128)) + watchdog.test_cfg_bytes(cfg) + bytes(range(128))
                write_banks(path, data)
                before = {p.name: p.read_bytes() for p in path.iterdir()}
                location = dict(offset=128, size=128 * version)
                changed = dict(cfg, user=[1, 2, 3, 4])
                record = watchdog.patch_test_cfg(path, location, changed)
                self.assertEqual(record["changed_rows"], [1])
                self.assertEqual(watchdog.read_bank_image(path),
                                 data[:128] + watchdog.test_cfg_bytes(changed) + data[128 + 128 * version:])
                watchdog.patch_test_cfg(path, location, cfg)
                self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})

    def test_native_user_accessor_reads_volatile_instance(self):
        source = ('#include "bingo_test_cfg.h"\n'
                  'volatile bingo_test_cfg_t bingo_test_cfg = BINGO_TEST_CFG_INITIALIZER;\n'
                  'int main(void) { for (unsigned i=0; i<4; ++i) {\n'
                  'bingo_test_cfg.user[i] = 17+i;\n'
                  'if (bingo_test_cfg_user(i) != 17+i) return 1;\n'
                  'bingo_test_cfg.user[i] = 31+i;\n'
                  'if (bingo_test_cfg_user(i) != 31+i) return 1;\n'
                  '} return 0; }\n')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "user.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                            "-DBINGO_TEST_CFG=1", "-I", str(ROOT / "target/sw/shared/runtime"),
                            str(path / "user.c"), "-o", str(path / "user")], check=True)
            subprocess.run([str(path / "user")], check=True)

    def test_macro_defaults_match_initializer(self):
        paths = (ROOT / "target/sw/host/runtime/libbingo/include/libbingo/bingo_api.h",
                 ROOT / "target/sw/device/runtime/src/bingo.h")
        macros = dict(re.findall(r"^#define\s+(BINGO_\w+)\s+(-?\d+)\s*$",
                                 "\n".join(path.read_text() for path in paths), re.M))
        defaults = watchdog.test_cfg_defaults()
        for macro, field in watchdog.TEST_CFG_MACROS.items():
            if macro in macros:
                self.assertEqual(defaults[field], int(macros[macro]) & 0xFFFFFFFF, macro)
            else:
                self.assertIn(field, ("fault_gid", "cerf_fb_cluster", "cerf_fb_core",
                                      "cerf_fb_clear", "cerf_fb_set"))
        self.assertEqual(defaults["fault_gid"], 0xFFFFFFFF)
        self.assertEqual(defaults["cerf_fb_enable"], 0)

    def test_scenario_twins_and_all_off(self):
        for number in (18, 19, 40, 41, 42, 43, 44):
            self.assertEqual(watchdog.SCENARIOS[f"t{number}"],
                             dict(watchdog.SCENARIOS[f"s{number}"], t1=True))
        self.assertEqual(watchdog.scenario_test_cfg(watchdog.SCENARIOS["tch0"]),
                         watchdog.test_cfg_defaults())

    def test_converter_whitelist_and_expect_discard(self):
        cfg = watchdog.scenario_test_cfg(watchdog.SCENARIOS["t41"])
        self.assertEqual((cfg["fault_gid"], cfg["fault_core"], cfg["fault_pre_stall_cycles"],
                          cfg["risk_policy"], cfg["risk_confirm"]), (3, 1, 100000, 2, 125000))
        cfg = watchdog.scenario_test_cfg(watchdog.SCENARIOS["t44"])
        self.assertEqual(cfg, watchdog.test_cfg_defaults())
        cfg = watchdog.scenario_test_cfg(watchdog.SCENARIOS["tch0"],
                                        "-DBINGO_CERF_FB_CLUSTER=1 -DBINGO_CERF_FB_CORE=0 "
                                        "-DBINGO_CERF_FB_CLEAR=3 -DBINGO_CERF_FB_SET=4")
        self.assertEqual(cfg["cerf_fb_enable"], 1)
        self.assertEqual(cfg["cerf_fb_clear"], 3)
        for flags in ("-DBINGO_INPLACE_UNSAFE_REPLAY=1", "-DBINGO_UNKNOWN=1", "-O2",
                      "-DBINGO_RISK_LATE=4294967296"):
            with self.assertRaises(ValueError):
                watchdog.scenario_test_cfg(watchdog.SCENARIOS["tch0"], flags)

    def test_same_ps_pair_allows_only_fault_gid_to_differ(self):
        reference = dict(watchdog.SCENARIOS["tch0"], fault_stall_cycles=0,
                         fault_gid=0xFFFFFFFF, victim=(0, 0, 0),
                         extra_flags="-DBINGO_WD_FAULT_PRE_STALL_CYCLES=17")
        paired = dict(reference, same_as="healthy_pair", fault_gid=9)
        with patch.dict(watchdog.SCENARIOS, {"healthy_pair": reference}):
            cfg = watchdog.scenario_test_cfg(paired)
            self.assertEqual(cfg["fault_gid"], 9)
            self.assertEqual(cfg["fault_pre_stall_cycles"], 17)
            for victim in ((0, 1, 0), (0, 0, 1)):
                with self.assertRaisesRegex(ValueError, "same-ps pair.*fault_(core|cluster)"):
                    watchdog.scenario_test_cfg(dict(paired, victim=victim))
            for flags, field in (
                ("-DBINGO_WD_FAULT_PRE_STALL_CYCLES=18", "fault_pre_stall_cycles"),
                ("-DBINGO_WD_FAULT_AFTER_KERNEL=1 -DBINGO_WD_FAULT_PRE_STALL_CYCLES=17",
                 "fault_after_kernel"),
            ):
                with self.assertRaisesRegex(ValueError, f"same-ps pair.*{field}"):
                    watchdog.scenario_test_cfg(dict(paired, extra_flags=flags))
            with self.assertRaisesRegex(ValueError, "same-ps pair.*fault_stall_cycles"):
                watchdog.scenario_test_cfg(dict(paired, fault_stall_cycles=1))

    def test_same_ps_guard_runs_before_build_cleanup(self):
        from types import SimpleNamespace

        reference = dict(watchdog.SCENARIOS["tch0"], fault_stall_cycles=0, victim=(0, 0, 0))
        paired = dict(reference, same_as="guard_reference", victim=(0, 1, 0))
        args = SimpleNamespace(extra_flags="", wd_timeout=None)
        with patch.dict(watchdog.SCENARIOS, {"guard_reference": reference, "guard_bad": paired}), \
                patch.object(watchdog, "make_cfg") as configure, \
                patch.object(watchdog, "clean_app_builds") as clean:
            with self.assertRaisesRegex(ValueError, "same-ps pair.*fault_core"):
                watchdog.run_scenario("guard_bad", args)
        configure.assert_not_called()
        clean.assert_not_called()

    def test_patch_default_unchanged_one_row_and_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            defaults = watchdog.test_cfg_defaults(1)
            data = bytes(range(128)) + watchdog.test_cfg_bytes(defaults) + bytes(range(128))
            write_banks(path, data)
            location = dict(offset=128, size=128, address=0x90000080, load_base=0x90000000)
            before = {p.name: p.read_bytes() for p in path.iterdir()}
            record = watchdog.patch_test_cfg(path, location, defaults)
            self.assertEqual(record["changed_bank_line_count"], 0)
            self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})
            changed = dict(defaults, fault_gid=0xFFFFFFFE, remote_proxy_timeout=777)
            record = watchdog.patch_test_cfg(path, location, changed)
            self.assertEqual(record["changed_rows"], [1])
            self.assertEqual(record["changed_bank_line_count"], 2)
            self.assertEqual(watchdog.read_bank_image(path),
                             data[:128] + watchdog.test_cfg_bytes(changed) + data[256:])
            for p in path.iterdir():
                self.assertEqual(p.read_bytes().splitlines()[0], before[p.name].splitlines()[0])
                self.assertEqual(p.read_bytes().splitlines()[2], before[p.name].splitlines()[2])
            watchdog.patch_test_cfg(path, location, defaults)
            self.assertEqual(before, {p.name: p.read_bytes() for p in path.iterdir()})

    def test_bad_magic_version_bounds_and_v2_two_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            defaults = watchdog.test_cfg_defaults(1)
            location = dict(offset=0, size=128)
            for bad in (dict(defaults, magic=0), dict(defaults, version=2)):
                write_banks(path, watchdog.test_cfg_bytes(bad))
                with self.assertRaises(ValueError):
                    watchdog.patch_test_cfg(path, location, defaults)
            write_banks(path, watchdog.test_cfg_bytes(defaults))
            with self.assertRaises(ValueError):
                watchdog.patch_test_cfg(path, dict(offset=128, size=128), defaults)
            v2 = watchdog.test_cfg_defaults()
            write_banks(path, watchdog.test_cfg_bytes(v2))
            changed = dict(v2, fault_gid=7, wd_type_h=[0, 8] + [0] * 14,
                           wd_type_c=[0, 30] + [0] * 14)
            record = watchdog.patch_test_cfg(path, dict(offset=0, size=256), changed)
            self.assertEqual(record["changed_rows"], [0, 1])

    def test_actual_load_base_and_initialized_symbol(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "host.elf"
            write_elf(path)
            location = watchdog.test_cfg_elf_location(path)
            self.assertEqual(location, dict(address=0x90000100, load_base=0x90000000,
                                            offset=256, size=128))
            write_elf(path, section_type=8)
            with self.assertRaises(ValueError):
                watchdog.test_cfg_elf_location(path)
            write_elf(path, address=0x90000104)
            with self.assertRaises(ValueError):
                watchdog.test_cfg_elf_location(path)

    def test_family_rejects_id_location_and_outside_rows(self):
        record = dict(image_id="x", location=dict(offset=128, size=128), changed_rows=[1])
        watchdog.check_t1_family([record, dict(record)])
        for other in (dict(record, image_id="y"),
                      dict(record, location=dict(offset=256, size=128)),
                      dict(record, changed_rows=[2])):
            with self.assertRaises(ValueError):
                watchdog.check_t1_family([record, other])

    def test_seed_forwarding_for_both_container_backends(self):
        import hemaia_sim_runner as runner
        for backend, option in (("podman", "-e"), ("apptainer", "--env")):
            with patch.dict(os.environ, {"PYTHONHASHSEED": "0"}), \
                    patch.object(runner.shutil, "which", side_effect=lambda name: name if name == backend else None), \
                    patch.object(runner.subprocess, "run") as execute:
                runner.run_in_container(ROOT, "image", ROOT, ["python3", "main_bingo.py"])
            command = execute.call_args.args[0]
            self.assertIn(option, command)
            self.assertIn("PYTHONHASHSEED=0", command)
        environment = {name: value for name, value in os.environ.items() if name != "PYTHONHASHSEED"}
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(runner.shutil, "which", side_effect=lambda name: name if name == "podman" else None), \
                patch.object(runner.subprocess, "run") as execute:
            runner.run_in_container(ROOT, "image", ROOT, ["true"])
        self.assertNotIn("-e", execute.call_args.args[0])

    def test_all_off_checker_requires_both_goldens_without_dispatch_trace(self):
        sc = dict(watchdog.SCENARIOS["tch0"], dispatch_log=False)
        uart = ("[Host] Bingo status: fenced=0x0 risk=0x0\n"
                "[Host] Check [A_chain_cluster0]: PASS\n[Host] Check [A_cluster1]: PASS\n")
        self.assertEqual(watchdog.evaluate_risk_chain(sc, "", uart), [])
        self.assertTrue(watchdog.evaluate_risk_chain(sc, "", uart.replace("risk=0x0", "risk=0x2")))
        self.assertTrue(watchdog.evaluate_risk_chain(sc, "", uart.replace(
            "[Host] Check [A_cluster1]: PASS\n", "")))
        self.assertEqual(watchdog.evaluate("tch0", sc,
                                          "All chips finished successfully at 100\n", uart), [])

    def test_t1_early_exit_builds_once_and_patches_actual_gid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            task_dir = path / "task"
            banks = task_dir / "bin/app_chip_0_0"
            write_banks(banks, watchdog.test_cfg_bytes(watchdog.test_cfg_defaults()))
            elf = path / "target/sim/apps/app.elf"
            elf.parent.mkdir(parents=True)
            elf.write_bytes(b"fixture")
            runner = watchdog.TestCfgSimRunner(
                test_cfg=watchdog.scenario_test_cfg(watchdog.SCENARIOS["t43"]),
                early_exit=True, inject_fault=True, repo_root=path,
                output_dir=path, skip_setup=True, engine="vsim", with_waveform=False,
                cfg="target/rtl/cfg/hemaia_ci.hjson", sim_cfg="target/sim/cfg/sim_rtl.hjson",
                with_macro=False, with_d2d=False, with_pll=False)
            with patch.object(watchdog.NoTraceSimRunner, "build_apps_and_stage",
                              return_value=[(task_dir, "app")]) as build, \
                    patch.object(watchdog, "early_exit_task_ids", return_value={"gemm2": 108}), \
                    patch.object(watchdog.shutil, "copyfile"), \
                    patch.object(watchdog, "test_cfg_elf_location",
                                 return_value=dict(offset=0, size=256)):
                runner.build_apps_and_stage([{}])
            build.assert_called_once()
            self.assertEqual(runner.test_cfg_record["configuration"]["fault_gid"], 108)
            self.assertEqual(json.loads((path / "test_cfg.json").read_text())["PYTHONHASHSEED"], "0")


if __name__ == "__main__":
    unittest.main()
