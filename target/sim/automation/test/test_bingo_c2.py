"""Handwritten C2 logs, graph mapping, health checks and scenario matrix."""
import importlib.util
from pathlib import Path
import re
import tempfile
import unittest

from bingo_c2 import (compare_cross_image, compare_same_image, healthy_problems,
                      run_metrics, task_mapping)
from pm_ticks import PMConfig

spec = importlib.util.spec_from_file_location(
    "watchdog_c2", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

LOG = """# [BINGO_PM] 50 domain=1 level=6
# [BINGO_DISPATCH] 100 chip=0 task=0 core=1 cluster=0
# [BINGO_DONE] 150 chip=0 task=0 core=1 cluster=0
# [BINGO_DISPATCH] 160 chip=0 task=1 core=1 cluster=0
# [BINGO_PM] 170 domain=1 level=25
# [BINGO_DONE] 180 chip=0 task=1 core=1 cluster=0
# [BINGO_RETIRED] 190 chip=0 core=1 cluster=0
# [BINGO_PM] 195 domain=1 level=6
# [BINGO_DISPATCH] 200 chip=0 task=2 core=2 cluster=0
# [EVLOG_BEGIN] 220
All chips finished successfully at 300
"""
UART = ("[Host] Bingo status: " + " ".join(f"{field}=0" for field in (
    "replay_stuck", "remote_done_mismatch", "link_error", "fenced", "dead_suspect",
    "remote_timeout", "park_fail", "cerf_fb_evt", "risk", "replay_blocked")) +
        " cerf=0x3 cerf_fb_en=0x4\nCheck [output]: PASS\n"
        "[EVLOG] ts=19 code=0x0b slot=0:1 arg=0x0000\n")
GRAPH = """ID,Chiplet,Cluster,Core,Type,Kernel
0,00,0,1,normal,__snax_bingo_kernel_idma_1d_copy
1,00,0,1,normal,__snax_bingo_kernel_exit
2,00,0,2,normal,__host_bingo_kernel_exit
"""


class C2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.graph = Path(self.tmp.name) / "final_dfg.csv"
        self.graph.write_text(GRAPH)
        self.pm = PMConfig(10, 6, 25, {(1, 0): 1})
        self.pm.core_types = {1: 2, 2: 0}
        self.a = run_metrics(LOG, UART, self.graph, self.pm)

    def tearDown(self):
        self.tmp.cleanup()

    def test_metrics_and_exact_ticks(self):
        self.assertEqual((self.a["T0_ps"], self.a["T_exit_ps"], self.a["T_evlog_ps"],
                          self.a["T_eoc_ps"]), (100, 200, 220, 300))
        self.assertEqual((self.a["makespan_hw_ps"], self.a["host_view_ps"],
                          self.a["n_dispatch"], self.a["n_done"], self.a["evlog_items"]),
                         (100, 120, 3, 2, 1))
        task = self.a["tasks"][1]
        self.assertEqual(task["busy_ticks_exact"], dict(numerator=31, denominator=25))
        self.assertIsNone(self.a["tasks"][2]["busy_ps"])

    def test_completely_identical(self):
        result = compare_same_image(self.a, self.a)
        self.assertTrue(result["identical_abs"])
        self.assertTrue(result["identical_rel"])
        self.assertEqual(result["first_diffs"], [])
        cross = compare_cross_image(self.a, self.a, task_mapping(self.a, self.a))
        self.assertTrue(cross["identical_rel"])
        self.assertEqual(cross["max_abs_d_busy_ps"], 0)

    def test_whole_run_translation(self):
        log = re.sub(r"(\[[A-Z0-9_]+\]) (\d+)", lambda m: f"{m[1]} {int(m[2]) + 1000}", LOG)
        log = log.replace("successfully at 300", "successfully at 1300")
        b = run_metrics(log, UART, self.graph, self.pm)
        result = compare_same_image(self.a, b)
        self.assertFalse(result["identical_abs"])
        self.assertTrue(result["identical_rel"])
        self.assertEqual(result["t0_delta_ps"], 1000)
        self.assertEqual(result["d_makespan_hw_ps"], 0)
        self.assertTrue(compare_cross_image(self.a, b, task_mapping(self.a, b))["identical_rel"])

    def test_kth_event_first_difference_quotes(self):
        log = LOG.replace("[BINGO_DONE] 150", "[BINGO_DONE] 151")
        b = run_metrics(log, UART, self.graph, self.pm)
        result = compare_same_image(self.a, b)
        self.assertFalse(result["identical_abs"])
        diff = result["first_diffs"][0]
        self.assertEqual((diff["stream"], diff["index"]), ("slot:0:0:1:DISPATCH_DONE", 1))
        self.assertIn("[BINGO_DONE] 150", diff["a"])
        self.assertIn("[BINGO_DONE] 151", diff["b"])
        cross = compare_cross_image(self.a, b, task_mapping(self.a, b))
        self.assertEqual(next(row for row in cross["task_deltas"] if row["task_a"] == 0)["d_busy_ps"], 1)

    def test_missing_or_nonunique_mapping_is_error(self):
        for mapping in (dict(pairs=[(0, 0), (1, 1)]),
                        dict(pairs=[(0, 0), (0, 0), (1, 1), (2, 2)])):
            with self.assertRaisesRegex(ValueError, "incomplete or not unique"):
                compare_cross_image(self.a, self.a, mapping)
        b = dict(self.a, tasks=self.a["tasks"] + [
            dict(self.a["tasks"][0], task=3)])
        with self.assertRaisesRegex(ValueError, "unmatched tasks"):
            task_mapping(self.a, b, allow_fallback_only=True)

    def test_fallback_only_is_explicit_and_reported(self):
        a = dict(self.a, tasks=self.a["tasks"] + [
            dict(self.a["tasks"][0], task=3, dispatch_ps=None, busy_ps=None,
                 busy_ticks=None, busy_ticks_exact=None)])
        mapping = task_mapping(a, self.a, allow_fallback_only=True)
        result = compare_cross_image(a, self.a, mapping)
        self.assertEqual(mapping["fallback_only"], [3])
        self.assertFalse(result["task_deltas"][-1]["dispatched"])

    def test_pm_short_windows_only_removed_cross_image(self):
        log = LOG.replace("# [BINGO_DISPATCH] 100", "# [BINGO_PM] 60 domain=1 level=25\n"
                          "# [BINGO_PM] 70 domain=1 level=6\n# [BINGO_DISPATCH] 100")
        b = run_metrics(log, UART, self.graph, self.pm)
        self.assertFalse(compare_same_image(self.a, b)["identical_rel"])
        result = compare_cross_image(self.a, b, task_mapping(self.a, b))
        self.assertTrue(result["identical_rel"])
        self.assertEqual(len(result["pm_removed_b"]["pm:1"]),
                         len(result["pm_removed_a"]["pm:1"]) + 1)

    def test_healthy_controls_status_checks_and_exit(self):
        self.assertEqual(healthy_problems(LOG, UART, self.graph), [])
        for event in ("LATE", "WD", "FENCE", "REPLAY", "REPLAY_STUCK", "REMAP", "CERF_FB",
                      "RISK", "RISK_CONFIRM", "TYPE_CONFIRM", "PARK", "RECOVERY_HOLD",
                      "EXPORT", "IMPORT", "REMOTE_TIMEOUT", "ASSERT"):
            self.assertTrue(healthy_problems(LOG + f"[BINGO_{event}] 250\n", UART, self.graph))
        self.assertTrue(healthy_problems(LOG, UART.replace("code=0x0b", "code=0x01"), self.graph))
        self.assertTrue(healthy_problems(LOG, UART.replace("slot=0:1", "slot=0:0"), self.graph))
        self.assertTrue(healthy_problems(LOG, UART.replace("risk=0", "risk=1"), self.graph))
        self.assertTrue(healthy_problems(LOG, UART.replace("PASS", "FAIL"), self.graph))

    def test_scenes_and_data(self):
        scenes = watchdog.c2_scenarios({"chain": 2})
        self.assertEqual(len(scenes), 15)
        for name, scene in scenes.items():
            self.assertTrue(scene["t1"] and scene["dispatch_log"] and scene["expect_eoc"])
            self.assertIsNone(scene["fault_stall_cycles"])
            self.assertEqual(scene["evlog_enable"], 1)
            self.assertFalse(scene["evlog_pair"])
            data = watchdog.scenario_test_cfg(scene)
            if scene["c2_variant"] != "on":
                expected = watchdog.test_cfg_defaults()
                expected["evlog_enable"] = 1
                self.assertEqual(data, expected, name)
            else:
                k = 2 if "chain" in name else 1
                self.assertEqual((data["risk_late"], data["risk_confirm"],
                                  data["wd_type_h"][2], data["wd_type_c"][2]),
                                 (30000 * k, 125000 * k, 20000 * k, 40000 * k))
                self.assertEqual((data["risk_policy"], data["boost_policy"],
                                  data["pm_boost_power_level"], data["cerf_fb_enable"],
                                  data["cerf_fb_clear"], data["cerf_fb_set"],
                                  data["recovery_hold"]), (2, 0x10101, 3, 1, 31, 30, 1000))
        self.assertNotIn("cerf_fallback", scenes["c2_moe2_nofb"])
        self.assertNotIn("host_fallback", scenes["c2_dmafb_nofb"])
        for name in ("c2_chain_off", "c2_dummy_nohb", "c2_moe2_nofb"):
            scene = scenes[name]
            self.assertIsNone(scene["timeout_cycles"])
            self.assertIsNone(scene["confirm_timeout_cycles"])
            self.assertIsNone(scene["extra_cfg"])

    def test_image_flag_whitelist_and_family_exclusion(self):
        self.assertEqual(watchdog.t1_image_flags(watchdog.SCENARIOS["c2_chain_nohb"]),
                         "-DBINGO_WD_NO_HEARTBEAT")
        for flag in ("-DBINGO_WD_NO_HEARTBEAT=1", "-DOTHER", "-DBINGO_TEST_CFG=0"):
            with self.assertRaises(ValueError):
                watchdog.t1_image_flags(dict(t1=True, image_flags=flag))
        with self.assertRaisesRegex(ValueError, "same-image family"):
            watchdog.t1_image_flags(dict(t1=True, same_as="tch0",
                                         image_flags="-DBINGO_WD_NO_HEARTBEAT"))
        self.assertFalse(any(name.startswith("c2_") and name.endswith("_nohb")
                             for names in watchdog.T1_FAMILIES.values() for name in names))


if __name__ == "__main__":
    unittest.main()
