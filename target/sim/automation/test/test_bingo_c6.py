"""C6 hardware vs software CERF degradation scenarios (DESIGN 9.24.8)."""
import copy
import importlib.util
from pathlib import Path
import struct
import unittest

import bingo_c6
from test_bingo_early_exit_conf import fixture as eec_fixture

spec = importlib.util.spec_from_file_location(
    "watchdog_c6", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)

# Word index of cerf_fb_mode: the 23 named fields, user[4], evlog, recovery_hold, pm_access_level
MODE_WORD = len(watchdog.TEST_CFG_FIELDS) + 4 + 1 + 2
TYPE_BIT = 1 << watchdog.C6_FB_TYPE

LOG = """[BINGO_DISPATCH] 100 chip=0 task=2 core=1 cluster=0
[BINGO_DISPATCH] 110 chip=0 task=3 core=1 cluster=0
[BINGO_DISPATCH] 120 chip=0 task=4 core=0 cluster=0
[BINGO_REPLAY_STUCK] 500 chip=0 core=0 cluster=0: no live core may run task 4 (logical core 0)
[BINGO_DISPATCH] 900 chip=0 task=6 core=1 cluster=1
[BINGO_DISPATCH] 910 chip=0 task=7 core=1 cluster=1
[BINGO_DISPATCH] 920 chip=0 task=8 core=0 cluster=1
[BINGO_DISPATCH] 930 chip=0 task=9 core=1 cluster=1
[BINGO_DISPATCH] 990 chip=0 task=10 core=2 cluster=0
"""


def uart(cerf, en, evt, mode=1, poll=0, writes=1, sw_evt=TYPE_BIT):
    return (f"[Host] Bingo status: replay_stuck=1 cerf=0x{cerf:x} cerf_fb_en=0x{en:x} cerf_fb_evt=0x{evt:x}\n"
            f"[Host] C6 cerf_fb_mode={mode} poll_cycles={poll} sw_writes={writes} sw_evt=0x{sw_evt:x}\n"
            "[Host] Check [expert_1_D]: PASS\n"
            "[MoE2] router selected expert 0; join complete; output expert 1\n")


def scenes():
    return watchdog.c6_scenarios()


