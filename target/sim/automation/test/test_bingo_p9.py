import importlib.util
from pathlib import Path
import unittest

from bingo_p9 import late_events, table_at, healthy_problems, healthy_pair_problems
from bingo_p9_scenarios import healthy_scenarios, evaluation_scenarios, late_control

spec = importlib.util.spec_from_file_location(
    "watchdog_p9", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class PredictiveCheckerTests(unittest.TestCase):
    def test_parse_late_sources_and_reject_malformed_or_short_interval(self):
        for source in ("static", "adaptive"):
            log = f"[BINGO_ADAPT_LATE] 1200 slot=1 core=1 cluster=0 type=2 class=1 interval=1200 threshold=1000 source=  {source}"
            event, = late_events(log)
            self.assertEqual(event["source"], source)
            self.assertEqual(event["time_ps"], 1200)
            self.assertEqual(event["slot"], 1)
        for log in ("[BINGO_ADAPT_LATE] missing fields",
                    "[BINGO_ADAPT_LATE] 1 slot=1 core=1 cluster=0 type=2 class=1 interval=1 threshold=2 source=adaptive"):
            with self.assertRaises(ValueError):
                late_events(log)

    def test_table_snapshot_uses_every_change_and_excludes_fence_edge(self):
        log = "\n".join(f"[TRACE] P9_TABLE {time} type=2 class=1 max=300 n={n} load=1"
                        for time, n in ((10, 1), (20, 2), (30, 3)))
        self.assertEqual(table_at(log, 30), {"2:1": dict(M=300, N=2, L=1)})
        self.assertEqual(table_at(log), {"2:1": dict(M=300, N=3, L=1)})
        self.assertEqual(table_at(log, 10), {})
        with self.assertRaises(ValueError):
            table_at("[TRACE] P9_TABLE bad row")

    def test_health_and_pair_gates_do_not_hide_events_or_ps_changes(self):
        for prefix in ("ADAPT_LATE", "LATE", "WD", "FENCE", "RISK", "RISK_CONFIRM", "PARK"):
            self.assertTrue(healthy_problems(f"[BINGO_{prefix}] 10"))
        before = "[BINGO_DONE] 10\nAll chips finished successfully at 20"
        after = before + "\n[TRACE] P9_TABLE 10 type=2 class=1 max=5 n=1 load=1"
        self.assertFalse(healthy_pair_problems(before, b"ok", after, b"ok"))
        self.assertTrue(healthy_pair_problems(before, b"ok", after.replace("at 20", "at 21"), b"ok"))
        self.assertTrue(healthy_pair_problems(before, b"ok", after, b"bad"))


class PredictiveScenarioTests(unittest.TestCase):
    def test_six_healthy_controls_differ_only_in_prediction_and_description(self):
        scenes = healthy_scenarios(driver.SCENARIOS, driver.L2_CFG_KEYS)
        self.assertEqual(len(scenes), 6)
        for workload in ("chain", "eec", "moe2"):
            off, on = (scenes[f"p9_{workload}_ok_{value}"] for value in ("off", "on"))
            changed = {key for key in off if off[key] != on[key]}
            self.assertEqual(changed, {"wd_late", "desc"})
            self.assertEqual(off["risk_policy"], 0x12)
            for scene in (off, on):
                cfg = driver.scenario_test_cfg(scene)
                self.assertEqual(len(driver.test_cfg_bytes(cfg)), 384)
                self.assertEqual(cfg["risk_policy"], 0x12)
                self.assertEqual(cfg["risk_late"], 0)
                self.assertEqual(cfg["risk_epoch"], 0)

    def test_eight_g4_scenes_share_image_and_fault_fields_except_gid(self):
        scenes = evaluation_scenarios(driver.SCENARIOS, driver.L2_CFG_KEYS,
            a=2, g=6, pre_stall_cycles=7200, static_late=8000)
        self.assertEqual(len(scenes), 8)
        base = driver.scenario_test_cfg(scenes["p9_d_static"])
        for scene in scenes.values():
            cfg = driver.scenario_test_cfg(scene)
            driver.check_same_ps_fault_cfg(base, cfg)
            self.assertEqual(cfg["fault_pre_stall_cycles"], 7200)
            self.assertEqual(cfg["fault_pre_stall_from"], 2)
            self.assertEqual((cfg["fault_core"], cfg["fault_cluster"]), (1, 0))
            self.assertEqual(cfg["risk_late"], 8000)
            self.assertEqual(cfg["risk_policy"], scene["risk_policy"])
            self.assertEqual(cfg["risk_epoch"], 0)
            self.assertEqual(driver.t1_image_flags(scene), "-DBINGO_WD_LATE -DBINGO_WD_ADAPT")
        self.assertEqual(scenes["p9_d_p9_derate"]["risk_policy"], 0xc32)
        self.assertEqual(scenes["p9_d_p9_boost"]["boost_policy"], 0x10101)
        boost = driver.scenario_test_cfg(scenes["p9_d_p9_boost"])
        self.assertEqual((boost["pm_boost_power_level"], boost["boost_policy"]), (3, 0x10101))
        self.assertFalse(scenes["p9_d_p9_noad"]["wd_adapt"] & 1)
        self.assertEqual(scenes["p9_s_p9"]["fault_gid"], 0xffffffff)

    def test_evaluation_parameter_contract_rejects_invalid_values(self):
        for changes in (dict(a=0), dict(g=4), dict(pre_stall_cycles=0),
                        dict(sh_l=0), dict(sh_l=3), dict(floor=1001), dict(floor=-1)):
            values = dict(a=2, g=6, pre_stall_cycles=7200, static_late=8000)
            values.update(changes)
            with self.assertRaises(ValueError):
                evaluation_scenarios(driver.SCENARIOS, driver.L2_CFG_KEYS, **values)
        self.assertEqual(late_control(), 0x3E8003)


if __name__ == "__main__":
    unittest.main()
