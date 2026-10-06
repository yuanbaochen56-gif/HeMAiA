"""Fast checks for the P6a DMA-chain scenarios."""

import importlib.util
from pathlib import Path
import unittest

_path = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
_spec = importlib.util.spec_from_file_location("bingo_risk_driver", _path)
driver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(driver)


def fixture(parked):
    tasks = [(i, 1, 0) for i in range(2 if parked else 4)]
    tasks += [(i, 1, 1) for i in range(2 if parked else 3, 6)]
    log = "\n".join(
        f"[BINGO_DISPATCH] {10 * (n + 1)} chip=0 task={task} core={core} cluster={cl}"
        for n, (task, core, cl) in enumerate(tasks))
    if parked:
        log += "\n[BINGO_RISK] 25 core=1 cluster=0 at risk"
    uart = (f"[Host] Bingo status: replay_stuck=0 park_fail=0x0 risk=0x{2 if parked else 0:x}\n"
            "Check [A_chain_cluster0]: PASS\nCheck [A_cluster1]: PASS\n")
    return log, uart


def full_fixture(parked):
    log, uart = fixture(parked)
    log += "\nAll chips finished successfully at 1000"
    log += "\n[BINGO_LATE] 15 core=1 cluster=0\n[BINGO_LATE] 25 core=1 cluster=0"
    if parked:
        log += ("\n[BINGO_PARK] 26 chip=0 core=1 cluster=0 HOLD"
                "\n[BINGO_PARK] 27 chip=0 core=1 cluster=0 PARKED -> core=1 cluster=1"
                "\n[BINGO_REMAP] 30 chip=0 task=2 logical_core=1 -> physical_core=1 cluster=1")
    else:
        log += ("\n[BINGO_LATE] 35 core=1 cluster=0"
                "\n[BINGO_WD] 41 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0"
                "\n[BINGO_WD] 42 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1"
                "\n[BINGO_REPLAY] 43 chip=0 task=3 type=2 logical_core=1 from=1 to=1 cluster=0 to_cluster=1"
                "\n[BINGO_RETIRED] 44 chip=0 core=1 cluster=0")
    # The victim's exit (task 11) is routed away and retired by the manager
    log += "\n[BINGO_EXIT_ABSORB] 60 chip=0 task=11 logical_core=1 logical_cluster=0 core=1 cluster=1"
    return log, uart


def control_fixture(name):
    sc = driver.SCENARIOS[name]
    times = [10, 30, 50, 90, 110, 130]
    lines = [f"[BINGO_DISPATCH] {t} chip=0 task={task} core=1 cluster={cl}"
             for task, (t, cl) in enumerate(zip(times, sc["expect_chain_clusters"]))]
    late = [20, 40]
    if name == "s21":
        late += [100, 120, 140, 170]
    elif name in ("s20", "s22"):
        late += [60, 100, 120, 140, 170]
    lines += [f"[BINGO_LATE] {t} core=1 cluster=0" for t in late]
    if sc.get("expect_risk"):
        lines.append("[BINGO_RISK] 41 core=1 cluster=0 at risk")
        lines.append("[BINGO_PARK] 42 chip=0 core=1 cluster=0 HOLD")
        if name == "s22":
            lines += ["[BINGO_PARK] 43 chip=0 core=1 cluster=0 FAIL",
                      "[BINGO_PM] 44 domain=1 level=10",
                      "[BINGO_PM] 160 domain=1 level=25"]
        else:
            lines.append("[BINGO_PARK] 43 chip=0 core=1 cluster=0 PARKED -> core=1 cluster=1")
            lines += [f"[BINGO_REMAP] {times[task]} chip=0 task={task} logical_core=1"
                      " -> physical_core=1 cluster=1"
                      for task, cl in enumerate(sc["expect_chain_clusters"]) if cl == 1]
    if name == "s20":
        lines += [f"[BINGO_RISK_DECAY] {t + 5} core=1 cluster=0 count=1 -> count=0"
                  for t in late]
    if name == "s21":
        lines += ["[BINGO_RISK_CLEAR] 60 mask=0x2",
                  "[BINGO_PARK] 61 chip=0 core=1 cluster=0 UNPARK (0 tasks left elsewhere)",
                  "[BINGO_PARK] 62 chip=0 core=1 cluster=0 UNPARKED"]
    if name == "s23":
        # The parked victim's exit (task 11) is routed away and retired by the manager
        lines.append("[BINGO_EXIT_ABSORB] 150 chip=0 task=11 logical_core=1 logical_cluster=0 core=1 cluster=1")
    lines.append("All chips finished successfully at 1000")
    risk = sc.get("expect_final_risk", 2 if sc.get("expect_risk") else 0)
    uart = (f"[Host] Bingo status: replay_stuck=0 remote_done_mismatch=0 link_error=0 "
            f"fenced=0x0 dead_suspect=0x0 remote_timeout=0 park_fail=0x{2 if name == 's22' else 0:x} "
            f"risk=0x{risk:x}\nCheck [A_chain_cluster0]: PASS\nCheck [A_cluster1]: PASS\n")
    if name == "s21":
        uart += "[RiskChain] CLEAR before=0x2 mask=0x2 held=0x2 after=0x0\n"
    return "\n".join(lines), uart


