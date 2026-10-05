import copy
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import hjson

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "util/occamygen"))
from occamy import get_bingo_remote_kwargs


class BingoClusterBoundTests(unittest.TestCase):
    def config(self):
        return dict(s1_quadrant=dict(bingo_substitute_level_mask=3,
                                    bingo_cluster_bound_core_types=[1]),
                    hemaia_multichip=dict(chip_id_width=8), nr_s1_quadrant=1)

    def test_l2_requires_explicit_cluster_bound_types(self):
        cfg = self.config()
        del cfg["s1_quadrant"]["bingo_cluster_bound_core_types"]
        with self.assertRaisesRegex(ValueError, "must be declared"):
            get_bingo_remote_kwargs(cfg)

    def test_cluster_bound_type_rejects_l3_target(self):
        cfg = self.config()
        cfg["s1_quadrant"]["bingo_remote_target_chip"] = {"1": 0}
        with self.assertRaisesRegex(ValueError, "cluster-bound core type 1"):
            get_bingo_remote_kwargs(cfg)

    def test_only_declared_type_is_disabled(self):
        cfg = self.config()
        self.assertEqual(get_bingo_remote_kwargs(cfg)["bingo_substitute_l2_type_en"], 0xfffd)
        cfg["s1_quadrant"]["bingo_cluster_bound_core_types"] = []
        self.assertEqual(get_bingo_remote_kwargs(cfg)["bingo_substitute_l2_type_en"], 0xffff)

    def test_l1_without_key_preserves_default(self):
        cfg = self.config()
        cfg["s1_quadrant"] = dict(bingo_substitute_level_mask=1)
        self.assertEqual(get_bingo_remote_kwargs(cfg)["bingo_substitute_l2_type_en"], 0xffff)

    def test_every_base_config_declares_type_one(self):
        paths = sorted((ROOT / "target/rtl/cfg").glob("hemaia*.hjson"))
        self.assertEqual(len(paths), 11)
        for path in paths:
            with self.subTest(cfg=path.name):
                cfg = hjson.loads(path.read_text())
                self.assertEqual(cfg["s1_quadrant"]["bingo_cluster_bound_core_types"], [1])

    def test_c3_generated_l1_and_l2_parameters(self):
        base = hjson.loads((ROOT / "target/rtl/cfg/hemaia_ci.hjson").read_text())
        for mask, suffix in ((1, "cerf_l1"), (3, "l2")):
            with self.subTest(cfg=suffix), tempfile.TemporaryDirectory() as directory:
                out = Path(directory)
                cfg = copy.deepcopy(base)
                cfg["s1_quadrant"].update(
                    bingo_substitute_level_mask=mask,
                    bingo_watchdog_timeout_cycles=100000,
                    bingo_watchdog_confirm_timeout_cycles=200000)
                cfg_path = out / f"hemaia_ci_wd100000_cf200000_{suffix}.hjson"
                cfg_path.write_text(hjson.dumps(cfg))
                result = subprocess.run(
                    [sys.executable, str(ROOT / "util/occamygen/occamygen.py"),
                     "--cfg", str(cfg_path), "--outdir", str(out),
                     "--quad-ctrl", str(ROOT / "hw/occamy/occamy_quad_ctrl.sv.tpl")],
                    cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                self.assertEqual(result.returncode, 0, result.stdout)
                text = (out / "occamy_quad_ctrl.sv").read_text()
                self.assertRegex(text, r"\.SubstituteL2TypeEn\s*\(\s*16'd65533\s*\)")
                self.assertRegex(text, rf"\.SubstituteLevelMask\s*\(\s*3'd{mask}\s*\)")


if __name__ == "__main__":
    unittest.main()
