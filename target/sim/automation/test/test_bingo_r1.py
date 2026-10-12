"""R1a: the C4 int32-add pair on default hardware, and its checker mode."""
import contextlib
import io
import unittest

from test_bingo_host_fallback import driver, IDS, drop


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


if __name__ == "__main__":
    unittest.main()
