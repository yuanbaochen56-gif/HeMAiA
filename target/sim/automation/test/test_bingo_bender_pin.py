"""The driver must distinguish TB-only HEAD divergence from changed RTL."""
import contextlib
import importlib.util
import io
from pathlib import Path
import re
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "watchdog_pin", Path(__file__).with_name("3_start_bingo_watchdog_sim.py"))
watchdog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watchdog)
import bingo_bender_pin as pin_check


class BingoBenderPinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.hemaia = self.root / "HeMAiA"
        self.bingo = self.root / "actual_bingo"
        self.hemaia.mkdir()
        self.bingo.mkdir()
        self.git("init", "-q")
        (self.bingo / "src").mkdir()
        (self.bingo / "test").mkdir()
        (self.bingo / "src/top.sv").write_text("module top; endmodule\n")
        (self.bingo / "test/harness.svh").write_text("// before\n")
        (self.bingo / "Bender.yml").write_text(
            "package:\n  name: bingo_hw_manager\nsources:\n  - src/top.sv\n"
            "  - target: test\n    files:\n      - test/harness.svh\n")
        self.git("add", ".")
        self.git("commit", "-q", "-m", "Add pinned RTL fixture")
        self.pin = self.git("rev-parse", "HEAD").strip()
        (self.hemaia / "Bender.yml").write_text(
            f'dependencies:\n  bingo_hw_manager: {{git: "local", rev: "{self.pin}"}}\n')
        (self.hemaia / "Bender.lock").write_text(
            "packages:\n  bingo_hw_manager:\n    revision: null\n    version: null\n"
            "    source:\n      Path: ../actual_bingo\n    dependencies: []\n")
        self.sources = patch.object(pin_check, "_bender_rtl_inputs",
                                    side_effect=lambda text: sorted(re.findall(r"- (src/\S+)", text)))
        self.sources.start()
        self.addCleanup(self.sources.stop)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.bingo), *args], check=True,
                              capture_output=True, text=True).stdout

    def commit(self, path, content):
        (self.bingo / path).write_text(content)
        self.git("add", path)
        self.git("commit", "-q", "-m", "Update source fixture")

    def check(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            record = watchdog.check_bingo_bender_pin(self.hemaia, self.bingo)
        return record, output.getvalue()

    def test_head_equals_pin_passes_without_source_query(self):
        record, output = self.check()
        self.assertEqual(record["status"], "HEAD_MATCH")
        self.assertNotIn("WARNING", output)
        pin_check._bender_rtl_inputs.assert_not_called()

    def test_tb_only_head_divergence_warns_with_identical_rtl(self):
        self.commit("test/harness.svh", "// explicitly connected ports\n")
        record, output = self.check()
        self.assertEqual(record["status"], "RTL_IDENTICAL")
        self.assertEqual(record["rtl_files"], ["src/top.sv"])
        self.assertIn("WARNING", output)
        self.assertIn(self.pin, output)

    def test_changed_compiled_rtl_fails(self):
        self.commit("src/top.sv", "module top; wire changed; endmodule\n")
        with self.assertRaisesRegex(ValueError, "RTL differs: src/top.sv"):
            self.check()

    def test_dirty_path_checkout_only_warns_when_head_matches(self):
        (self.bingo / "src/top.sv").write_text("module top; wire dirty; endmodule\n")
        record, output = self.check()
        self.assertEqual(record["status"], "HEAD_MATCH")
        self.assertTrue(record["dirty"])
        self.assertIn("uncommitted changes", output)

    def test_dirty_rtl_does_not_hide_divergent_head(self):
        self.commit("test/harness.svh", "// after\n")
        (self.bingo / "src/top.sv").write_text("module top; wire dirty; endmodule\n")
        with self.assertRaisesRegex(ValueError, "RTL differs"):
            self.check()

    def test_lock_path_is_actual_source_not_requested_sibling(self):
        other = self.root / "wrong_bingo"
        other.mkdir()
        with self.assertRaisesRegex(ValueError, "Bender compiles Bingo from"):
            watchdog.check_bingo_bender_pin(self.hemaia, other)

    def test_block_style_rev_and_quoted_absolute_path(self):
        (self.hemaia / "Bender.yml").write_text(
            f"dependencies:\n  bingo_hw_manager:\n    git: local\n    rev: '{self.pin}'\n"
            "  other: {version: 1}\n")
        text = (self.hemaia / "Bender.lock").read_text()
        (self.hemaia / "Bender.lock").write_text(
            text.replace("../actual_bingo", f'"{self.bingo}"') + "  other:\n    revision: null\n")
        self.assertEqual(self.check()[0]["status"], "HEAD_MATCH")

    def test_source_membership_change_fails(self):
        self.commit("Bender.yml", (self.bingo / "Bender.yml").read_text().replace("  - src/top.sv\n", ""))
        with self.assertRaisesRegex(ValueError, "RTL source list differs"):
            self.check()

    def test_git_dependency_uses_bender_checkout_not_extra_mount(self):
        cached = self.hemaia / ".bender/git/checkouts"
        cached.mkdir(parents=True)
        (cached / "bingo_hw_manager-local").symlink_to(self.bingo, target_is_directory=True)
        (self.hemaia / "Bender.lock").write_text(
            f"packages:\n  bingo_hw_manager:\n    revision: {self.pin}\n"
            "    source:\n      Git: local\n    dependencies: []\n")
        other = self.root / "uncompiled_sibling"
        other.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            record = watchdog.check_bingo_bender_pin(self.hemaia, other)
        self.assertEqual(record["checkout"], str(self.bingo))
        self.assertEqual(record["status"], "HEAD_MATCH")

    def test_ambiguous_git_dependency_fails_closed(self):
        cached = self.hemaia / ".bender/git/checkouts"
        cached.mkdir(parents=True)
        (cached / "bingo_hw_manager-a").symlink_to(self.bingo, target_is_directory=True)
        (cached / "bingo_hw_manager-b").mkdir()
        (self.hemaia / "Bender.lock").write_text(
            f"packages:\n  bingo_hw_manager:\n    revision: {self.pin}\n"
            "    source:\n      Git: local\n")
        with self.assertRaisesRegex(ValueError, "Cannot uniquely resolve"):
            self.check()

    def test_uncompiled_file_change_does_not_fail(self):
        self.commit("src/unused.sv", "module unused; endmodule\n")
        self.assertEqual(self.check()[0]["status"], "RTL_IDENTICAL")

    def test_missing_pin_object_fails_closed(self):
        (self.hemaia / "Bender.yml").write_text(
            f"dependencies:\n  bingo_hw_manager: {{rev: {'f' * 40}}}\n")
        with self.assertRaisesRegex(ValueError, "Cannot verify Bingo Git"):
            self.check()

    def test_main_checks_pin_before_simulator_or_scenario(self):
        with patch.object(watchdog.sys, "argv", ["driver"]), \
                patch.object(watchdog, "check_bingo_bender_pin",
                             side_effect=ValueError("RTL differs")) as check, \
                patch.object(watchdog.shutil, "which") as which, \
                patch.object(watchdog, "run_scenario") as run:
            with self.assertRaisesRegex(SystemExit, "Bingo Bender pin check failed"):
                watchdog.main()
            check.assert_called_once()
            which.assert_not_called()
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