class RiskChainTests(unittest.TestCase):
    def check(self, parked, log=None, uart=None):
        original_log, original_uart = fixture(parked)
        return driver.evaluate_risk_chain(
            driver.SCENARIOS["s19" if parked else "s18"],
            original_log if log is None else log,
            original_uart if uart is None else uart)

    def test_baseline_and_parking(self):
        for parked in (False, True):
            with self.subTest(parked=parked):
                self.assertEqual(self.check(parked), [])

    def test_host_risk_is_required_and_exact(self):
        for parked in (False, True):
            log, uart = fixture(parked)
            for changed in (uart.replace("risk=", "other="),
                            uart.replace(f"risk=0x{2 if parked else 0:x}", "risk=0x4")):
                with self.subTest(parked=parked, uart=changed):
                    self.assertTrue(self.check(parked, uart=changed))

    def test_missing_duplicate_wrong_slot_or_chip(self):
        for parked in (False, True):
            log, _ = fixture(parked)
            for changed in (log.replace("task=2", "task=99"),
                            log + "\n[BINGO_DISPATCH] 100 chip=0 task=5 core=1 cluster=1",
                            log.replace("task=5 core=1 cluster=1", "task=5 core=1 cluster=0"),
                            log.replace("chip=0 task=0", "chip=1 task=0")):
                with self.subTest(parked=parked, log=changed):
                    self.assertTrue(self.check(parked, log=changed))

    def test_risk_must_trip_during_second_task(self):
        log, _ = fixture(True)
        for changed in (log.replace("RISK] 25", "RISK] 15"),
                        log.replace("RISK] 25", "RISK] 35"),
                        log.replace("[BINGO_RISK]", "[OTHER]")):
            with self.subTest(log=changed):
                self.assertTrue(self.check(True, log=changed))

    def test_both_goldens_are_required(self):
        for parked in (False, True):
            _, uart = fixture(parked)
            for changed in (uart.replace("Check [A_chain_cluster0]: PASS\n", ""),
                            uart.replace("Check [A_cluster1]: PASS\n", ""),
                            uart.replace(": PASS", ": FAIL"),
                            uart + "Check [A_cluster1]: PASS\n"):
                with self.subTest(parked=parked, uart=changed):
                    self.assertTrue(self.check(parked, uart=changed))

    def test_full_scenario_expectations(self):
        for parked in (False, True):
            name = "s19" if parked else "s18"
            log, uart = full_fixture(parked)
            with self.subTest(name=name):
                self.assertEqual(driver.evaluate(name, driver.SCENARIOS[name], log, uart), [])
                self.assertTrue(driver.evaluate(
                    name, driver.SCENARIOS[name], log.replace("LATE] 15", "OTHER] 15"), uart))
                self.assertTrue(driver.evaluate(
                    name, driver.SCENARIOS[name], log.replace("LATE] 15 core=1", "LATE] 15 core=0"), uart))
        log, uart = full_fixture(True)
        self.assertTrue(driver.evaluate(
            "s19", driver.SCENARIOS["s19"],
            log + "\n[BINGO_WD] 90 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1", uart))

    def test_control_scenarios(self):
        for name in ("s20", "s21", "s22", "s23"):
            log, uart = control_fixture(name)
            with self.subTest(name=name):
                self.assertEqual(driver.evaluate(name, driver.SCENARIOS[name], log, uart), [])

    def test_decay_requires_halving_between_late_beats(self):
        log, uart = control_fixture("s20")
        for changed in (log.replace("[BINGO_RISK_DECAY]", "[OTHER]"),
                        log.replace("[BINGO_RISK_DECAY] 45", "[OTHER] 45"),
                        log.replace("count=1 -> count=0", "count=2 -> count=1"),
                        log.replace("25 core=1 cluster=0", "25 core=0 cluster=0"),
                        log + "\n[BINGO_RISK] 41 core=1 cluster=0 at risk"):
            with self.subTest(log=changed):
                self.assertTrue(driver.evaluate("s20", driver.SCENARIOS["s20"], changed, uart))

    def test_clear_requires_readback_and_unpark_before_home_dispatch(self):
        log, uart = control_fixture("s21")
        for changed in (uart.replace("before=0x2", "before=0x0"),
                        uart.replace("mask=0x2", "mask=0x4"),
                        uart.replace("held=0x2", "held=0x0"),
                        uart.replace("after=0x0", "after=0x2"),
                        uart.replace("[RiskChain]", "[OTHER]"),
                        uart.replace("risk=0x0", "risk=0x2")):
            with self.subTest(uart=changed):
                self.assertTrue(driver.evaluate("s21", driver.SCENARIOS["s21"], log, changed))
        for changed in (log.replace("CLEAR] 60 mask=0x2", "CLEAR] 60 mask=0x4"),
                        log.replace("CLEAR] 60", "CLEAR] 45"),
                        log.replace("PARK] 62", "PARK] 95"),
                        log.replace(" UNPARKED", " OTHER"),
                        log.replace("task=3 core=1 cluster=0", "task=3 core=1 cluster=1"),
                        log + "\n[BINGO_RISK] 120 core=1 cluster=0 at risk"):
            with self.subTest(log=changed):
                self.assertTrue(driver.evaluate("s21", driver.SCENARIOS["s21"], changed, uart))

    def test_derate_requires_park_failure_and_persistent_cap(self):
        log, uart = control_fixture("s22")
        for changed in (log.replace(" FAIL", " PARKED -> core=1 cluster=1"),
                        log.replace("PM] 44", "PM] 40"),
                        log.replace("level=10", "level=6"),
                        log + "\n[BINGO_PM] 100 domain=1 level=3",
                        log.replace("task=4 core=1 cluster=0", "task=4 core=1 cluster=1")):
            with self.subTest(log=changed):
                self.assertTrue(driver.evaluate("s22", driver.SCENARIOS["s22"], changed, uart))
        for changed in (uart.replace("park_fail=0x2", "park_fail=0x0"),
                        uart.replace("park_fail=0x2", "park_fail=0x4")):
            with self.subTest(uart=changed):
                self.assertTrue(driver.evaluate("s22", driver.SCENARIOS["s22"], log, changed))

    def test_control_scenarios_still_require_goldens_and_no_watchdog(self):
        for name in ("s20", "s21", "s22", "s23"):
            log, uart = control_fixture(name)
            with self.subTest(name=name):
                self.assertTrue(driver.evaluate(name, driver.SCENARIOS[name], log,
                                                uart.replace("Check [A_cluster1]: PASS", "")))
                self.assertTrue(driver.evaluate(name, driver.SCENARIOS[name],
                                                log + "\n[BINGO_WD] 150 chip=0 core=1 cluster=0 "
                                                "dead_suspect=1 fenced=0", uart))


class RiskWorkloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = driver._REPO_ROOT / (
            "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/"
            "dma_chain_2cluster/main_bingo.py")
        spec = importlib.util.spec_from_file_location("bingo_risk_workload", path)
        cls.workload = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.workload)
        cls.params = dict(num_clusters=2, chain_len=6, chunk_size=64, A_size=384)
        cls.platform = dict(num_chiplets=1, num_clusters_per_chiplet=2,
                            num_cores_per_cluster=2, chiplet_ids=[0])

    def graph(self, clear_after=None):
        return self.workload.create_dfg(
            self.params, self.workload.define_memory_handles(self.params),
            self.platform, clear_after)

    def test_default_chain_is_unchanged(self):
        graph = self.graph()
        self.assertEqual(len(graph.node_list), 9)
        self.assertTrue(all(graph.has_edge(graph.node_list[i], graph.node_list[i + 1])
                            for i in range(5)))
        self.assertFalse(any(n.kernel_name == "__host_bingo_kernel_risk_clear"
                             for n in graph.node_list))

    def test_clear_node_orders_remapped_and_home_copies(self):
        graph = self.graph(2)
        self.assertEqual(len(graph.node_list), 10)
        clear = graph.node_list[7]
        self.assertEqual(clear.kernel_name, "__host_bingo_kernel_risk_clear")
        self.assertEqual((clear.assigned_core_id, clear.assigned_cluster_id), (2, 0))
        self.assertEqual(clear.kernel_args.dummy_input, 2)
        graph.bingo_transform_add_core_sequencing_edges()
        self.assertTrue(graph.has_edge(graph.node_list[2], clear))
        self.assertTrue(graph.has_edge(clear, graph.node_list[3]))
        self.assertFalse(graph.has_edge(graph.node_list[2], graph.node_list[3]))

    def test_clear_after_requires_a_successor(self):
        for index in (-1, 5, 6):
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.graph(index)


if __name__ == "__main__":
    unittest.main()