class ScenarioTests(unittest.TestCase):
    def test_matrix(self):
        names = [f"c6_{w}_{v}{ok}" for w in ("moe2", "eec") for v in ("hw", "sw0", "sw10000")
                 for ok in ("", "_ok")] + ["c6_moe2_none", "c6_eec_none"]
        self.assertEqual(sorted(scenes()), sorted(names))
        self.assertEqual({watchdog.t1_image_flags(s) for s in scenes().values()}, {"-DBINGO_C6_SW_CERF"})
        for name, scene in scenes().items():
            cfg = watchdog.scenario_test_cfg(scene)
            self.assertEqual(cfg["cerf_fb_mode"], watchdog.C6_MODES[scene["c6_mode"]], name)
            self.assertEqual(cfg["cerf_poll_cycles"], 10000 if "sw10000" in name else 0, name)
            # Both tables are compiled (bingo_add_cerf_fallback), never T1
            self.assertEqual(cfg["cerf_fb_enable"], 0, name)
        for workload in ("moe2", "eec"):
            none = scenes()[f"c6_{workload}_none"]
            self.assertFalse(none["expect_eoc"])
            self.assertEqual(none["sim_timeout_s"], 900)
            self.assertNotIn("cerf_fallback", none)
        self.assertTrue(scenes()["c6_moe2_hw"]["cerf_fallback"])
        self.assertTrue(all(scenes()[f"c6_eec_{v}"]["early_exit_conf"] for v in ("hw", "sw0", "none")))

    def test_one_image_per_workload(self):
        """Within a workload the T1 words differ only in mode, poll and (controls) the GID."""
        for workload in ("moe2", "eec"):
            cfgs = {n: watchdog.scenario_test_cfg(s) for n, s in scenes().items() if s["c6_workload"] == workload}
            reference = cfgs[f"c6_{workload}_hw"]
            for name, cfg in cfgs.items():
                allowed = {"cerf_fb_mode", "cerf_poll_cycles"} | ({"fault_gid"} if name.endswith("_ok") else set())
                self.assertLessEqual({k for k in cfg if cfg[k] != reference[k]}, allowed, name)
            for name, scene in scenes().items():
                if name.endswith("_ok"):
                    self.assertEqual(scene["same_as"], name[:-3])

    def test_templates_are_the_c4_pairs(self):
        for name, scene in scenes().items():
            template = watchdog.SCENARIOS[f"c4_{scene['c6_workload']}_{'ok' if name.endswith('_ok') else 'fault'}"]
            for key in ("cfg", "extra_cfg", "workload", "fault_stall_cycles", "victim", "extra_flags", "evlog_enable"):
                self.assertEqual(scene.get(key), template.get(key), (name, key))

    def test_image_flags_must_match_the_pair(self):
        scene = dict(scenes()["c6_moe2_hw_ok"], same_as="c4_moe2_fault")
        with self.assertRaises(ValueError):
            watchdog.t1_image_flags(scene)

    def test_t1_words(self):
        cfg = watchdog.scenario_test_cfg(scenes()["c6_moe2_sw10000"])
        words = struct.unpack("<64I", watchdog.test_cfg_bytes(cfg))
        self.assertEqual(MODE_WORD * 4, 120)
        self.assertEqual(words[MODE_WORD:MODE_WORD + 2], (1, 10000))
        with self.assertRaises(ValueError):
            watchdog.test_cfg_bytes(dict(cfg, cerf_fb_mode=3))

    def test_default_bytes_unchanged(self):
        # Scenes without the C6 words keep the former reserved words at zero.
        cfg = watchdog.scenario_test_cfg(watchdog.SCENARIOS["c4_moe2_fault"])
        self.assertNotIn("cerf_fb_mode", cfg)
        words = struct.unpack("<64I", watchdog.test_cfg_bytes(cfg))
        self.assertEqual(words[MODE_WORD:MODE_WORD + 2], (0, 0))


class Moe2CheckerTests(unittest.TestCase):
    def check(self, log=LOG, text=None, name="c6_moe2_sw0"):
        text = uart(0b10, 0, 0) if text is None else text
        return watchdog.evaluate_cerf_fallback(scenes()[name], log, text) + \
            watchdog.evaluate_c6(scenes()[name], log, text)

    def test_software_degradation_accepted(self):
        self.assertEqual(self.check(), [])

    def test_hardware_degradation_accepted(self):
        log = LOG.replace("[BINGO_DISPATCH] 900", f"[BINGO_CERF_FB] 800 type {watchdog.MOE2_CORE_TYPE} "
                          "clear g0 set g1\n[BINGO_DISPATCH] 900")
        self.assertEqual(self.check(log, uart(0b10, TYPE_BIT, TYPE_BIT, mode=0, writes=0, sw_evt=0),
                                    "c6_moe2_hw"), [])

    def test_rejected(self):
        hw_event = LOG + f"[BINGO_CERF_FB] 800 type {watchdog.MOE2_CORE_TYPE} clear g0 set g1\n"
        cases = {
            "hardware event": (hw_event, uart(0b10, 0, 0)),
            "late primary": (LOG.replace("120 chip=0 task=4", "950 chip=0 task=4"), uart(0b10, 0, 0)),
            "table left enabled": (LOG, uart(0b10, TYPE_BIT, 0)),
            "backup before stuck": (LOG.replace("REPLAY_STUCK] 500", "REPLAY_STUCK] 905"), uart(0b10, 0, 0)),
            "no stuck": ("".join(l + "\n" for l in LOG.splitlines() if "STUCK" not in l), uart(0b10, 0, 0)),
            "two writes": (LOG, uart(0b10, 0, 0, writes=2)),
            "no write": (LOG, uart(0b10, 0, 0, writes=0, sw_evt=0)),
            "wrong poll": (LOG, uart(0b10, 0, 0, poll=10000)),
            "wrong mode": (LOG, uart(0b10, 0, 0, mode=0)),
            "no C6 line": (LOG, "".join(l + "\n" for l in uart(0b10, 0, 0).splitlines() if "C6" not in l)),
        }
        for label, (log, text) in cases.items():
            with self.subTest(label):
                self.assertTrue(self.check(log, text))

    def test_healthy_controls(self):
        for name, mode, poll in (("c6_moe2_hw_ok", 0, 0), ("c6_moe2_sw10000_ok", 1, 10000)):
            en = TYPE_BIT if mode == 0 else 0
            good = f"[Host] Bingo status: replay_stuck=0 cerf=0x1 cerf_fb_en=0x{en:x} cerf_fb_evt=0x0\n" \
                   f"[Host] C6 cerf_fb_mode={mode} poll_cycles={poll} sw_writes=0 sw_evt=0x0\n"
            with self.subTest(name):
                self.assertEqual(watchdog.evaluate_c6(scenes()[name], "", good), [])
                self.assertTrue(watchdog.evaluate_c6(scenes()[name], "", good.replace("sw_writes=0", "sw_writes=1")))
                self.assertTrue(watchdog.evaluate_c6(scenes()[name], "", good.replace(
                    f"cerf_fb_en=0x{en:x}", f"cerf_fb_en=0x{en ^ TYPE_BIT:x}")))


