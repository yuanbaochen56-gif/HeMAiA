"""R1a and R1b: the int32-add pair, and the fence-fallback word."""
import contextlib
import io
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from test_bingo_host_fallback import driver, IDS, drop

ROOT = Path(__file__).resolve().parents[4]


def fence_cerf_fixture():
    """One DM fenced without a substitute: CERF to the host add at the fence."""
    log = "\n".join([
        "[BINGO_DISPATCH] 20 chip=0 task=0 core=1 cluster=0",
        "[BINGO_WD] 50 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
        "[BINGO_WD] 60 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1",
        "[BINGO_CERF_FB] 63 type 2 clear g0 set g1",
        "[BINGO_REPLAY_STUCK] 63 chip=0 core=1 cluster=0: no live core may run task 0 (logical core 1)",
        "[BINGO_DISPATCH] 70 chip=0 task=2 core=2 cluster=0",
        "[BINGO_DISPATCH] 140 chip=0 task=1 core=2 cluster=0",
        "[BINGO_STATUS] 180 chip=0 replay_stuck=1 remote_done_mismatch=0 "
        "link_error=0 fenced=0x2 dead_suspect=0x2 remote_timeout=0",
        f"{driver.SIM_OK_MARKER} at 200",
    ])
    uart = ("[Host] Bingo status: replay_stuck=1 remote_done_mismatch=0 link_error=0 "
            "fenced=0x2 cerf=0x2 cerf_fb_en=0x4 cerf_fb_evt=0x4\n"
            "[Host] Check [C_l3]: PASS\n"
            "[AddHostFallback] check complete; host fallback 1\n")
    return log, uart


class R1aScenarioTests(unittest.TestCase):
    def evaluate(self, log, uart, name="r1a_hfb_one_l1"):
        # The dispatch pairing needs the generated platform header; the
        # fixture only exercises the checker itself
        sc = dict(driver.SCENARIOS[name], host_fallback_task_ids=IDS, dispatch_log=False)
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.evaluate(name, sc, log, uart)

    def test_same_image_and_t1_data_as_c4_pair(self):
        for name, template in (("r1a_hfb_one_l1", "c4_hfb_one"),
                               ("r1a_hfb_one_l1_ok", "c4_hfb_one_ok")):
            scene, original = driver.SCENARIOS[name], driver.SCENARIOS[template]
            self.assertEqual(scene["workload"], "add_host_fallback_2cluster")
            self.assertEqual(driver.t1_image_flags(scene), "")
            self.assertEqual(driver.scenario_test_cfg(scene),
                             driver.scenario_test_cfg(original))
            self.assertEqual(original["extra_cfg"], driver.L2_CFG_KEYS)

    def test_default_hardware_cfg(self):
        for name in ("r1a_hfb_one_l1", "r1a_hfb_one_l1_ok"):
            scene = driver.SCENARIOS[name]
            self.assertNotIn("extra_cfg", scene)
            self.assertEqual(scene["cfg_suffix"], "")
            self.assertEqual(scene["timeout_cycles"], driver.SCENARIOS["c4_hfb_one"]["timeout_cycles"])

    def test_fault_scene_expects_no_substitute(self):
        scene = driver.SCENARIOS["r1a_hfb_one_l1"]
        self.assertIsNone(scene["substitute"])
        self.assertTrue(scene["expect_replay_stuck"] and scene["expect_host_stuck"])
        self.assertTrue(scene["host_fallback_fence_cerf"] and scene["expect_fence"])
        self.assertNotIn("host_fallback_fence_cerf", driver.SCENARIOS["c4_hfb_one"])
        self.assertNotIn("host_fallback_fence_cerf", driver.SCENARIOS["r1a_hfb_one_l1_ok"])
        self.assertEqual(driver.SCENARIOS["r1a_hfb_one_l1_ok"]["same_as"], "r1a_hfb_one_l1")

    def test_fixture_passes(self):
        self.assertEqual(self.evaluate(*fence_cerf_fixture()), [])

    def test_mutations_rejected(self):
        log, uart = fence_cerf_fixture()
        mutations = {
            "no CERF": (drop(log, "[BINGO_CERF_FB]"), uart),
            "no stuck": (drop(log, "[BINGO_REPLAY_STUCK]"), uart),
            "replay": (log + "\n[BINGO_REPLAY] 61 chip=0 task=0 type=0 logical_core=1 "
                       "from=1 to=1 cluster=0 to_cluster=1 logical_cluster=0", uart),
            "copy on substitute": (log + "\n[BINGO_DISPATCH] 80 chip=0 task=0 core=1 cluster=1", uart),
            "no backup": (drop(log, "task=2 core=2"), uart),
            "backup before CERF": (log.replace("70 chip=0 task=2", "62 chip=0 task=2"), uart),
            "check before backup": (log.replace("140 chip=0 task=1", "65 chip=0 task=1"), uart),
            "absorbed exit": (log + "\n[BINGO_EXIT_ABSORB] 150 chip=0 task=5 logical_core=1 "
                              "logical_cluster=0 core=1 cluster=1", uart),
            "substitute exit dispatched": (log + "\n[BINGO_DISPATCH] 160 chip=0 task=7 "
                                           "core=1 cluster=1", uart),
            "event bit missing": (log, uart.replace("cerf_fb_evt=0x4", "cerf_fb_evt=0x0")),
            "replay branch marker": (log, uart.replace("host fallback 1", "host fallback 0")),
            "bad golden": (log, uart.replace("PASS", "FAIL")),
        }
        for label, (changed_log, changed_uart) in mutations.items():
            with self.subTest(mutation=label):
                self.assertTrue(self.evaluate(changed_log, changed_uart))

    def test_replayed_single_fault_still_rejects_cerf(self):
        log, uart = fence_cerf_fixture()
        sc = dict(driver.SCENARIOS["c4_hfb_one"], host_fallback_task_ids=IDS, dispatch_log=False)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(driver.evaluate("c4_hfb_one", sc, log, uart))


