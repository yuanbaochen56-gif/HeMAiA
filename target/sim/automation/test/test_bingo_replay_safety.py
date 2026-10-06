"""Positive and adversarial log fixtures for s37/s38/s39."""
import contextlib
import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

path = Path(__file__).with_name("3_start_bingo_watchdog_sim.py")
spec = importlib.util.spec_from_file_location("bingo_replay_safety_driver", path)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)
IDS = {"copy": 1, "add": 2, "backup": 3, "check": 4,
       "primary_exit": 7, "substitute_exit": 9}


def fixture(name, ids=IDS):
    protected, unsafe = name == "s37", name == "s38"
    faulty = protected or unsafe
    log = [f"[BINGO_DISPATCH] 10 chip=0 task={ids['copy']} core=1 cluster=0",
           f"[BINGO_DISPATCH] 20 chip=0 task={ids['add']} core=1 cluster=0"]
    if faulty:
        log += ["[BINGO_WD] 40 chip=0 core=1 cluster=0 dead_suspect=1 fenced=0",
                "[BINGO_WD] 50 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1"]
    if protected:
        log += [f"[BINGO_REPLAY_STUCK] 60 chip=0 core=1 cluster=0: no live core may run task {ids['add']}",
                f"[BINGO_REPLAY_BLOCKED] 60 chip=0 core=1 cluster=0 task={ids['add']}",
                "[BINGO_CERF_FB] 70 type 2 clear g0 set g1",
                f"[BINGO_DISPATCH] 80 chip=0 task={ids['backup']} core=2 cluster=0"]
    if unsafe:
        log += [f"[BINGO_REPLAY] 60 chip=0 task={ids['add']} type=0 logical_core=1 "
                "from=1 to=1 cluster=0 to_cluster=1 logical_cluster=0",
                "[BINGO_RETIRED] 65 chip=0 core=1 cluster=0",
                f"[BINGO_DISPATCH] 70 chip=0 task={ids['add']} core=1 cluster=1",
                f"[BINGO_EXIT_ABSORB] 95 chip=0 task={ids['primary_exit']} logical_core=1 "
                "logical_cluster=0 core=1 cluster=1",
                f"[BINGO_REMAP] 95 chip=0 task={ids['primary_exit']} logical_core=1 "
                "-> physical_core=1 cluster=1 logical_cluster=0"]
    log += [f"[BINGO_DISPATCH] 90 chip=0 task={ids['check']} core=2 cluster=0"]
    if not faulty:
        log += [f"[BINGO_DISPATCH] 100 chip=0 task={ids['primary_exit']} core=1 cluster=0"]
    if not protected:
        log += [f"[BINGO_DISPATCH] 110 chip=0 task={ids['substitute_exit']} core=1 cluster=1"]
    log += [f"[BINGO_STATUS] 120 chip=0 replay_stuck={int(protected)} remote_done_mismatch=0 "
            f"link_error=0 fenced=0x{2 if faulty else 0:x} dead_suspect=0x{2 if faulty else 0:x} remote_timeout=0",
            f"{driver.SIM_OK_MARKER} at 130"]
    uart = (
        f"[Host] Bingo status: replay_stuck={int(protected)} remote_done_mismatch=0 "
        f"link_error=0 fenced=0x{2 if faulty else 0:x} cerf=0x{2 if protected else 1:x} "
        f"cerf_fb_en=0x4 cerf_fb_evt=0x{4 if protected else 0:x} risk=0x0 "
        f"replay_blocked=0x{2 if protected else 0:x}\n"
        "[Host] Check [acc]: PASS\n"
        + ("[Host] Check [bk]: PASS\n" if protected else "")
        + f"[Int32Inplace] check complete; branch={int(protected)} unsafe={int(unsafe)} "
        f"replay_blocked=0x{2 if protected else 0:x}\n")
    return "\n".join(log), uart


def drop(text, needle):
    return "\n".join(line for line in text.splitlines() if needle not in line)


