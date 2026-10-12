"""B2: the two-offload scenes and their checker (bingo_b2_check)."""
import contextlib
import io
import unittest
from unittest import mock

import bingo_b2_check
from test_bingo_host_fallback import driver

HEALTHY_STATUS = ("[Host] Bingo status: replay_stuck=0 remote_done_mismatch=0 link_error=0 "
                  "fenced=0x0 dead_suspect=0x0\n")
FENCED_STATUS = ("[Host] Bingo status: replay_stuck=0 remote_done_mismatch=0 link_error=0 "
                 "fenced=0x2 dead_suspect=0x2\n")


def fixture(first_exit=0, second_exit=1):
    """A hang on (core 1, cluster 0) in offload 1, the reset, a healthy offload 2."""
    log = "\n".join([
        "# Loading work.testharness",
        "# [BINGO_DISPATCH] 100 chip=0 task=3 core=1 cluster=0",
        "# [BINGO_WD] 200 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
        "# [BINGO_STATUS] 300 chip=0 replay_stuck=0 remote_done_mismatch=0 link_error=0 "
        "fenced=0x2 dead_suspect=0x2 remote_timeout=0",
        "# [BINGO_WD] 300 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1",
        "# [EVLOG_BEGIN] 400",
        "# [ACC_RESET] 500 request",
        "# [BINGO_PM] 510 domain=1 level=25",
        "# [ACC_RESET] 520 reset",
        "# [B2_CDC] 520 testharness.i_cdc reset after 5 pushes, 5 pops",
        "# [ACC_RESET] 600 release",
        "# [ACC_RESET] 700 done",
        "# [BINGO_DISPATCH] 800 chip=0 task=3 core=1 cluster=0",
        "# [BINGO_DONE] 900 chip=0 task=3 core=1 cluster=0",
        "# [EVLOG_BEGIN] 950",
        "# Simulation of chip_0_0 finished successfully at 1000",
        f"# {driver.SIM_OK_MARKER} at 1000",
        "# Errors: 0, Warnings: 0",
    ]) + "\n"
    uart = (FENCED_STATUS + "[EVLOG] count=0 dropped=0\n"
            f"[B2] offload 1 ret=0 wake=10 ready=20 end=30 exit=0x{first_exit:x}\n"
            + HEALTHY_STATUS + "[EVLOG] count=0 dropped=0\n"
            f"[B2] offload 2 ret=0 wake=50 ready=60 end=70 exit=0x{second_exit:x}\n"
            "[B2] accelerator reset start=40 end=41 restored=45 image=100\n")
    return log, uart


class B2SceneTests(unittest.TestCase):
    def test_scenes_use_the_reset_hardware_and_the_chain_templates(self):
        for name in ("b2v3_ok", "b2v3_hang", "b2v3_zombie", "b2v3_zombie_back", "b2v3_hang_again"):
            scene = driver.SCENARIOS[name]
            self.assertEqual(scene["workload"], "dma_chain_twice_2cluster")
            self.assertEqual(scene["extra_cfg"], dict(driver.L2_CFG_KEYS, acc_reset="true"))
            self.assertEqual(scene["cfg_suffix"], "_l2_accrst")
            self.assertEqual(len(scene["b2_offloads"]), 2)
            for entry in scene["b2_offloads"]:
                self.assertIn(entry["template"], ("c4_chain_h100", "c4_chain_h100_ok"))
        present = driver.SCENARIOS["b2v3_present_ok"]
        self.assertEqual(present["workload"], "dma_chain_2cluster")
        self.assertNotIn("b2_offloads", present)
        self.assertEqual(present["cfg_suffix"], "_l2_accrst")

    def test_test_configuration_of_each_scene(self):
        cfg = driver.scenario_test_cfg
        scenes = driver.SCENARIOS
        self.assertEqual(cfg(scenes["b2v3_hang"]), cfg(scenes["c4_chain_h100"]))
        self.assertEqual(cfg(scenes["b2v3_ok"]), cfg(scenes["c4_chain_h100_ok"]))
        self.assertEqual(cfg(scenes["b2v3_present_ok"]), cfg(scenes["c4_chain_h100_ok"]))
        zombie = cfg(scenes["b2v3_zombie"])
        self.assertEqual((zombie["fault_gid"], zombie["fault_stall_cycles"], zombie["fault_cluster"],
                          zombie["fault_core"], zombie["user"]), (6, 700000, 1, 1, [0, 0, 0, 0]))
        self.assertEqual(cfg(scenes["b2v3_zombie_back"]), dict(zombie, user=[0, 300000, 0, 0]))
        self.assertEqual(cfg(scenes["b2v3_hang_again"]),
                         dict(cfg(scenes["c4_chain_h100"]), user=[1, 0, 0, 0]))

    def test_existing_scenes_are_not_b2_runs(self):
        for name, scene in driver.SCENARIOS.items():
            if not name.startswith("b2v3_"):
                self.assertNotIn("b2_offloads", scene, name)
                self.assertNotIn("acc_reset", scene.get("extra_cfg") or {}, name)


