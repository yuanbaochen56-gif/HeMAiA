"""C5 energy proxy, knob checks and the generated matrix (DESIGN 9.24.7)."""
from fractions import Fraction
import importlib.util
from pathlib import Path
import tempfile
import unittest

from bingo_c5 import (energy, group_problems, knob_problems, pairs, pm_start, pm_timeline,
                      residency, run_metrics)

spec = importlib.util.spec_from_file_location(
    "watchdog_c5", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

LOG = """[BINGO_PM] 1000 domain=1 level=6
[BINGO_PM] 1000 domain=2 level=6
[BINGO_DISPATCH] 2000 chip=0 task=0 core=1 cluster=0
[BINGO_PM] 3000 domain=2 level=25
[BINGO_WD] 5000 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0
[BINGO_WD] 7000 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1
[BINGO_PM] 7000 domain=1 level=25
[BINGO_PM] 7000 domain=2 level=3
[BINGO_DISPATCH] 7100 chip=0 task=0 core=1 cluster=1
[BINGO_DONE] 8000 chip=0 task=0 core=1 cluster=1
[BINGO_PM] 8000 domain=2 level=25
[BINGO_DISPATCH] 8500 chip=0 task=1 core=2 cluster=0
[EVLOG_BEGIN] 9000
All chips finished successfully at 9500
"""
GRAPH = "ID,Kernel,Chiplet,Cluster,Core,Type\n0,copy,0x0,0,1,2\n1,__host_bingo_kernel_exit,0x0,0,2,0\n"


class EnergyTests(unittest.TestCase):
    def test_residency_clips_the_window(self):
        events = [(0, 6), (100, 25), (300, 6)]
        self.assertEqual(residency(events, 50, 350), {6: 100, 25: 200})

    def test_unknown_start_level_rejected(self):
        with self.assertRaises(ValueError):
            residency([(100, 6)], 50, 200)

    def test_unexpected_level_rejected(self):
        with self.assertRaises(ValueError):
            pm_timeline("[BINGO_PM] 1 domain=1 level=7\n")

    def test_energy_weights(self):
        # 6/6 = 1 for 1 ms, 6/25 for 1 ms, 6/3 = 2 for 1 ms, plus leakage
        timeline = {1: [(0, 6), (10**9, 25)], 2: [(0, 3)]}
        self.assertEqual(energy(timeline, 0, 2 * 10**9, Fraction(0)),
                         1 + Fraction(6, 25) + 4)
        self.assertEqual(energy(timeline, 0, 2 * 10**9, Fraction(1, 10)),
                         1 + Fraction(6, 25) + 4 + Fraction(4, 10))

    def test_run_metrics_fault(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = Path(directory) / "final_dfg.csv"
            graph.write_text(GRAPH)
            m = run_metrics(LOG, "", graph, fault=True)
        self.assertEqual(pm_start(pm_timeline(LOG)), 1000)
        self.assertEqual((m["T_fence_ps"], m["T_evlog_ps"], m["recovery_ps"]), (7000, 9000, 2000))
        self.assertEqual(m["d2_l3_ps"], 1000)
        self.assertEqual(m["levels_seen"], [3, 6, 25])
        self.assertAlmostEqual(m["E_L0"], m["E_L0_before_fence"] + m["E_L0_after_fence"])
        # domain 1: 6000 ps normal + 2000 idle; domain 2: 2000 normal + 4000 idle + 1000 boost + 1000 idle
        expected = (6000 + 2000 * Fraction(6, 25) + 2000 + 5000 * Fraction(6, 25) + 2000) / 10**9
        self.assertAlmostEqual(m["E_L0"], float(expected))

    def test_fault_flag_must_match_log(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = Path(directory) / "final_dfg.csv"
            graph.write_text(GRAPH)
            with self.assertRaises(ValueError):
                run_metrics(LOG, "", graph, fault=False)


class KnobTests(unittest.TestCase):
    def scene(self, fault=True, **knobs):
        values = dict(W=0, boost=0, D=0, A=0, S=0)
        values.update(knobs)
        return dict(c5_knobs=values, c5_fault=fault)

    def metrics(self, levels, holds=0):
        return dict(levels_seen=levels, recovery_hold_events=holds)

    def test_boost_must_act_in_fault_runs(self):
        self.assertEqual(knob_problems(self.scene(boost=1), self.metrics([3, 6, 25])), [])
        self.assertTrue(knob_problems(self.scene(boost=1), self.metrics([6, 25])))
        self.assertTrue(knob_problems(self.scene(fault=False, boost=1), self.metrics([3, 6, 25])))

    def test_recovery_hold(self):
        self.assertTrue(knob_problems(self.scene(W=1000), self.metrics([6, 25])))
        self.assertEqual(knob_problems(self.scene(W=1000), self.metrics([6, 25], holds=2)), [])
        self.assertEqual(knob_problems(self.scene(fault=False, W=10000), self.metrics([6, 25])), [])
        self.assertTrue(knob_problems(self.scene(), self.metrics([6, 25], holds=1)))

    def test_servo(self):
        self.assertTrue(knob_problems(self.scene(A=1000, S=12), self.metrics([6, 25])))
        self.assertTrue(knob_problems(self.scene(), self.metrics([6, 12, 25])))

    def test_group_and_pairs(self):
        knobs = dict(W=0, boost=0, D=0, A=0, S=0)
        rows = [dict(scenario="f", fault=True, knobs=knobs, dispatches=[[0, 1, 0]], T_pm0_ps=1,
                     T_evlog_ps=10, makespan_ps=9, E_L0=5.0, **{"E_L0.1": 6.0}),
                dict(scenario="h", fault=False, knobs=knobs, dispatches=[[0, 1, 1]], T_pm0_ps=1,
                     T_evlog_ps=4, makespan_ps=3, E_L0=2.0, **{"E_L0.1": 2.5})]
        self.assertEqual(group_problems(rows), [])
        self.assertEqual(pairs(rows)[0]["delta_T_evlog_ps"], 6)
        rows[1]["T_pm0_ps"] = 2
        self.assertTrue(group_problems(rows))


class MatrixTests(unittest.TestCase):
    def test_matrix(self):
        scenes = watchdog.c5_scenarios()
        self.assertEqual(len(scenes), 21)
        groups = [scene["c5_group"] for scene in scenes.values()]
        self.assertEqual((groups.count("F"), groups.count("H"), groups.count("SV")), (12, 5, 4))
        base = watchdog.c4_scenarios()
        reference = watchdog.scenario_test_cfg(base["c4_chain_h100"])
        self.assertEqual(watchdog.scenario_test_cfg(scenes["c5_f_w0_b0_d0"]), reference)
        healthy = watchdog.scenario_test_cfg(base["c4_chain_h100_ok"])
        self.assertEqual(watchdog.scenario_test_cfg(scenes["c5_h_w0_b0_d0"]), healthy)
        for name, scene in scenes.items():
            cfg = watchdog.scenario_test_cfg(scene)
            knobs = scene["c5_knobs"]
            self.assertEqual(cfg["recovery_hold"], knobs["W"], name)
            self.assertEqual(cfg["pm_idle_entry_delay"], knobs["D"], name)
            self.assertEqual(cfg["pm_access_wake_hold"], knobs["A"], name)
            self.assertEqual(cfg["pm_access_level"], knobs["S"], name)
            self.assertEqual(cfg["pm_boost_power_level"], 3 * knobs["boost"], name)
            changed = {k for k in cfg if cfg[k] != (reference if scene["c5_fault"] else healthy)[k]}
            self.assertLessEqual(changed, {"recovery_hold", "pm_idle_entry_delay", "pm_access_wake_hold",
                                           "pm_access_level", "pm_boost_power_level", "boost_policy"}, name)
            if not scene["c5_fault"]:
                self.assertIn(scene["same_as"], scenes)


if __name__ == "__main__":
    unittest.main()