class ReplaySafetyCheckerTests(unittest.TestCase):
    def evaluate(self, name, log=None, uart=None, ids=IDS):
        original_log, original_uart = fixture(name, ids)
        sc = {**driver.SCENARIOS[name], "replay_safety_task_ids": ids}
        if name != "s39":
            sc["fault_gid"] = ids["add"]
        with contextlib.redirect_stdout(io.StringIO()):
            return driver.evaluate(name, sc, original_log if log is None else log,
                                   original_uart if uart is None else uart)

    def test_all_three_positive_fixtures_and_changed_ids(self):
        for name in ("s37", "s38", "s39"):
            for ids in (IDS, {key: value + 20 for key, value in IDS.items()}):
                with self.subTest(name=name, ids=ids):
                    self.assertEqual(self.evaluate(name, ids=ids), [])

    def test_s37_mutations_are_rejected(self):
        log, uart = fixture("s37")
        mutations = [
            (drop(log, "REPLAY_BLOCKED"), uart),
            (log.replace("task=2", "task=5"), uart),
            (log + "\n[BINGO_REPLAY] 65 chip=0 task=2 type=3 logical_core=1 from=1 to=1 cluster=0 to_cluster=1", uart),
            (drop(log, "BINGO_CERF_FB"), uart),
            (log.replace("type 2 clear", "type 1 clear"), uart),
            (log.replace("set g1", "set g0"), uart),
            (drop(log, "task=3 core=2"), uart),
            (log.replace("task=3 core=2", "task=3 core=1"), uart),
            (log.replace("80 chip=0", "55 chip=0"), uart),
            (log.replace("90 chip=0", "65 chip=0"), uart),
            (log + "\n[BINGO_DISPATCH] 100 chip=0 task=7 core=1 cluster=0", uart),
            (log, uart.replace("replay_blocked=0x2", "replay_blocked=0x4")),
            (log, uart.replace("cerf_fb_evt=0x4", "cerf_fb_evt=0x0")),
            (log, uart.replace("Check [acc]: PASS", "Check [acc]: FAIL")),
            (log, uart.replace("Check [bk]: PASS", "Check [bk]: FAIL")),
            (log, uart.replace("branch=1", "branch=0")),
        ]
        for index, (bad_log, bad_uart) in enumerate(mutations):
            with self.subTest(mutation=index):
                self.assertTrue(self.evaluate("s37", bad_log, bad_uart))

    def test_controls_reject_wrong_replay_or_backup(self):
        log, uart = fixture("s38")
        self.assertTrue(self.evaluate("s38", drop(log, "[BINGO_REPLAY]"), uart))
        self.assertTrue(self.evaluate("s38", log + "\n[BINGO_CERF_FB] 70 type 2 clear g0 set g1", uart))
        self.assertTrue(self.evaluate("s38", log, uart.replace("unsafe=1", "unsafe=0")))
        self.assertTrue(self.evaluate("s38", drop(log, "[BINGO_EXIT_ABSORB]"), uart))
        self.assertTrue(self.evaluate("s38", log, uart + "Exit task of cluster 0 core 1 taken over\n"))
        self.assertTrue(self.evaluate("s38", log + "\n[BINGO_DISPATCH] 100 chip=0 task=7 core=1 cluster=1", uart))
        log, uart = fixture("s39")
        self.assertTrue(self.evaluate("s39", log + "\n[BINGO_DISPATCH] 80 chip=0 task=3 core=2 cluster=0", uart))
        self.assertTrue(self.evaluate("s39", log + "\n[BINGO_WD] 50 chip=0 core=1 cluster=0 dead_suspect=1 fenced=1", uart))
        self.assertTrue(self.evaluate("s39", log, uart.replace("Check [acc]: PASS", "Check [acc]: FAIL")))

    def test_csv_parser_rejects_missing_and_duplicate_kernels(self):
        with tempfile.TemporaryDirectory() as temporary:
            csv = Path(temporary) / "final_dfg.csv"
            text = ("ID,Chiplet,Cluster,Core,Type,Kernel\n"
                    "1,00,0,1,normal,__snax_bingo_kernel_idma_1d_copy\n"
                    "2,00,0,1,normal,__snax_bingo_kernel_int32_add\n"
                    "3,00,0,2,normal,__host_bingo_kernel_add_i32\n"
                    "4,00,0,2,normal,__host_bingo_kernel_check_result\n"
                    "7,00,0,1,normal,__snax_bingo_kernel_exit\n"
                    "9,00,1,1,normal,__snax_bingo_kernel_exit\n")
            csv.write_text(text)
            self.assertEqual(driver.replay_safety_task_ids(csv), IDS)
            csv.write_text(drop(text, "__snax_bingo_kernel_int32_add"))
            with self.assertRaisesRegex(ValueError, "one add"):
                driver.replay_safety_task_ids(csv)
            csv.write_text(text + "20,00,0,1,normal,__snax_bingo_kernel_int32_add\n")
            with self.assertRaisesRegex(ValueError, "one add"):
                driver.replay_safety_task_ids(csv)
