from pathlib import Path
import subprocess
import tempfile
import unittest

from bingo_sdc_quiet_check import report_problems
from test_bingo_sdc_check import evidence
from test_bingo_sdc_device import MOCK, ROOT
from test_bingo_test_cfg import watchdog


class QuietTests(unittest.TestCase):
    pairs = [dict(selected=True, node_id=2, shadow_id=10, compare_id=11, status_index=0)]
    graph = dict(nodes={"11": dict(cluster=1, core=1)})

    def test_flag_combinations(self):
        for flags in ("-DBINGO_SDC_QUIET", "-DBINGO_WD_ADAPT -DBINGO_SDC_QUIET",
                      "-DBINGO_SDC_QUIET=1 -DBINGO_SDC"):
            with self.assertRaises(ValueError):
                watchdog.t1_image_flags(dict(t1=True, image_flags=flags))
        self.assertEqual(watchdog.t1_image_flags(dict(t1=True,
            image_flags="-DBINGO_SDC -DBINGO_SDC_QUIET")),
            "-DBINGO_SDC -DBINGO_SDC_QUIET -DBINGO_WD_ADAPT")

    def test_quiet_host_only_and_nonquiet_agreement(self):
        log, uart = evidence(False, True, True)
        scene = dict(sdc_enable=1, image_flags="-DBINGO_SDC -DBINGO_SDC_QUIET")
        self.assertEqual(report_problems(scene, log, uart, self.pairs, self.graph), [])
        scene["image_flags"] = "-DBINGO_SDC"
        self.assertTrue(report_problems(scene, log, uart, self.pairs, self.graph))
        device = "[BINGO_SDC] cluster=1 core=1 status_lo=0x80001000 result=18\n"
        self.assertEqual(report_problems(scene, log, device + uart, self.pairs, self.graph), [])
        self.assertTrue(report_problems(scene, log, device.replace("result=18", "result=19") + uart,
                                       self.pairs, self.graph))
        scene["image_flags"] += " -DBINGO_SDC_QUIET"
        self.assertTrue(report_problems(scene, log, device + uart, self.pairs, self.graph))

    def test_quiet_actual_c_preserves_status_and_injection(self):
        with tempfile.TemporaryDirectory() as directory:
            source, executable = Path(directory) / "device.c", Path(directory) / "device"
            source.write_text(MOCK)
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-fno-pie", "-no-pie",
                "-DBINGO_SDC", "-DBINGO_SDC_QUIET", "-DBINGO_TEST_CFG=1",
                "-I", str(ROOT / "target/sw/shared/runtime"),
                "-I", str(ROOT / "target/sw/host/runtime/libbingo/include"),
                "-I", str(ROOT / "target/sw/device/runtime/src"),
                "-I", str(ROOT / "target/sw/device/apps/snax/snax-bingo-offload/libsnaxkernel/offload_hw_kernels"),
                str(source), "-o", str(executable)], check=True, capture_output=True)
            output = subprocess.check_output([str(executable)], text=True)
            self.assertIn("SDC DEVICE PASS", output)
            self.assertIn("[BINGO_SDC_INJECT]", output)
            self.assertNotIn("[BINGO_SDC]", output)


if __name__ == "__main__":
    unittest.main()