class B2SplitTests(unittest.TestCase):
    def test_cut_at_the_reset(self):
        log, uart = fixture()
        logs, uarts, times = bingo_b2_check.split_offloads(log, uart)
        self.assertEqual(times, dict(request=500, reset=520, release=600, done=700))
        self.assertIn("[BINGO_WD] 300", logs[0])
        self.assertIn("[BINGO_PM] 510", logs[0])            # isolation phase: still offload 1
        self.assertIn(driver.SIM_OK_MARKER, logs[0])        # the end of the run is shared
        self.assertNotIn("[BINGO_DISPATCH] 800", logs[0])
        self.assertNotIn("[BINGO_WD]", logs[1])
        self.assertIn("[BINGO_DONE] 900", logs[1])
        self.assertIn(driver.SIM_OK_MARKER, logs[1])
        self.assertIn("offload 1 ret=0", uarts[0])
        self.assertNotIn("offload 2", uarts[0])
        self.assertIn("fenced=0x0", uarts[1])
        self.assertNotIn("fenced=0x2", uarts[1])

    def test_incomplete_or_repeated_reset(self):
        log, uart = fixture()
        for broken in (log.replace("# [ACC_RESET] 600 release\n", ""),
                       log.replace("# [ACC_RESET] 700 done\n", ""),
                       log + "# [ACC_RESET] 1100 request\n",
                       log.replace("[ACC_RESET] 600 release", "[ACC_RESET] 710 release")):
            with self.assertRaises(ValueError):
                bingo_b2_check.split_offloads(broken, uart)

    def test_event_while_the_quadrant_is_in_reset(self):
        log, uart = fixture()
        broken = log.replace("# [ACC_RESET] 600 release\n",
                             "# [BINGO_DONE] 550 chip=0 task=3 core=1 cluster=0\n"
                             "# [ACC_RESET] 600 release\n")
        with self.assertRaisesRegex(ValueError, "while the quadrant is in reset"):
            bingo_b2_check.split_offloads(broken, uart)


