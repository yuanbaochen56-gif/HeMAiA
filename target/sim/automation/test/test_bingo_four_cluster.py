"""Four-cluster runs: the substitute rule over several victims, the fault slot,
the second compile-time fault in the T1 image flags, the PM domain of a victim."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

import bingo_four_cluster as fc
from bingo_log import pm_domain_re

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("driver4cl", HERE / "3_start_bingo_watchdog_sim.py")
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)

GRAPH = "ID,Chiplet,Cluster,Core,Type,Kernel\n" + "".join(
    f"{i},00,{c},{k},normal,k\n" for i, (c, k) in enumerate([(e, 1) for e in range(4)] + [(1, 1), (1, 0)]))
# tasks 0-3: DM core of clusters 0-3; task 4: DM of cluster 1; task 5: GEMM of cluster 1


def wd(t, core, cluster):
    return f"# [BINGO_WD] {t} chip=0 core={core} cluster={cluster} dead_suspect=1 fenced=1\n"


def replay(t, task, cl, to_cl):
    return f"# [BINGO_REPLAY] {t} chip=0 task={task} type=2 logical_core=1 from=1 to=1 cluster={cl} to_cluster={to_cl}\n"


def remap(t, task, cl):
    return f"# [BINGO_REMAP] {t} chip=0 task={task} logical_core=1 -> physical_core=1 cluster={cl}\n"


def retired(core, cluster):
    return f"# [BINGO_RETIRED] 1 chip=0 core={core} cluster={cluster}\n"


class FourClusterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.graph = Path(self.tmp.name) / "final_dfg.csv"
        self.graph.write_text(GRAPH)

    def tearDown(self):
        self.tmp.cleanup()

    def scene(self, victims, gid=1):
        return dict(takeover_victims=victims, fault_gid=gid)

    def test_substitute_rule(self):
        self.assertEqual(fc.substitute(1, 2, set()), 2)
        self.assertEqual(fc.substitute(1, 2, {(1, 2)}), 0)
        self.assertEqual(fc.substitute(1, 2, {(1, 2), (1, 0)}), 1)
        self.assertEqual(fc.substitute(1, 0, {(1, 0), (1, 1), (1, 2)}), 3)
        self.assertIsNone(fc.substitute(1, 3, {(1, c) for c in range(4)}))

    def test_one_victim_to_lowest_other_cluster(self):
        log = wd(100, 1, 1) + replay(110, 1, 1, 0) + replay(120, 4, 1, 0) + retired(1, 1) + remap(200, 4, 0)
        self.assertEqual(fc.takeover_chain_problems(self.scene([(0, 1, 1)]), log, self.graph), [])

    def test_wrong_cluster_is_reported(self):
        log = wd(100, 1, 1) + replay(110, 1, 1, 2) + retired(1, 1)
        problems = fc.takeover_chain_problems(self.scene([(0, 1, 1)]), log, self.graph)
        self.assertTrue(any("expected cluster 0" in p for p in problems), problems)

    def test_second_victim_moves_both_logical_slots_on(self):
        log = (wd(100, 1, 1) + replay(110, 1, 1, 0) + retired(1, 1) + wd(300, 1, 0) + replay(310, 4, 0, 2)
               + replay(311, 0, 0, 2) + retired(1, 0) + remap(400, 4, 2))
        self.assertEqual(fc.takeover_chain_problems(self.scene([(0, 1, 1), (0, 1, 0)]), log, self.graph), [])

    def test_fence_order_and_retired_list(self):
        log = wd(100, 1, 0) + wd(200, 1, 1) + replay(210, 1, 1, 2) + retired(1, 0) + retired(1, 1)
        problems = fc.takeover_chain_problems(self.scene([(0, 1, 1), (0, 1, 0)]), log, self.graph)
        self.assertTrue(any("in this order" in p for p in problems), problems)
        self.assertTrue(any("RETIRED" in p for p in problems), problems)

    def test_fault_task_must_be_replayed(self):
        log = wd(100, 1, 1) + retired(1, 1)
        problems = fc.takeover_chain_problems(self.scene([(0, 1, 1)]), log, self.graph)
        self.assertTrue(any("was not replayed" in p for p in problems), problems)

    def test_fault_slot(self):
        sc = dict(fault_stall_cycles=0, fault_gid=5, victim=(0, 0, 1))
        self.assertEqual(fc.fault_slot_problems(sc, self.graph), [])
        self.assertTrue(fc.fault_slot_problems(dict(sc, fault_gid=4), self.graph))
        self.assertEqual(fc.fault_slot_problems(dict(sc, fault_stall_cycles=None, fault_gid=4), self.graph), [])

    def test_second_fault_image_flags(self):
        flags = "-DBINGO_WD_FAULT2_GID=6 -DBINGO_WD_FAULT2_CLUSTER=0 -DBINGO_WD_FAULT2_CORE=1"
        self.assertEqual(driver.t1_image_flags(dict(t1=True, image_flags=flags)), flags)
        with self.assertRaises(ValueError):
            driver.t1_image_flags(dict(t1=True, image_flags="-DBINGO_WD_FAULT2_GID=6"))
        with self.assertRaises(ValueError):
            driver.t1_image_flags(dict(t1=True, image_flags="-DBINGO_WD_FAULT3_GID=6"))

    def test_pm_domain_of_a_cluster(self):
        log = "# [BINGO_PM] 5 domain=3 level=6\n# [BINGO_PM] 7 domain=1 level=25\n"
        self.assertEqual(pm_domain_re(3).findall(log), [("5", "6")])
        self.assertEqual(pm_domain_re(1).findall(log), [("7", "25")])

    def moe4_log(self, experts, fb=None):
        log = ""
        for e in experts:
            for t in fc.moe4_tasks(e):
                log += f"# [BINGO_DISPATCH] {1000 + t} chip=0 task={t} core={1 if t % 4 != 3 else 0} cluster={e}\n"
        if fb is not None:
            log += f"# [BINGO_CERF_FB] {fb} type 1 clear g1 set g0\n"
        return log

    def test_moe4_healthy(self):
        uart = "[Host] Check [expert_1_D]: PASS\n[Host] Check [expert_3_D]: PASS\n[Host] Check [softmax]: PASS\n"
        sc = dict(fault_stall_cycles=None)
        self.assertEqual(fc.evaluate_moe4_cerf(sc, self.moe4_log([1, 3]), uart), [])
        self.assertTrue(fc.evaluate_moe4_cerf(sc, self.moe4_log([0, 1, 3]), uart))
        self.assertTrue(fc.evaluate_moe4_cerf(sc, self.moe4_log([1, 3]), uart.replace("_3_D]: PASS", "_3_D]: FAIL")))

    def test_moe4_gemm_fallback(self):
        uart = "[Host] Check [expert_0_D]: PASS\n[Host] Check [expert_3_D]: PASS\n[Host] Check [softmax]: PASS\n"
        sc = dict(fault_stall_cycles=0)
        log = self.moe4_log([0, 3]) + "".join(
            f"# [BINGO_DISPATCH] {1000 + t} chip=0 task={t} core=1 cluster=1\n" for t in (5, 6))
        self.assertEqual(fc.evaluate_moe4_cerf(sc, log + "# [BINGO_CERF_FB] 900 type 1 clear g1 set g0\n", uart), [])
        self.assertTrue(fc.evaluate_moe4_cerf(sc, log, uart))   # no fallback event
        self.assertTrue(fc.evaluate_moe4_cerf(sc, log + "# [BINGO_CERF_FB] 2000 type 1 clear g1 set g0\n", uart))


if __name__ == "__main__":
    unittest.main()
