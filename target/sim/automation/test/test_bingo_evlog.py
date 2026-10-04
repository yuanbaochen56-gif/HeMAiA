import importlib.util
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest

from bingo_evlog import check_evlog, check_evlog_pair

spec = importlib.util.spec_from_file_location(
    "evlog_cfg_driver", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


class EventLogTests(unittest.TestCase):
    def test_native_mmio_api_reads_and_value_changes(self):
        root = Path(__file__).resolve().parents[4]
        api = (root / "target/sw/host/runtime/libbingo/src/bingo_api.c").read_text()
        start = api.index("void bingo_evlog_enable(")
        end = api.index("// The task will be initized", start)
        functions = api[start:end].replace('asm volatile("fence" ::: "memory");',
                                           '__asm__ __volatile__("" ::: "memory");')
        source = r'''
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>
#include <assert.h>
#define printf_safe printf
static uint32_t regs[8], index_q, reads;
static uint64_t items[3] = {0x0000006403010002ULL,
                            0x0000006504014003ULL, 0x000000660b010000ULL};
static uintptr_t chiplet_addr_transform(uintptr_t address) { return address; }
static uintptr_t quad_ctrl_bingo_evlog_ctrl_addr(void) { return 0; }
static uintptr_t quad_ctrl_bingo_evlog_clear_addr(void) { return 1; }
static uintptr_t quad_ctrl_bingo_evlog_pop_addr(void) { return 2; }
static uintptr_t quad_ctrl_bingo_evlog_count_addr(void) { return 3; }
static uintptr_t quad_ctrl_bingo_evlog_dropped_addr(void) { return 4; }
static uintptr_t quad_ctrl_bingo_evlog_lo_addr(void) { return 5; }
static uintptr_t quad_ctrl_bingo_evlog_hi_addr(void) { return 6; }
static uint32_t readw(uintptr_t address) {
    reads++;
    if (address == 5) return (uint32_t)items[index_q];
    if (address == 6) return (uint32_t)(items[index_q] >> 32);
    return regs[address];
}
static void writew(uint32_t value, uintptr_t address) {
    if (address == 2 && value != regs[address] && regs[3]) {
        regs[3]--; index_q++;
    }
    if (address == 1 && value != regs[address]) { regs[3] = 0; regs[4] = 0; }
    regs[address] = value;
}
'''
        source += functions + r'''
int main(void) {
    uint64_t buf[4] = {0};
    bingo_evlog_enable(5); assert(regs[0] == 1);
    regs[3] = 3; regs[2] = 8;
    uint32_t before = reads;
    assert(bingo_evlog_read(NULL, 4) == 0);
    assert(bingo_evlog_read(buf, 0) == 0); assert(reads == before);
    assert(bingo_evlog_read(buf, 2) == 2);
    assert(buf[0] == items[0] && buf[1] == items[1]);
    assert(regs[3] == 1 && regs[2] == 10);
    assert(bingo_evlog_read(buf + 2, 2) == 1);
    assert(buf[2] == items[2] && regs[2] == 11);
    assert(bingo_evlog_read(buf, 4) == 0);
    regs[4] = 123; assert(bingo_evlog_dropped() == 123);
    bingo_evlog_clear(); bingo_evlog_clear();
    assert(regs[1] == 2 && regs[3] == 0 && regs[4] == 0);
    index_q = 0; regs[3] = 3; bingo_evlog_print();
    assert(regs[3] == 0);
    bingo_evlog_enable(0); assert(regs[0] == 0);
    before = reads; bingo_evlog_print(); assert(reads == before + 1);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "api.c").write_text(source)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                            str(path / "api.c"), "-o", str(path / "api")], check=True)
            output = subprocess.check_output([str(path / "api")], text=True)
        self.assertIn("[EVLOG] ts=100 code=0x03 slot=0:1 arg=0x0002", output)
        self.assertIn("[EVLOG] count=3 dropped=0", output)

    def test_reserved_word_layout_and_macro_conversion(self):
        for version in (1, 2):
            defaults = driver.test_cfg_defaults(version)
            enabled = dict(defaults, evlog_enable=1)
            before, after = map(driver.test_cfg_bytes, (defaults, enabled))
            self.assertEqual(len(after), 128 * version)
            self.assertEqual(after[:108], before[:108])
            self.assertEqual(after[112:], before[112:])
            self.assertEqual(struct.unpack_from("<I", after, 108), (1,))
        cfg = driver.scenario_test_cfg(driver.SCENARIOS["t60e"])
        self.assertEqual(cfg["evlog_enable"], 1)
        self.assertEqual(driver.scenario_test_cfg(driver.SCENARIOS["t60"])["evlog_enable"], 0)
        cfg = driver.scenario_test_cfg(driver.SCENARIOS["t60"], "-DBINGO_EVLOG_ENABLE=1")
        self.assertEqual(cfg["evlog_enable"], 1)
        with self.assertRaises(ValueError):
            driver.test_cfg_bytes(dict(driver.test_cfg_defaults(), evlog_enable=-1))

    def fixture(self):
        log = "\n".join([
            "[BINGO_WD] 308000 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
            "[BINGO_WD] 364000 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1",
            "[BINGO_REPLAY] 392000 chip=0 task=3 type=0 logical_core=1 from=1 to=1 cluster=0 to_cluster=1 logical_cluster=0",
            "[BINGO_RETIRED] 420000 chip=0 core=1 cluster=0",
            "[EVLOG_BEGIN] 448000",
        ])
        uart = "\n".join([
            "[EVLOG] ts=10 code=0x01 slot=0:1 arg=0x0000",
            "[EVLOG] ts=12 code=0x03 slot=0:1 arg=0x0000",
            "[EVLOG] ts=13 code=0x04 slot=0:1 arg=0x4003",
            "[EVLOG] ts=14 code=0x0b slot=0:1 arg=0x0000",
            "[EVLOG] count=4 dropped=0",
        ])
        return log, uart

    def test_exact_timestamps_and_correspondence(self):
        log, uart = self.fixture()
        self.assertEqual(check_evlog(log, uart, 3), [])
        self.assertEqual(check_evlog_pair(log, log), [])

    def test_empty_log_is_rejected_by_default(self):
        self.assertEqual(check_evlog("", "[EVLOG] count=0 dropped=0", 3),
                         ["enabled EVLOG has no items"])

    def test_empty_log_is_allowed_without_simulation_events(self):
        self.assertEqual(check_evlog("", "[EVLOG] count=0 dropped=0", 3,
                                     allow_empty=True), [])
        for name, scene in driver.SCENARIOS.items():
            self.assertEqual(scene.get("evlog_allow_empty", False), name.startswith("c2_"))

    def test_empty_log_is_rejected_with_simulation_events(self):
        log = "[BINGO_WD] 308000 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0"
        self.assertIn("enabled EVLOG has no items",
                      check_evlog(log, "[EVLOG] count=0 dropped=0", 3, allow_empty=True))

    def test_negative_mutations(self):
        log, uart = self.fixture()
        for old, new in [
            ("ts=12", "ts=13"), ("code=0x04", "code=0x0d"),
            ("slot=0:1 arg=0x4003", "slot=1:1 arg=0x4003"),
            ("arg=0x4003", "arg=0x3003"), ("dropped=0", "dropped=1"),
            ("code=0x03", "code=0x83"), ("count=4", "count=3"),
        ]:
            with self.subTest(new=new):
                self.assertTrue(check_evlog(log, uart.replace(old, new), 3))
        self.assertTrue(check_evlog(log, "\n".join(uart.splitlines()[1:]), 3))
        self.assertTrue(check_evlog_pair(log, log.replace("308000", "308001")))
        self.assertTrue(check_evlog_pair(log, log.replace("448000", "448001")))

    def test_cerf_and_blocked_same_edge(self):
        log = "\n".join([
            "[BINGO_REPLAY_STUCK] 308000 chip=0 core=1 cluster=0: no live core",
            "[BINGO_REPLAY_BLOCKED] 308000 chip=0 core=1 cluster=0 task=3",
            "[BINGO_CERF_FB] 308000 type 2 clear g0 set g1",
        ])
        uart = "\n".join([
            "[EVLOG] ts=10 code=0x0a slot=0:2 arg=0x0020",
            "[EVLOG] ts=10 code=0x05 slot=0:1 arg=0x0000",
            "[EVLOG] ts=10 code=0x06 slot=0:1 arg=0x0000",
            "[EVLOG] count=3 dropped=0",
        ])
        self.assertEqual(check_evlog(log, uart, 3), [])


if __name__ == "__main__":
    unittest.main()