class EarlyExitConfCheckerTests(unittest.TestCase):
    def software(self):
        """The t47 deep fault, degraded by the host: no hardware event, table off."""
        sc, log, text = eec_fixture("t47")
        scene = copy.deepcopy(scenes()["c6_eec_sw0"])
        scene.update(early_exit_task_ids=sc["early_exit_task_ids"],
                     early_exit_conf_data=sc["early_exit_conf_data"], t1_user=sc["t1_user"])
        log = "".join(l + "\n" for l in log.splitlines() if "[BINGO_CERF_FB]" not in l)
        text = text.replace("cerf_fb_en=0x2 cerf_fb_evt=0x2", "cerf_fb_en=0x0 cerf_fb_evt=0x0")
        return scene, log, text

    def test_software_degradation_accepted(self):
        self.assertEqual(watchdog.evaluate_early_exit_conf(*self.software()), [])

    def test_software_rejected(self):
        scene, log, text = self.software()
        for label, (bad_log, bad_text) in {
            "hardware event": (log + "[BINGO_CERF_FB] 700 type 1 clear g0 set g1\n", text),
            "table enabled": (log, text.replace("cerf_fb_en=0x0", "cerf_fb_en=0x2")),
            "shallow copy before stuck": (log.replace("REPLAY_STUCK] 650", "REPLAY_STUCK] 850"), text),
        }.items():
            with self.subTest(label):
                self.assertTrue(watchdog.evaluate_early_exit_conf(scene, bad_log, bad_text))

    def test_hardware_unchanged(self):
        sc, log, text = eec_fixture("t47")
        scene = dict(scenes()["c6_eec_hw"], early_exit_task_ids=sc["early_exit_task_ids"],
                     early_exit_conf_data=sc["early_exit_conf_data"], t1_user=sc["t1_user"])
        self.assertEqual(watchdog.evaluate_early_exit_conf(scene, log, text), [])


def recovered_log(cerf_fb):
    return LOG + ("[BINGO_CERF_FB] 800 type 1 clear g0 set g1\n" if cerf_fb else "") + \
        "[BINGO_WD] 300 chip=0 core=0 cluster=0 dead_suspect=1 fenced=1\n" \
        "[BINGO_DISPATCH] 50 chip=0 task=4 core=0 cluster=0\n" \
        "All chips finished successfully at 2000\n" \
        "Simulation of chip_0_0 finished with status 0\n"


