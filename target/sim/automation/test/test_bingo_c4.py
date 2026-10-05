"""Exact event phases, same-image costs and the approved C4 matrix."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bingo_c4 import control_cost, phase_metrics
from pm_ticks import PMConfig

spec = importlib.util.spec_from_file_location(
    "watchdog_c4", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

LOG = """[BINGO_PM] 0 domain=1 level=6
[BINGO_DISPATCH] 100 chip=0 task=0 core=1 cluster=0
[BINGO_WD] 1100 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0
[BINGO_WD] 2100 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1
[BINGO_REPLAY] 2130 chip=0 task=0 type=0 logical_core=1 from=1 to=1 cluster=0 to_cluster=1
[BINGO_DISPATCH] 2140 chip=0 task=0 core=1 cluster=1
[BINGO_DONE] 2200 chip=0 task=0 core=1 cluster=1
[BINGO_DISPATCH] 2400 chip=0 task=1 core=2 cluster=0
[EVLOG_BEGIN] 2450
All chips finished successfully at 2500
"""
UART = """[EVLOG] ts=110 code=0x01 slot=0:1 arg=0x0000
[EVLOG] ts=210 code=0x03 slot=0:1 arg=0x0000
[EVLOG] ts=213 code=0x04 slot=0:1 arg=0x4000
[EVLOG] count=3 dropped=0
"""


class C4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.graph = Path(self.tmp.name) / "graph.csv"
        self.graph.write_text("ID,Kernel,Chiplet,Cluster,Core,Type\n"
            "0,__snax_bingo_kernel_int32_add,0x0,0,1,normal\n"
            "1,__host_bingo_kernel_exit,0x0,0,2,normal\n"
            "2,__host_bingo_kernel_add_i32,0x0,0,2,normal\n")
        self.pm = PMConfig(10, 6, 25, {(1, 0): 1, (1, 1): 2})

    def measure(self, log=LOG, uart=UART, family="H"):
        return phase_metrics(log, uart, self.graph, self.pm, family, 0, (0, 1, 0), (0, 1, 1))

    def test_replay_phases_and_raw_pm_ticks(self):
        result = self.measure()
        phase = result["phases"][0]
        for field, expected in dict(detect_ps=1000, fence_total_ps=2000, confirm_ps=1000,
                                    act_ps=30, restart_ps=10, exec_ps=60, tail_ps=200,
                                    host_tail_ps=50, detect_ticks=100, fence_total_ticks=200).items():
            self.assertEqual(phase[field], expected, field)
        self.assertEqual(phase["detect_ticks_exact"], dict(numerator=100, denominator=1))
        self.assertEqual({row["source"] for row in result["events"]}, {"sim", "evlog"})
        json.dumps(result)

    def test_pm_slow_overlap_is_not_nominal_clock_time(self):
        log = LOG.replace("[BINGO_PM] 0 domain=1 level=6",
                          "[BINGO_PM] 0 domain=1 level=12\n[BINGO_PM] 600 domain=1 level=6")
        self.assertEqual(self.measure(log=log)["phases"][0]["detect_ticks"], 75)

    def test_evlog_mismatch_or_empty_fails(self):
        for uart in (UART.replace("ts=210", "ts=211"), "[EVLOG] count=0 dropped=0\n",
                     UART.replace("dropped=0", "dropped=1")):
            with self.assertRaisesRegex(ValueError, "EVLOG correspondence"):
                self.measure(uart=uart)

    def test_missing_replay_done_or_wrong_substitute_fails(self):
        for log in (LOG.replace("[BINGO_DONE] 2200 chip=0 task=0 core=1 cluster=1\n", ""),
                    LOG.replace("2140 chip=0 task=0 core=1 cluster=1",
                                "2140 chip=0 task=0 core=0 cluster=1")):
            with self.assertRaises(ValueError):
                self.measure(log=log)

    def test_cerf_rest_uses_first_post_action_dispatch(self):
        log = LOG.replace(
            "[BINGO_REPLAY] 2130 chip=0 task=0 type=0 logical_core=1 from=1 to=1 cluster=0 to_cluster=1",
            "[BINGO_CERF_FB] 2130 type 2 clear g0 set g1")
        log = log.replace("task=0 core=1 cluster=1", "task=2 core=2 cluster=0")
        uart = UART.replace("code=0x04 slot=0:1 arg=0x4000",
                            "code=0x0a slot=0:2 arg=0x0020")
        phase = self.measure(log, uart, "moe2")["phases"][0]
        self.assertEqual(phase["rest_ps"], 260)
        self.assertIsNone(phase["exec_ps"])

    def test_double_dead_core_has_two_segments(self):
        log = LOG.replace("[BINGO_DONE] 2200 chip=0 task=0 core=1 cluster=1",
            "[BINGO_WD] 2240 chip=0 core=1 cluster=1 dead_suspect=1 fenced=0\n"
            "[BINGO_WD] 2340 chip=0 core=1 cluster=1 dead_suspect=1 fenced=1\n"
            "[BINGO_CERF_FB] 2350 type 2 clear g0 set g1\n"
            "[BINGO_DISPATCH] 2360 chip=0 task=2 core=2 cluster=0\n"
            "[BINGO_DONE] 2380 chip=0 task=2 core=2 cluster=0")
        uart = UART.replace("[EVLOG] count=3 dropped=0", (
            "[EVLOG] ts=224 code=0x01 slot=1:1 arg=0x0000\n"
            "[EVLOG] ts=234 code=0x03 slot=1:1 arg=0x0000\n"
            "[EVLOG] ts=235 code=0x0a slot=0:2 arg=0x0020\n"
            "[EVLOG] count=6 dropped=0"))
        phases = self.measure(log, uart, "hfb_both")["phases"]
        self.assertEqual(len(phases), 2)
        self.assertEqual(phases[0]["t_rd_ps"], phases[1]["t_d_ps"])
        self.assertEqual(phases[1]["t_rd_ps"], 2360)
        self.assertEqual(phases[1]["rest_ps"], 40)

    def test_cost_requires_image_and_fault_fields_to_match(self):
        cfg = watchdog.test_cfg_defaults()
        fault = dict(image_id="same", test_cfg_json=dict(cfg, fault_gid=9),
                     T_exit_ps=300, T_evlog_ps=400)
        control = dict(image_id="same", test_cfg_json=cfg, T_exit_ps=200, T_evlog_ps=250)
        self.assertEqual(control_cost(fault, control), dict(cost_hw_ps=100, cost_host_ps=150))
        with self.assertRaisesRegex(ValueError, "image IDs"):
            control_cost(fault, dict(control, image_id="other"))
        with self.assertRaisesRegex(ValueError, "beyond fault_gid"):
            control_cost(fault, dict(control, test_cfg_json=dict(cfg, fault_core=0)))

    def test_matrix_has_twenty_scenes_and_strict_fault_controls(self):
        scenes = watchdog.c4_scenarios()
        self.assertEqual(len(scenes), 20)
        for name, scene in scenes.items():
            self.assertTrue(scene["t1"] and scene["dispatch_log"])
            self.assertEqual(scene["evlog_enable"], 1)
            self.assertFalse(scene["evlog_pair"])
            if scene["c4_role"] == "control":
                self.assertEqual(watchdog.scenario_test_cfg(scene)["fault_gid"], 0xFFFFFFFF)
                self.assertTrue(scene["evlog_allow_empty"])
                self.assertIn(scene["same_as"], scenes)
                self.assertIsNone(scene.get("substitute"))
            else:
                self.assertFalse(scene["evlog_allow_empty"])
        self.assertEqual({key: scenes[key]["timeout_cycles"] for key in (
            "c4_chain_h50", "c4_chain_h100", "c4_chain_h200")},
            dict(c4_chain_h50=50000, c4_chain_h100=100000, c4_chain_h200=200000))
        for name in ("c4_chain_a5_20", "c4_chain_a5_40"):
            self.assertTrue(scenes[name]["expect_type_confirm"])
            self.assertEqual(scenes[name]["wd_type_c"][2], 2*scenes[name]["wd_type_h"][2])

    def test_existing_healthy_templates_keep_their_checker(self):
        for name, baseline in (("c4_eec_ok", "t46"), ("c4_hfb_both_ok", "t55")):
            scene = watchdog.SCENARIOS[name]
            for key, value in watchdog.SCENARIOS[baseline].items():
                self.assertEqual(scene[key], value, key)

    def test_a5_c4_calls_the_existing_type_checker(self):
        scene = dict(c4=True, wd_type_h=[0]*16, fault_stall_cycles=None,
                     expect_eoc=True, workload="fixture")
        with patch.object(watchdog, "evaluate_type_threshold", return_value=["type fixture"]) as check:
            result = watchdog.evaluate("c4_fixture", scene, "", "")
        check.assert_called_once()
        self.assertIn("type fixture", result)


if __name__ == "__main__":
    unittest.main()
