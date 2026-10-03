"""Real confidence graph, separated data and native workload-local kernels."""
import contextlib
import ctypes
import io
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import warnings

import networkx as nx
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bingo_dfg import BingoDFG

REPO_ROOT = Path(__file__).resolve().parents[7]
WORKLOAD = REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/early_exit_conf_2cluster"
B3 = WORKLOAD.parent / "early_exit_2cluster"


class EarlyExitConfWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.platform = self.root / "occamy.h"
        self.platform.write_text(
            "#define N_CHIPLETS 1\n#define CHIPLET_ID_0 0x00\n"
            "#define N_CLUSTERS_PER_CHIPLET 2\n#define N_CORES_PER_CLUSTER 2\n"
            "#define BINGO_CORE_TYPE_ID(cluster, core) "
            "(((cluster) == 0 && (core) == 0) ? 1 : "
            "((cluster) == 0 && (core) == 1) ? 2 : "
            "((cluster) == 1 && (core) == 0) ? 1 : "
            "((cluster) == 1 && (core) == 1) ? 2 : 0)\n")
        self.hw = self.root / "hw.json"
        self.hw.write_text(json.dumps({"snax_versacore_core_template": {"snax_acc_cfg": [{
            "snax_versacore_spatial_unrolling": [[[32, 2, 32], [1, 16, 32]]]
        }]}}))

    def generate(self):
        original = BingoDFG.bingo_compile_dfg

        def compile_graph(graph, *args, **kwargs):
            self.graph = graph
            return original(graph, *args, **kwargs)

        argv = [str(WORKLOAD / "main_bingo.py"), "--output_dir", str(self.root),
                "--data_h", str(self.root / "early_exit_conf_data.h"),
                "-c", str(WORKLOAD / "params.hjson"), "--hwcfg", str(self.hw),
                "--platformcfg", str(self.platform)]
        with patch.object(sys, "argv", argv), \
                patch.object(sys, "path", [str(WORKLOAD), str(B3), *sys.path]), \
                patch.object(BingoDFG, "bingo_compile_dfg", compile_graph), \
                patch.object(BingoDFG, "bingo_visualize_dfg"), \
                contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runpy.run_path(str(WORKLOAD / "main_bingo.py"), run_name="__main__")
        self.warnings = [str(item.message) for item in caught]
        self.nodes = {n.node_name: n for n in self.graph.node_list if n.node_name}
        self.output = (self.root / "offload_bingo_hw.h").read_text()

    def test_prefix_custom_gate_groups_and_protected_exits(self):
        self.generate()
        n, graph = self.nodes, self.graph
        for name in ("select_sample", "load_X", "load_W1", "gemm1", "store_h", "requant"):
            self.assertTrue(nx.has_path(graph, n[name], n["gemm2"]))
            self.assertFalse(n[name].cond_exec_en)
        gate = n["conf_gate"]
        self.assertEqual(gate.node_type, "gating")
        self.assertEqual(gate.cerf_write_groups, [0, 1])
        self.assertEqual(gate.kernel_name, "__host_bingo_kernel_ee_conf_gate")
        self.assertEqual(gate.kernel_args.cerf_controlled_mask, 3)
        for name in ("load_h8", "load_W2", "gemm2", "store_deep", "shallow_copy"):
            self.assertTrue(nx.has_path(graph, gate, n[name]))
            self.assertTrue(n[name].cond_exec_en)
            self.assertEqual(n[name].cond_exec_group_id, int(name == "shallow_copy"))
        self.assertTrue(nx.has_path(graph, n["gemm2"], n["shallow_copy"]))
        self.assertTrue(nx.has_path(graph, n["store_deep"], n["shallow_copy"]))
        self.assertEqual(graph._cerf_fallbacks, [(n["gemm2"], n["shallow_copy"])])
        exits = [node for node in graph.node_list
                 if node.kernel_name == "__snax_bingo_kernel_exit" and node.assigned_core_id == 0]
        self.assertEqual(len(exits), 2)
        self.assertTrue(all(node.cond_exec_en and node.cond_exec_group_id == 0 for node in exits))
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 0, 1);", self.output)
        self.assertFalse(any("late" in message for message in self.warnings), self.warnings)

    def test_reuses_b3_sources_and_formula(self):
        self.generate()
        makefile = (WORKLOAD / "Makefile").read_text()
        self.assertIn("$(B3_DIR)/requant.c", makefile)
        self.assertIn('runpy.run_path(str(B3 / "main_bingo.py"))["RequantArgs"]',
                      (WORKLOAD / "main_bingo.py").read_text())
        self.assertIn('runpy.run_path(str(B3 / "early_exit_datagen.py"))',
                      (WORKLOAD / "early_exit_conf_datagen.py").read_text())
        self.assertIn('"requant.h"', self.output)
        self.assertIn('"ee_conf.h"', self.output)
        self.assertIn("__fault_evt || __decision", self.output)
        self.assertIn("early_exit_conf_sample()", self.output)
        self.assertFalse((WORKLOAD / "requant.c").exists())
        self.assertIn("PARTIAL_OUTPUTS += $(MARGIN_JSON)", makefile)
        self.assertIn("$(DATA_H) $(OFFLOAD_H) $(MARGIN_JSON):", makefile)
        self.assertIn("early_exit_conf_data.json", (WORKLOAD / ".gitignore").read_text().splitlines())
        margins = json.loads((self.root / "early_exit_conf_data.json").read_text())
        self.assertLessEqual(2 * margins["margins"][0], margins["threshold"])
        self.assertGreaterEqual(margins["margins"][1], 2 * margins["threshold"])

    def test_datagen_separation_determinism_and_goldens(self):
        module = runpy.run_path(str(WORKLOAD / "early_exit_conf_datagen.py"))
        params = dict(M=1, K=2, N=1, meshRow=1, tileSize=16, meshCol=32, shift=2, threshold=16)
        data, size, margins = module["generate_data"](params)
        again, other_size, other_margins = module["generate_data"](params)
        self.assertEqual((size, margins), (other_size, other_margins))
        self.assertEqual(size, 128)
        self.assertLessEqual(2 * margins[0], params["threshold"])
        self.assertGreaterEqual(margins[1], 2 * params["threshold"])
        for name in data:
            np.testing.assert_array_equal(data[name], again[name])
        for sample in range(2):
            shallow = data["shallow_golden"][sample * 32:(sample + 1) * 32]
            deep = data["deep_golden"][sample * 32:(sample + 1) * 32]
            self.assertEqual(module["margin"](shallow), margins[sample])
            self.assertFalse(np.array_equal(shallow, deep))
            self.assertTrue(np.all(data["X_samples"][sample * size + 32:(sample + 1) * size] == 0))

    def native(self):
        include = self.root / "libbingo"
        include.mkdir()
        (include / "bingo_api.h").write_text(
            "#include <stdint.h>\n#define BINGO_RET_SUCC 0\n#define BINGO_RET_FAIL 1\n"
            "typedef struct { uint64_t return_value; uint32_t num_return_values; } bingo_kernel_scratchpad_t;\n"
            "void bingo_cerf_update(uint32_t clear, uint32_t set);\n")
        source = self.root / "fixture.c"
        source.write_text(
            '#include "libbingo/bingo_api.h"\n#include "ee_conf.h"\n#include "bingo_test_cfg.h"\n'
            'volatile bingo_test_cfg_t bingo_test_cfg = BINGO_TEST_CFG_INITIALIZER;\n'
            'uint32_t last_clear, last_set, calls;\n'
            'void bingo_cerf_update(uint32_t clear, uint32_t set) { last_clear=clear; last_set=set; calls++; }\n'
            'uint64_t gate(int32_t *src, unsigned n, unsigned threshold, unsigned *decision) {\n'
            'bingo_kernel_scratchpad_t sp={9,9};\n'
            '__host_bingo_kernel_ee_conf_gate_args_t args={(uintptr_t)src,(uintptr_t)decision,n,threshold,3,(uintptr_t)&sp};\n'
            'uint64_t result=__host_bingo_kernel_ee_conf_gate(&args);\n'
            'return sp.return_value==result && !sp.num_return_values ? result : 9; }\n'
            'uint64_t select_sample(int8_t *src, int8_t *dst, unsigned size, unsigned sample) {\n'
            'bingo_test_cfg.user[0]=sample; bingo_kernel_scratchpad_t sp={9,9};\n'
            '__host_bingo_kernel_ee_select_args_t args={(uintptr_t)src,(uintptr_t)dst,size,2,(uintptr_t)&sp};\n'
            'uint64_t result=__host_bingo_kernel_ee_select(&args);\n'
            'return sp.return_value==result && !sp.num_return_values ? result : 9; }\n'
            'uint64_t margin(int32_t *src, unsigned n) { return early_exit_conf_margin(src,n); }\n')
        library = self.root / "fixture.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-std=c11", "-Wall", "-Wextra", "-Werror",
                        "-DBINGO_TEST_CFG=1", "-I", str(self.root), "-I", str(WORKLOAD),
                        "-I", str(REPO_ROOT / "target/sw/shared/runtime"),
                        str(source), str(WORKLOAD / "ee_conf.c"), "-o", str(library)], check=True)
        native = ctypes.CDLL(str(library))
        native.gate.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_uint, ctypes.c_uint,
                                ctypes.POINTER(ctypes.c_uint)]
        native.gate.restype = ctypes.c_uint64
        native.margin.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.c_uint]
        native.margin.restype = ctypes.c_uint64
        native.select_sample.argtypes = [ctypes.POINTER(ctypes.c_int8), ctypes.POINTER(ctypes.c_int8),
                                         ctypes.c_uint, ctypes.c_uint]
        native.select_sample.restype = ctypes.c_uint64
        return native

    def test_native_margin_gate_and_sample_copy(self):
        native = self.native()
        cases = [[9, 9, 1], [-7, -2, -4], [-2**31, 2**31-1],
                 [-2**31, -2**31], [0, 1, 2, 3], [42, 1, -100]]
        rng = np.random.default_rng(450)
        cases += rng.integers(-(2**31), 2**31, size=(50, 32), dtype=np.int32).tolist()
        for values in cases:
            src = (ctypes.c_int32 * len(values))(*values)
            expected = sorted(values)[-1] - sorted(values)[-2]
            self.assertEqual(native.margin(src, len(values)), expected)
            for threshold in (0, 16, min(expected, 0xFFFFFFFF), 0xFFFFFFFF):
                decision = ctypes.c_uint(99)
                self.assertEqual(native.gate(src, len(values), threshold, ctypes.byref(decision)), 0)
                self.assertEqual(decision.value, int(expected >= threshold))
                self.assertEqual(ctypes.c_uint.in_dll(native, "last_clear").value, 3)
                self.assertEqual(ctypes.c_uint.in_dll(native, "last_set").value, 2 if decision.value else 1)
        samples = (ctypes.c_int8 * 256)(*[i % 256 - 128 for i in range(256)])
        for sample in (0, 1):
            dst = (ctypes.c_int8 * 128)()
            self.assertEqual(native.select_sample(samples, dst, 128, sample), 0)
            self.assertEqual(list(dst), list(samples)[sample * 128:(sample + 1) * 128])
        dst = (ctypes.c_int8 * 128)(*[7] * 128)
        self.assertEqual(native.select_sample(samples, dst, 128, 2), 1)
        self.assertEqual(list(dst), [7] * 128)


if __name__ == "__main__":
    unittest.main()