class ClassificationTests(unittest.TestCase):
    def classify(self, log, text):
        return bingo_c6.classify_c6(log, text, fault_gid=4, victim=(0, 0, 0))

    def test_host_write_is_the_cerf_signal(self):
        result = self.classify(recovered_log(False), uart(0b10, 0, 0))
        self.assertEqual((result["class"], result["cerf_source"]), ("degraded", "host"))
        self.assertIn("after_host_cerf", result["annot"])

    def test_without_a_reported_write_it_stays_unclassified(self):
        text = uart(0b10, 0, 0, writes=0, sw_evt=0)
        result = self.classify(recovered_log(False), text)
        self.assertEqual((result["class"], result["unclassified_reason"]), ("unclassified", "U5"))

    def test_manager_cerf_unchanged(self):
        text = uart(0b10, TYPE_BIT, TYPE_BIT, mode=0, writes=0, sw_evt=0)
        result = self.classify(recovered_log(True), text)
        self.assertEqual((result["class"], result["cerf_source"]), ("degraded", "manager"))

    def test_other_unclassified_reasons_are_not_rescued(self):
        log = recovered_log(False) + "[BINGO_ASSERT] 10 something\n"
        result = self.classify(log, uart(0b10, 0, 0))
        self.assertEqual(result["class"], "unclassified")
        self.assertIn("U2", result["unclassified_reason"])


class MetricsTests(unittest.TestCase):
    def row(self, mode, fault, poll=0, log=LOG, text=None, image="i"):
        text = uart(0b10, 0, 0) if text is None else text
        metrics = bingo_c6.run_metrics(log, text, {6, 7, 8, 9})
        return dict(metrics, scenario=f"{mode}{poll}{fault}", mode=mode, poll=poll, fault=fault,
                    image_id=image, cls="degraded" if fault else "recovered")

    def test_run_metrics(self):
        m = bingo_c6.run_metrics(LOG, uart(0b10, 0, 0), {6, 7, 8, 9})
        self.assertEqual((m["t_stuck_ps"], m["t_backup_ps"], m["stuck_to_backup_ps"]), (500, 900, 400))
        self.assertEqual((m["t_cerf_fb_ps"], m["host_writes"], m["n_backup"]), (None, 1, 4))
        self.assertEqual(m["slots"]["1,1"], [6, 7, 9])

    def test_group_gates(self):
        rows = [self.row("hw", True), self.row("sw", True), self.row("sw", True, 10000)]
        self.assertEqual(bingo_c6.group_problems(rows), [])
        moved = LOG.replace("task=7 core=1 cluster=1", "task=7 core=0 cluster=1")
        self.assertTrue(bingo_c6.group_problems(rows + [self.row("sw", True, 5, log=moved)]))
        self.assertTrue(bingo_c6.group_problems(rows + [self.row("hw", False, image="j")]))
        stuck_only = "[BINGO_REPLAY_STUCK] 500 chip=0 core=0 cluster=0: no live core\n"
        none = self.row("none", True, log=stuck_only, text="")
        self.assertEqual(bingo_c6.group_problems(rows + [none]), [])
        self.assertTrue(bingo_c6.group_problems(rows + [self.row("none", True)]))

    def test_summary(self):
        rows = [self.row("hw", True), self.row("sw", True, 10000)]
        for row, evlog in zip(rows, (1000, 1300)):
            row["T_evlog_ps"] = evlog
        rows[1]["stuck_to_backup_ps"] = 700
        control = self.row("sw", False, 10000)
        control["T_evlog_ps"] = 900
        result = {r["variant"]: r for r in bingo_c6.summary(rows + [control])}
        self.assertEqual(result["sw10000"]["fault_cost_ps"], 400)
        self.assertEqual(result["sw10000"]["T_evlog_vs_hw_ps"], 300)
        self.assertEqual(result["sw10000"]["stuck_to_backup_vs_hw_ps"], 300)
        self.assertIsNone(result["hw"]["fault_cost_ps"])


if __name__ == "__main__":
    unittest.main()
