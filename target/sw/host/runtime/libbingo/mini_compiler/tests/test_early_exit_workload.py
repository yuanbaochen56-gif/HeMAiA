"""Compile the real early-exit graph and verify the native requant formula."""
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
WORKLOAD = REPO_ROOT / "target/sw/host/apps/offload_bingo_hw/single_chip/workloads/early_exit_2cluster"


class EarlyExitWorkloadTests(unittest.TestCase):
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
                "--data_h", str(self.root / "early_exit_data.h"),
                "-c", str(WORKLOAD / "params.hjson"), "--hwcfg", str(self.hw),
                "--platformcfg", str(self.platform)]
        with patch.object(sys, "argv", argv), \
                patch.object(sys, "path", [str(WORKLOAD), *sys.path]), \
                patch.object(BingoDFG, "bingo_compile_dfg", compile_graph), \
                patch.object(BingoDFG, "bingo_visualize_dfg"), \
                contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            runpy.run_path(str(WORKLOAD / "main_bingo.py"), run_name="__main__")
        self.warnings = [str(item.message) for item in caught]
        self.nodes = {n.node_name: n for n in self.graph.node_list if n.node_name}
        self.output = (self.root / "offload_bingo_hw.h").read_text()

    def test_prefix_groups_table_and_exit_order(self):
        self.generate()
        n, graph = self.nodes, self.graph
        for name in ("load_X", "load_W1", "gemm1", "store_h", "requant"):
            self.assertTrue(nx.has_path(graph, n[name], n["gemm2"]))
            self.assertFalse(n[name].cond_exec_en)
        for name in ("load_h8", "load_W2", "gemm2", "store_deep"):
            self.assertTrue(n[name].cond_exec_en)
            self.assertEqual(n[name].cond_exec_group_id, 0)
        self.assertEqual(n["shallow_copy"].cond_exec_group_id, 1)
        self.assertTrue(graph.has_edge(n["gemm2"], n["shallow_copy"]) or
                        nx.has_path(graph, n["gemm2"], n["shallow_copy"]))
        self.assertTrue(nx.has_path(graph, n["store_deep"], n["shallow_copy"]))
        self.assertEqual(graph._cerf_fallbacks, [(n["gemm2"], n["shallow_copy"])])
        self.assertIn("bingo_cerf_fb_set(BINGO_CORE_TYPE_ID(0, 0), 0, 1);", self.output)
        for node in graph.node_list:
            if node.kernel_name == "__snax_bingo_kernel_exit" and node.assigned_core_id == 0:
                self.assertTrue(node.cond_exec_en)
                self.assertEqual(node.cond_exec_group_id, 0)
        self.assertFalse(any("late" in message for message in self.warnings), self.warnings)

    def test_ids_and_host_requant_addresses(self):
        self.generate()
        expected = ["gating", "load_X", "load_W1", "gemm1", "store_h", "requant",
                    "load_h8", "load_W2", "gemm2", "store_deep", "shallow_copy", "join"]
        self.assertEqual([self.nodes[name].node_id for name in expected], list(range(12)))
        args = self.nodes["requant"].kernel_args
        fields = args.get_c_field_assignments({args.src_addr: "ptr_h", args.dst_addr: "ptr_h8"})
        self.assertEqual(fields, dict(src_addr="(uint64_t)ptr_h", dst_addr="(uint64_t)ptr_h8",
                                      count="32", shift="2", dst_size="128"))
        self.assertIn('BINGO_EE_EXPECT_SHALLOW', self.output)
        self.assertIn('"requant.h"', self.output)

    def test_datagen_and_native_requant(self):
        module = runpy.run_path(str(WORKLOAD / "early_exit_datagen.py"))
        params = dict(M=1, K=2, N=1, meshRow=1, tileSize=16, meshCol=32, shift=2)
        data, size = module["generate_data"](params)
        self.assertEqual(size, 128)
        self.assertFalse(np.array_equal(data["shallow_golden"], data["deep_golden"]))
        self.assertTrue(np.all(data["input_X"][32:] == 0))
        fixture = self.root / "fixture.c"
        fixture.write_text('#include "requant.h"\n'
                           'void convert(int32_t *src, int8_t *dst, unsigned n, unsigned shift) {\n'
                           'for (unsigned i=0;i<n;i++) dst[i]=early_exit_requant(src[i],shift);\n}\n')
        library = self.root / "fixture.so"
        subprocess.run(["cc", "-shared", "-fPIC", "-std=c99", "-Wall", "-Wextra", "-Werror",
                        "-I", str(WORKLOAD), str(fixture), "-o", str(library)], check=True)
        native = ctypes.CDLL(str(library))
        native.convert.argtypes = [ctypes.POINTER(ctypes.c_int32), ctypes.POINTER(ctypes.c_int8),
                                   ctypes.c_uint, ctypes.c_uint]
        values = np.concatenate((np.arange(-2049, 2050, dtype=np.int32),
                                 np.array([-2**31, 2**31-1], dtype=np.int32),
                                 data["shallow_golden"]))
        for shift in (0, 1, 2, 7, 30):
            src = (ctypes.c_int32 * len(values))(*values)
            dst = (ctypes.c_int8 * len(values))()
            native.convert(src, dst, len(values), shift)
            np.testing.assert_array_equal(list(dst), module["requant"](values, shift))
        with self.assertRaises(ValueError):
            module["requant"](values, 31)


if __name__ == "__main__":
    unittest.main()