class B2CheckTests(unittest.TestCase):
    """The boundary checks; the per-offload checkers are covered by their own suites."""

    def evaluate(self, log, uart, name="b2v3_hang", per_offload=None, **changes):
        scene = dict(driver.SCENARIOS[name], dispatch_graph_csv=None, **changes)
        seen = []

        def offload(label, spec, offload_log, offload_uart):
            seen.append((label, spec))
            return list((per_offload or {}).get(label, []))

        with mock.patch.object(bingo_b2_check, "healthy_problems", return_value=[]), \
                mock.patch.object(bingo_b2_check, "check_evlog", return_value=[]), \
                contextlib.redirect_stdout(io.StringIO()):
            problems = bingo_b2_check.evaluate_b2(name, scene, log, uart, driver.SCENARIOS, offload, 3)
        return problems, seen

    def test_pass_and_per_offload_specs(self):
        problems, seen = self.evaluate(*fixture())
        self.assertEqual(problems, [])
        self.assertEqual([label for label, _ in seen], ["b2v3_hang/1", "b2v3_hang/2"])
        first, second = seen[0][1], seen[1][1]
        self.assertTrue(first["expect_fence"] and not second["expect_fence"])
        self.assertEqual(first["workload"], "dma_chain_2cluster")
        self.assertTrue(second["c4_control"])
        self.assertNotIn("b2_offloads", first)

    def test_driver_evaluate_dispatches_to_the_b2_checker(self):
        log, uart = fixture()
        with mock.patch.dict(driver.evaluate.__globals__,
                             {"evaluate_b2": lambda *args: ["called", args[0]]}):
            self.assertEqual(driver.evaluate("b2v3_hang", driver.SCENARIOS["b2v3_hang"], log, uart),
                             ["called", "b2v3_hang"])

    def test_per_offload_problem_is_reported(self):
        problems, _ = self.evaluate(*fixture(), per_offload={"b2v3_hang/2": ["boom"]})
        self.assertEqual(problems, ["offload 2: boom"])

    def test_spurious_cdc_transaction(self):
        log, uart = fixture()
        log = log.replace("# [ACC_RESET] 700 done\n", "# [ACC_RESET] 700 done\n"
                          "# ** Error: [B2_CDC] testharness.i_cdc channel 0: pop 1 without a push "
                          "(pushes 0, resets 2)\n")
        problems, _ = self.evaluate(log, uart)
        self.assertTrue(any("spurious CDC" in p for p in problems), problems)
        self.assertTrue(any("simulator error" in p for p in problems), problems)

    def test_wrong_exit_word(self):
        problems, _ = self.evaluate(*fixture(second_exit=0))
        self.assertEqual(problems, ["offload 2: host result ret=0 exit=0x0, expected ret=0 exit=0x1"])
        problems, _ = self.evaluate(*fixture(first_exit=1))
        self.assertEqual(problems, ["offload 1: host result ret=0 exit=0x1, expected ret=0 exit=0x0"])

    def test_missing_host_lines(self):
        log, uart = fixture()
        problems, _ = self.evaluate(log, "\n".join(l for l in uart.splitlines() if "offload 2" not in l) + "\n")
        self.assertIn("expected one '[B2] offload 1' and one '[B2] offload 2' result line", problems)
        problems, _ = self.evaluate(log, uart.replace("[B2] accelerator reset", "[B2] reset"))
        self.assertIn("expected one '[B2] accelerator reset' line", problems)

    def test_dropped_done(self):
        log, uart = fixture()
        late = log.replace("# [BINGO_DONE] 900", "# [BINGO_FENCE] 850 chip=0 core=1 cluster=0 "
                           "dropped done task=3\n# [BINGO_DONE] 900")
        problems, _ = self.evaluate(late, uart)
        self.assertEqual(problems, ["offload 2: dropped done [(0, 1, 0, 3)], expected []"])
        # b2v3_zombie_back expects the stalled core's done before the reset, and only there
        problems, _ = self.evaluate(log, uart, name="b2v3_zombie_back")
        self.assertIn("offload 1: dropped done [], expected [(0, 1, 1, 6)]", problems)

    def test_fenced_slot_must_work_again(self):
        log, uart = fixture()
        log = log.replace("# [BINGO_DISPATCH] 800 chip=0 task=3 core=1 cluster=0\n", "")
        problems, _ = self.evaluate(log, uart)
        self.assertEqual(problems, ["slot (0, 1, 0) fenced in offload 1 got no task in offload 2"])

    def test_host_status_is_checked_per_offload(self):
        log, uart = fixture()
        problems, _ = self.evaluate(log, uart.replace(HEALTHY_STATUS, FENCED_STATUS))
        self.assertEqual(len(problems), 1)
        self.assertTrue(problems[0].startswith("offload 2: host status"), problems)

    def test_run_without_a_reset(self):
        log, uart = fixture()
        log = "\n".join(l for l in log.splitlines() if "[ACC_RESET]" not in l) + "\n"
        problems, _ = self.evaluate(log, uart)
        self.assertEqual(len(problems), 1)
        self.assertIn("accelerator reset markers []", problems[0])


if __name__ == "__main__":
    unittest.main()
