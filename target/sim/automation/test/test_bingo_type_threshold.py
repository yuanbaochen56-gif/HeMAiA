"""A5 configuration, physical busy-segment pairing and MMIO API checks."""
import importlib.util
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "watchdog_type_threshold", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
ROOT = Path(__file__).resolve().parents[4]


class TypeThresholdTests(unittest.TestCase):
    def test_scenarios_only_shorten_selected_type_without_other_controls(self):
        for name in watchdog.A5_FAMILY:
            sc = watchdog.SCENARIOS[name]
            cfg = watchdog.scenario_test_cfg(sc)
            self.assertTrue(sc["dispatch_log"])
            self.assertEqual(cfg["version"], 2)
            for field in ("fault_pre_stall_cycles", "risk_late", "risk_policy",
                          "risk_epoch", "risk_confirm", "cerf_fb_enable"):
                self.assertEqual(cfg[field], 0)
            for typ in range(16):
                selected = (name in ("t50", "t51") and typ == 2) or (name == "t52" and typ == 1)
                self.assertEqual((cfg["wd_type_h"][typ], cfg["wd_type_c"][typ]),
                                 (20000, 40000) if selected else (0, 0))
            if name in ("tch3", "t50", "t52"):
                self.assertEqual((cfg["fault_gid"], cfg["fault_stall_cycles"]), (3, 0))

    def test_v1_cannot_contain_type_arrays_and_v2_values_are_full_uint32(self):
        cfg = watchdog.test_cfg_defaults(1)
        with self.assertRaises(ValueError):
            watchdog.test_cfg_bytes(dict(cfg, wd_type_h=[0] * 16))
        cfg = watchdog.test_cfg_defaults()
        for bad in ([0] * 15, [0] * 15 + [-1], [0] * 15 + [1 << 32]):
            with self.assertRaises(ValueError):
                watchdog.test_cfg_bytes(dict(cfg, wd_type_h=bad))
        cfg["wd_type_h"][15] = 0xFFFFFFFF
        self.assertEqual(len(watchdog.test_cfg_bytes(cfg)), 256)

    def test_dispatch_done_pairs_and_negative_cases(self):
        dispatch = "[BINGO_DISPATCH] 10 chip=0 task=3 core=1 cluster=0\n"
        done = "[BINGO_DONE] 20 chip=0 task=3 core=1 cluster=0\n"
        self.assertEqual(watchdog.dispatch_done_pairs(dispatch + done),
                         [dict(chip=0, core=1, cluster=0, task=3, dispatch_ps=10, done_ps=20)])
        for text in (dispatch, done, dispatch + dispatch, dispatch + done + done,
                     dispatch + done.replace("task=3", "task=4"),
                     dispatch + done.replace("cluster=0", "cluster=1"),
                     dispatch + done.replace("20", "9")):
            with self.assertRaises(ValueError):
                watchdog.dispatch_done_pairs(text)
        self.assertEqual(watchdog.dispatch_done_pairs(dispatch, [(0, 1, 0, 3)]), [])
        with self.assertRaises(ValueError):
            watchdog.dispatch_done_pairs(dispatch, [(0, 1, 0, 4)])

    def test_type_cause_is_required_only_for_actual_type_two_fence(self):
        text = ("[BINGO_TYPE_CONFIRM] 99 core=1 cluster=0 type=2 threshold=40000\n"
                "[BINGO_WD] 100 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1\n")
        self.assertEqual(watchdog.evaluate_type_threshold(watchdog.SCENARIOS["t50"], text), [])
        for wrong in ("", text.replace("type=2", "type=1"), text.replace("40000", "200000"),
                      text + text, text.replace("99", "101"),
                      text + "[BINGO_RISK_CONFIRM]\n"):
            self.assertTrue(watchdog.evaluate_type_threshold(watchdog.SCENARIOS["t50"], wrong))
        for name in ("tch0", "tch3", "t51", "t52"):
            self.assertTrue(watchdog.evaluate_type_threshold(watchdog.SCENARIOS[name], text))
            self.assertEqual(watchdog.evaluate_type_threshold(watchdog.SCENARIOS[name], ""), [])

    def host_pairing(self, text, *, kernel="__host_bingo_kernel_exit", checker=True):
        with tempfile.TemporaryDirectory() as directory:
            graph = Path(directory) / "final_dfg.csv"
            # Deliberately not task 14: the checker must consult this graph.
            graph.write_text("ID,Chiplet,Cluster,Core,Type,Kernel\n"
                             f"93,00,0,2,normal,{kernel}\n")
            return watchdog.dispatch_done_pairs(
                text, graph_csv=graph, host_slot=(0, 2, 0), checker_passed=checker)

    def test_terminal_host_exit_without_done_is_allowed_from_graph(self):
        text = ("[BINGO_DISPATCH] 10 chip=0 task=7 core=2 cluster=0\n"
                "[BINGO_DONE] 20 chip=0 task=7 core=2 cluster=0\n"
                "[BINGO_DISPATCH] 30 chip=0 task=93 core=2 cluster=0\n"
                "All chips finished successfully at 100\n")
        self.assertEqual(len(self.host_pairing(text)), 1)

    def test_unpaired_device_dispatch_is_not_host_exit(self):
        text = ("[BINGO_DISPATCH] 30 chip=0 task=93 core=1 cluster=0\n"
                "All chips finished successfully at 100\n")
        with self.assertRaises(ValueError):
            self.host_pairing(text)

    def test_unpaired_host_non_exit_is_rejected(self):
        text = ("[BINGO_DISPATCH] 30 chip=0 task=93 core=2 cluster=0\n"
                "All chips finished successfully at 100\n")
        for kernel in ("__host_bingo_kernel_check_result", ""):
            with self.assertRaises(ValueError):
                self.host_pairing(text, kernel=kernel)
        with self.assertRaises(ValueError):
            self.host_pairing(text.replace("task=93", "task=14"))

    def test_two_unpaired_host_dispatches_are_rejected(self):
        text = ("[BINGO_DISPATCH] 30 chip=0 task=7 core=2 cluster=0\n"
                "[BINGO_DISPATCH] 40 chip=0 task=93 core=2 cluster=0\n"
                "All chips finished successfully at 100\n")
        with self.assertRaises(ValueError):
            self.host_pairing(text)

    def test_unpaired_host_exit_not_last_is_rejected(self):
        text = ("[BINGO_DISPATCH] 30 chip=0 task=93 core=2 cluster=0\n"
                "[BINGO_DISPATCH] 40 chip=0 task=7 core=2 cluster=0\n"
                "[BINGO_DONE] 50 chip=0 task=7 core=2 cluster=0\n"
                "All chips finished successfully at 100\n")
        with self.assertRaises(ValueError):
            self.host_pairing(text)

    def test_host_exit_requires_successful_eoc_and_checker(self):
        dispatch = "[BINGO_DISPATCH] 30 chip=0 task=93 core=2 cluster=0\n"
        for suffix in ("", "All chips finished successfully at 20\n"):
            with self.assertRaises(ValueError):
                self.host_pairing(dispatch + suffix)
        with self.assertRaises(ValueError):
            self.host_pairing(dispatch + "All chips finished successfully at 100\n", checker=False)

    def test_native_api_writes_full_values_to_selected_type(self):
        api = (ROOT / "target/sw/host/runtime/libbingo/src/bingo_api.c").read_text()
        body = re.search(r"void bingo_wd_type_set\([^)]*\) \{[\s\S]*?\n\}", api).group(0)
        body = body.replace('asm volatile("fence" ::: "memory");', "")
        source = """
#include <stdint.h>
#include <assert.h>
#define BINGO_WD_NUM_TYPES 16
static unsigned n;
static uint32_t values[4];
static uintptr_t addresses[4];
static uint64_t chiplet_addr_transform(uint64_t address) { return address + 0x10000000; }
static uintptr_t quad_ctrl_bingo_wd_type_h_addr(uint32_t t) { return 0x104 + 4*t; }
static uintptr_t quad_ctrl_bingo_wd_type_c_addr(uint32_t t) { return 0x144 + 4*t; }
static void writew(uint32_t value, uintptr_t address) { values[n]=value; addresses[n++]=address; }
"""
        source += body + """
int main(void) {
    bingo_wd_type_set(2, 20000, 40000);
    bingo_wd_type_set(15, UINT32_MAX, UINT32_MAX-1);
    bingo_wd_type_set(16, 1, 2);
    assert(n==4 && values[0]==20000 && values[1]==40000);
    assert(values[2]==UINT32_MAX && values[3]==UINT32_MAX-1);
    assert(addresses[0]==0x1000010c && addresses[1]==0x1000014c);
    assert(addresses[2]==0x10000140 && addresses[3]==0x10000180);
    return 0;
}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "api.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(path / "api.c"), "-o", str(path / "api")], check=True)
            subprocess.run([str(path / "api")], check=True)
        init = api[api.index("#if BINGO_TEST_CFG", api.index("void bingo_hw_scheduler_init(")):]
        self.assertIn("type < BINGO_WD_NUM_TYPES", init.split("#else")[0])
        self.assertNotIn("bingo_wd_type_set(", init.split("#else")[1].split("#endif")[0])


if __name__ == "__main__":
    unittest.main()
