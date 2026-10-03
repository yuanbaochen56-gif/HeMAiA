"""Compiler changes must invalidate generated headers, without deleting source."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "watchdog_clean", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)


class CleanAppBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        self.workload = self.repo / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/example"
        self.workload.mkdir(parents=True)
        (self.workload / "main_bingo.py").write_text("# generator\n")
        (self.workload / "Makefile").write_text(
            f"MK_DIR := {self.workload}\n"
            "DATA_H = $(MK_DIR)/data.h\n"
            "OFFLOAD_H = $(MK_DIR)/offload_bingo_hw.h\n"
            "MARGIN_JSON = $(MK_DIR)/margin.json\n"
            "$(DATA_H) $(OFFLOAD_H) $(MARGIN_JSON): $(MK_DIR)/main_bingo.py\n"
            "\tpython3 $(MK_DIR)/main_bingo.py --data_h $(DATA_H)\n")
        for name in ("data.h", "offload_bingo_hw.h", "margin.json"):
            (self.workload / name).write_text("generated\n")
        (self.workload / "build").mkdir()
        (self.workload / "build/object.o").write_text("old object\n")

    def dry_run(self):
        return subprocess.run(
            ["make", "--no-print-directory", "-n", "-f", str(self.workload / "Makefile"),
             str(self.workload / "offload_bingo_hw.h")],
            cwd=self.workload, check=True, text=True, capture_output=True).stdout

    def test_cleanup_makes_generator_run_again(self):
        self.assertNotIn("python3", self.dry_run())
        with patch.object(watchdog, "_REPO_ROOT", self.repo):
            watchdog.clean_app_builds("example")
        self.assertIn(f"python3 {self.workload}/main_bingo.py", self.dry_run())
        for name in ("data.h", "offload_bingo_hw.h", "margin.json", "build"):
            self.assertFalse((self.workload / name).exists())
        self.assertTrue((self.workload / "main_bingo.py").is_file())

    def test_tracked_output_aborts_before_any_cleanup(self):
        subprocess.run(["git", "-C", str(self.repo), "add",
                        str(self.workload / "margin.json")], check=True)
        with patch.object(watchdog, "_REPO_ROOT", self.repo):
            with self.assertRaisesRegex(ValueError, "tracked generated outputs"):
                watchdog.clean_app_builds("example")
        for name in ("data.h", "offload_bingo_hw.h", "margin.json", "build/object.o"):
            self.assertTrue((self.workload / name).is_file())

    def test_missing_git_metadata_fails_closed(self):
        with patch.object(watchdog, "_REPO_ROOT", self.repo), \
                patch.object(watchdog.subprocess, "run",
                             side_effect=subprocess.CalledProcessError(128, "git")):
            with self.assertRaises(subprocess.CalledProcessError):
                watchdog.clean_app_builds("example")
        self.assertTrue((self.workload / "offload_bingo_hw.h").is_file())

    def test_outside_or_unresolved_output_is_rejected(self):
        for value in ("$(MK_DIR)/../source.h", "$(UNKNOWN)/data.h", "$(shell echo source.h)"):
            (self.workload / "Makefile").write_text(
                f"DATA_H = {value}\n$(DATA_H): main_bingo.py\n\tpython3 main_bingo.py\n")
            with self.assertRaises(ValueError):
                watchdog.workload_generated_outputs(self.workload)

    def test_symlink_output_does_not_delete_target(self):
        path = self.workload / "data.h"
        path.unlink()
        source = self.repo / "source.h"
        source.write_text("source\n")
        path.symlink_to(source)
        with patch.object(watchdog, "_REPO_ROOT", self.repo):
            watchdog.clean_app_builds("example")
        self.assertEqual(source.read_text(), "source\n")
        self.assertFalse(path.is_symlink())

    def test_real_workload_generation_targets_are_resolved(self):
        root = watchdog._REPO_ROOT / "target/sw/host/apps/offload_bingo_hw"
        workloads = sorted(root.glob("*/workloads/*/main_bingo.py"))
        self.assertGreaterEqual(len(workloads), 70)
        for script in workloads:
            with self.subTest(workload=script.parent.name):
                outputs = watchdog.workload_generated_outputs(script.parent)
                self.assertIn(script.parent / "offload_bingo_hw.h", outputs)
                self.assertGreaterEqual(len(outputs), 2)


if __name__ == "__main__":
    unittest.main()