class R1bScenarioTests(unittest.TestCase):
    ADD = ("r1b_hfb_off", "r1b_hfb_on", "r1b_hfb_off_ok", "r1b_hfb_on_ok")
    COPY = ("r1b_copy_off", "r1b_copy_req")

    def test_one_flag_image_per_workload_on_level_2_hardware(self):
        for names, workload in ((self.ADD, "add_host_fallback_2cluster"),
                                (self.COPY, "dma_host_fallback_2cluster")):
            for name in names:
                scene = driver.SCENARIOS[name]
                self.assertEqual(scene["workload"], workload)
                self.assertTrue(scene["t1"])
                self.assertEqual(driver.t1_image_flags(scene), "-DBINGO_FENCE_FB -DBINGO_WD_ADAPT")
                self.assertEqual(scene["extra_cfg"], driver.L2_CFG_KEYS)
                self.assertEqual(scene["extra_flags"], "")
                self.assertFalse(any(scene is driver.SCENARIOS.get(member)
                                     for family in driver.T1_FAMILIES.values() for member in family))

    def test_only_the_word_differs_within_a_pair(self):
        for first, second in (("r1b_hfb_off", "r1b_hfb_on"), ("r1b_hfb_off_ok", "r1b_hfb_on_ok"),
                              ("r1b_copy_off", "r1b_copy_req")):
            off = driver.scenario_test_cfg(driver.SCENARIOS[first])
            on = driver.scenario_test_cfg(driver.SCENARIOS[second])
            self.assertEqual((off["version"], off["fence_fb"], on["fence_fb"]), (3, 0, 4))
            self.assertEqual({k: v for k, v in off.items() if k != "fence_fb"},
                             {k: v for k, v in on.items() if k != "fence_fb"})
            off_bytes, on_bytes = driver.test_cfg_bytes(off), driver.test_cfg_bytes(on)
            self.assertEqual(len(off_bytes), 384)
            self.assertEqual(off_bytes[:292] + off_bytes[296:], on_bytes[:292] + on_bytes[296:])
            self.assertEqual(struct.unpack_from("<I", off_bytes, 292), (0,))
            self.assertEqual(struct.unpack_from("<I", on_bytes, 292), (4,))

    def test_fault_data_is_the_frozen_template(self):
        for name, template in (("r1b_hfb_off", "c4_hfb_one"), ("r1b_hfb_on", "c4_hfb_one"),
                               ("r1b_hfb_off_ok", "c4_hfb_one_ok"), ("r1b_hfb_on_ok", "c4_hfb_one_ok")):
            mine = driver.scenario_test_cfg(driver.SCENARIOS[name])
            frozen = driver.scenario_test_cfg(driver.SCENARIOS[template])
            self.assertEqual((frozen["version"], mine["version"], mine["wd_adapt"]), (2, 3, 0))
            self.assertEqual({k: v for k, v in mine.items()
                              if k not in ("version", "wd_adapt", "fence_fb")},
                             {k: v for k, v in frozen.items() if k != "version"})
        for name in self.COPY:
            scene, template = driver.SCENARIOS[name], driver.SCENARIOS["s35"]
            cfg = driver.scenario_test_cfg(scene)
            self.assertEqual((cfg["fault_gid"], cfg["fault_stall_cycles"], cfg["fault_cluster"],
                              cfg["fault_core"]), (driver.HOST_FALLBACK_FAULT_GID, 0, 0, 1))
            for key in ("victim", "substitute", "substitute_cluster", "expect_takeover",
                        "expect_fence", "timeout_cycles", "confirm_timeout_cycles", "host_fallback"):
                self.assertEqual(scene[key], template[key])

    def test_expectations(self):
        scenes = driver.SCENARIOS
        on, off = scenes["r1b_hfb_on"], scenes["r1b_hfb_off"]
        self.assertIsNone(on["substitute"])
        self.assertNotIn("substitute_cluster", on)
        self.assertTrue(on["host_fallback_fence_cerf"] and on["expect_replay_stuck"]
                        and on["expect_host_stuck"] and on["expect_fence"])
        self.assertFalse(on["expect_takeover"])
        for key in ("substitute", "substitute_cluster", "expect_takeover", "expect_fence"):
            self.assertEqual(off[key], scenes["c4_hfb_one"][key])
        for name in ("r1b_hfb_off", "r1b_hfb_off_ok", "r1b_hfb_on_ok") + self.COPY:
            self.assertFalse(scenes[name].get("host_fallback_fence_cerf", False))
        for name, pair in (("r1b_hfb_off_ok", "r1b_hfb_off"), ("r1b_hfb_on_ok", "r1b_hfb_on")):
            self.assertEqual(scenes[name]["same_as"], pair)
            self.assertFalse(scenes[name]["expect_fence"])
            self.assertEqual(scenes[name]["expect_wd"], [])

    def test_word_needs_the_image_flag_and_fits_the_register(self):
        plain = driver.test_cfg_bytes(driver.test_cfg_defaults(3))
        self.assertEqual(len(plain), 384)
        self.assertEqual(plain[256:], bytes(128))
        with self.assertRaisesRegex(ValueError, "requires the BINGO_FENCE_FB image flag"):
            driver.scenario_test_cfg(dict(driver.SCENARIOS["tch0"], fence_fb=4))
        with self.assertRaisesRegex(ValueError, "not combined"):
            driver.t1_image_flags(dict(driver.SCENARIOS["c4_hfb_one"],
                                       image_flags="-DBINGO_FENCE_FB -DBINGO_SDC"))
        with self.assertRaisesRegex(ValueError, "not combined"):
            driver.t1_image_flags(dict(driver.SCENARIOS["c4_hfb_one"],
                                       image_flags="-DBINGO_FENCE_FB -DBINGO_WD_LATE"))
        for bad in (0x10000, -1, 4.0, True):
            with self.assertRaisesRegex(ValueError, "sixteen core types"):
                driver.test_cfg_bytes(dict(driver.test_cfg_defaults(3), fence_fb=bad))
        with self.assertRaisesRegex(ValueError, "unsupported test configuration layout"):
            driver.test_cfg_bytes(dict(driver.test_cfg_defaults(2), fence_fb=4))

    def test_native_layouts(self):
        """The word sits at byte 292; the older v3 layouts keep their fields and size."""
        cases = (
            (["-DBINGO_FENCE_FB"], "offsetof(bingo_test_cfg_t, fence_fb)==292"),
            (["-DBINGO_WD_ADAPT"], "offsetof(bingo_test_cfg_t, wd_adapt_reserved)==260"),
            (["-DBINGO_WD_LATE"], "offsetof(bingo_test_cfg_t, fault_pre_stall_from)==264"),
            (["-DBINGO_SDC"], "offsetof(bingo_test_cfg_t, fault_sdc_mask)==288"),
            (["-DBINGO_SDC", "-DBINGO_WD_LATE"], "offsetof(bingo_test_cfg_t, wd_adapt_reserved)==292"),
        )
        with tempfile.TemporaryDirectory() as directory:
            for index, (flags, layout) in enumerate(cases):
                source = Path(directory) / f"layout{index}.c"
                source.write_text(
                    '#include <stddef.h>\n#include "bingo_test_cfg.h"\n'
                    '_Static_assert(sizeof(bingo_test_cfg_t)==384, "size");\n'
                    f'_Static_assert({layout}, "layout");\n'
                    'int main(void) { bingo_test_cfg_t c=BINGO_TEST_CFG_INITIALIZER; '
                    'return c.version!=3 || c.wd_adapt!=0; }\n')
                exe = Path(directory) / f"layout{index}"
                subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
                                "-I", str(ROOT / "target/sw/shared/runtime"),
                                str(source), "-o", str(exe)], check=True, capture_output=True)
                subprocess.run([str(exe)], check=True)
            source = Path(directory) / "combined.c"
            source.write_text('#include "bingo_test_cfg.h"\nint main(void) { return 0; }\n')
            for other in ("-DBINGO_SDC", "-DBINGO_WD_LATE"):
                result = subprocess.run(
                    ["cc", "-std=c11", "-DBINGO_FENCE_FB", other,
                     "-I", str(ROOT / "target/sw/shared/runtime"), str(source), "-o",
                     str(Path(directory) / "combined")], capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("BINGO_FENCE_FB is not combined", result.stderr)

    def test_on_fixture_passes_and_the_other_scenes_reject_it(self):
        log, uart = fence_cerf_fixture()
        self.assertEqual(R1aScenarioTests().evaluate(log, uart, "r1b_hfb_on"), [])
        for name in ("r1b_hfb_off", "r1b_hfb_on_ok", "r1b_hfb_off_ok"):
            with self.subTest(scene=name):
                self.assertTrue(R1aScenarioTests().evaluate(log, uart, name))

    def test_copy_scenes_expect_the_replay_with_or_without_the_request(self):
        log = "\n".join([
            "[BINGO_DISPATCH] 20 chip=0 task=0 core=1 cluster=0",
            "[BINGO_WD] 50 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
            "[BINGO_WD] 60 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1",
            "[BINGO_REPLAY] 61 chip=0 task=0 type=0 logical_core=1 from=1 to=1 cluster=0 "
            "to_cluster=1 logical_cluster=0",
            "[BINGO_DISPATCH] 62 chip=0 task=0 core=1 cluster=1",
            "[BINGO_RETIRED] 63 chip=0 core=1 cluster=0",
            "[BINGO_DONE] 90 chip=0 task=0 core=1 cluster=1",
            "[BINGO_DISPATCH] 140 chip=0 task=1 core=2 cluster=0",
            "[BINGO_EXIT_ABSORB] 150 chip=0 task=5 logical_core=1 logical_cluster=0 core=1 cluster=1",
            "[BINGO_DISPATCH] 160 chip=0 task=7 core=1 cluster=1",
            "[BINGO_STATUS] 180 chip=0 replay_stuck=0 remote_done_mismatch=0 "
            "link_error=0 fenced=0x2 dead_suspect=0x2 remote_timeout=0",
            f"{driver.SIM_OK_MARKER} at 200",
        ])
        uart = ("[Host] Bingo status: replay_stuck=0 remote_done_mismatch=0 link_error=0 "
                "fenced=0x2 cerf=0x1 cerf_fb_en=0x4 cerf_fb_evt=0x0\n"
                "[Host] Check [A_L1]: PASS\n"
                "[DmaHostFallback] check complete; host fallback 0\n")
        degraded, degraded_uart = fence_cerf_fixture()
        for name in self.COPY:
            with self.subTest(scene=name):
                sc = dict(driver.SCENARIOS[name], host_fallback_task_ids=IDS)
                with contextlib.redirect_stdout(io.StringIO()):
                    problems = driver.evaluate_host_fallback(sc, log, uart)
                    rejected = driver.evaluate_host_fallback(
                        sc, degraded, degraded_uart.replace("C_l3", "A_L1").replace(
                            "AddHostFallback", "DmaHostFallback"))
                self.assertEqual(problems, [])
                self.assertTrue(rejected)


if __name__ == "__main__":
    unittest.main()
