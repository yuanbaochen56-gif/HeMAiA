"""Task-time estimates (bingo_cost.py) and their use in the generated code."""
import io
import itertools
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bingo_cost as cost
import bingo_fence_fb
from bingo_dfg import BingoDFG
from bingo_kernel_args import (SnaxBingoKernelGemmFullArgs, SnaxBingoKernelIdma1dCopyArgs,
                               SnaxBingoKernelInt32AddArgs)
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_node import BingoNode
from bingo_platform import parse_task_time_platform

COPY = "__snax_bingo_kernel_idma_1d_copy"
GEMM = "__snax_bingo_kernel_gemm_full"
ADD = "__snax_bingo_kernel_int32_add"
EXIT = "__snax_bingo_kernel_exit"


class EncodingTests(unittest.TestCase):
    def test_codes_round_up_within_one_sixteenth(self):
        previous = 0
        for ticks in itertools.chain(range(1, 5000), range(5000, 1015809, 997), [1015808]):
            code = cost.encode(ticks)
            value = cost.decode(code)
            self.assertNotEqual(code, 0, ticks)
            self.assertGreaterEqual(value, ticks)
            self.assertGreaterEqual(value, previous)       # monotonic
            if ticks >= 17:
                self.assertLessEqual(value, ticks * 17 / 16, ticks)
            previous = value

    def test_boundaries(self):
        self.assertEqual(cost.encode(0), 0)
        self.assertEqual(cost.decode(0), 0)
        self.assertEqual(cost.encode(1), 1)                # 16 is code 0 = none: 17 ticks
        self.assertEqual(cost.decode(cost.encode(16)), 17)
        self.assertEqual(cost.decode(cost.encode(17)), 17)
        self.assertEqual(cost.encode(31), 0x0F)
        self.assertEqual(cost.decode(0x10), 32)
        self.assertEqual(cost.decode(0xFF), 1015808)
        self.assertEqual(cost.encode(1015808), 0xFF)
        self.assertEqual(cost.encode(1015809), 0)          # no code: no estimate
        self.assertEqual(cost.decode(cost.encode(2515)), 2560)

    def test_entry_and_shift(self):
        self.assertEqual(cost.entry(2515, 0), cost.encode(2515))
        self.assertEqual(cost.entry(2515, 3) >> 8, 3)
        self.assertEqual(cost.entry(0, 2), 0)
        self.assertEqual(cost.entry(2_000_000, 1), 0)
        code = cost.encode(64925)
        self.assertEqual(cost.bucket_shift(code, [32463]), 1)
        self.assertEqual(cost.bucket_shift(code, [64925]), 0)
        self.assertEqual(cost.bucket_shift(code, [1]), 3)


class BucketTests(unittest.TestCase):
    def worst(self, groups):
        return max(g[-1] / g[0] for g in groups)

    def test_optimal_against_brute_force(self):
        values = [92, 100, 119, 124, 157, 332, 455, 479, 482, 510, 1193, 2523, 2596, 8473]
        for count in range(1, 8):
            groups = cost.buckets(values, count)
            self.assertLessEqual(len(groups), count)
            self.assertEqual(sorted(v for g in groups for v in g), sorted(set(values)))
            best = min(self.worst([values[i:j] for i, j in zip((0,) + cuts, cuts + (len(values),))])
                       for k in range(count) for cuts in itertools.combinations(range(1, len(values)), k))
            self.assertAlmostEqual(self.worst(groups), best)

    def test_small_and_deterministic(self):
        self.assertEqual(cost.buckets([], 7), [])
        self.assertEqual(cost.buckets([5, 5, 5], 7), [[5]])
        self.assertEqual(cost.buckets([3, 1, 2], 7), [[1], [2], [3]])
        self.assertEqual(cost.buckets([10, 11, 100], 2), [[10, 11], [100]])
        groups = cost.buckets([10, 11, 100, 101], 2)
        self.assertEqual(cost.bucket_index(groups, 11), 1)
        self.assertEqual(cost.bucket_index(groups, 101), 2)


class EstimateTests(unittest.TestCase):
    def test_task_keys(self):
        self.assertEqual(cost.task_key(COPY, dict(size=256), "L3", "L1"), ("copy", "size=256 L3>L1", 256))
        self.assertEqual(cost.task_key(GEMM, dict(M=1, K=2, N=1, array_shape_idx=1, transpose_A=0))[1],
                         "M=1 K=2 N=1 array_shape_idx=1")
        self.assertEqual(cost.task_key(ADD, dict(num_elements=512))[1:], ("n=512", 512))
        self.assertEqual(cost.task_key(EXIT, dict(exit_code=0))[1:], ("", 0))

    def test_memory_levels(self):
        l1 = BingoMemAlloc("x", 64, mem_level="L1", cluster_id=1)
        self.assertEqual(cost.mem_level(l1, 1), "L1")
        self.assertEqual(cost.mem_level(l1, 0), "L1r")
        self.assertEqual(cost.mem_level(l1.view(16), 1), "L1")
        self.assertEqual(cost.mem_level(BingoMemAlloc("y", 64), 0), "L3")
        self.assertEqual(cost.mem_level(BingoMemSymbol("w"), 0), "L3")
        self.assertEqual(cost.mem_level(0x80000000, 0), "L3")

    def test_rule(self):
        ci = "hemaia_ci"
        # measured warm, measured cold, warm + cold term, model, out of range, none
        self.assertEqual(cost.estimate(COPY, "copy", "size=256 L3>L1", 256, False, ci), (100, "measured"))
        self.assertEqual(cost.estimate(COPY, "copy", "size=256 L3>L1", 256, True, ci), (510, "measured (cold)"))
        self.assertEqual(cost.estimate(COPY, "copy", "size=128 L1>L3", 128, True, ci),
                         (119 + 410, "measured + cold term"))
        value, source = cost.estimate(COPY, "copy", "size=2048 L3>L1", 2048, False, ci)
        self.assertEqual(source, "model")
        self.assertEqual(value, -(-(90.92 + 0.0324 * 2048) // 1))
        self.assertEqual(cost.estimate(COPY, "copy", "size=4096 L3>L1", 4096, False, ci)[0], None)
        self.assertEqual(cost.estimate(GEMM, "gemm", "M=9 K=9 N=9", 729, False, ci)[0], None)
        # a warm execution of a size measured only cold takes the cold value
        self.assertEqual(cost.estimate(ADD, "eltwise", "n=512", 512, False, ci), (64925, "measured (cold)"))
        for kernel in ("__snax_bingo_kernel_sdc_compare", "__host_bingo_kernel_idma", ""):
            self.assertIsNone(cost.estimate(kernel, "x", "size=64", 64, False, ci)[0])
        self.assertIsNone(cost.estimate(COPY, "copy", "size=256 L3>L1", 256, False, None)[0])

    def test_interval_shift(self):
        self.assertEqual(cost.interval_shift(ADD, dict(num_elements=32)), 0)
        self.assertEqual(cost.interval_shift(ADD, dict(num_elements=512)), 1)
        self.assertEqual(cost.interval_shift(ADD, dict(num_elements=1024)), 2)
        self.assertEqual(cost.interval_shift(ADD, dict(num_elements=1 << 20)), 3)
        self.assertEqual(cost.interval_shift(COPY, dict(size=1 << 20)), 0)

    def test_platforms(self):
        self.assertEqual(cost.platform_name(2, 2, 28000), "hemaia_ci")
        self.assertEqual(cost.platform_name(4, 2, 28000), "hemaia_ci_4cluster")
        self.assertIsNone(cost.platform_name(2, 2, 14000))
        self.assertIsNone(cost.platform_name(2, 2, None))
        self.assertIsNone(cost.platform_name(1, 2, 28000))
        with tempfile.TemporaryDirectory() as tmp:
            header = Path(tmp) / "occamy.h"
            header.write_text("#define N_CLUSTERS_PER_CHIPLET 2\n#define N_CORES_PER_CLUSTER 2\n")
            self.assertEqual(parse_task_time_platform(header), dict(num_clusters=2, num_cores=2, tick_ps=None))
            header.write_text(header.read_text() + "#define BINGO_WD_TICK_PS 28000\n")
            self.assertEqual(parse_task_time_platform(header)["tick_ps"], 28000)


class GeneratedCodeTests(unittest.TestCase):
    TYPES = {(0, 0): 1, (0, 1): 2, (0, 2): 0, (1, 0): 1, (1, 1): 2, (1, 2): 0}

    def graph(self, task_time=True, platform="hemaia_ci"):
        with patch.dict(os.environ, {"BINGO_TASK_TIME": "1" if task_time else ""}, clear=False):
            os.environ.pop("BINGO_PLATFORM_CFG", None)
            graph = BingoDFG(1, 2, 2, True, [0], core_type_ids=dict(self.TYPES))
        graph._task_time_platform = platform if task_time else None
        return graph

    def copy(self, graph, size, cluster=0, conditional=False):
        node = BingoNode(0, cluster, 1, kernel_name=COPY, kernel_args=SnaxBingoKernelIdma1dCopyArgs(
            BingoMemAlloc(f"s{graph.id}", size), BingoMemAlloc(f"d{graph.id}", size, "L1", 0, cluster), size))
        node.cond_exec_en = conditional
        graph.bingo_add_node(node)
        return node

    def gemm(self, graph, cluster=0):
        node = BingoNode(0, cluster, 0, kernel_name=GEMM, kernel_args=SnaxBingoKernelGemmFullArgs(
            0, 0, 0, 0, 1, 2, 1, 1, 0, 0, 0))
        graph.bingo_add_node(node)
        return node

    def test_switch_and_mode_off(self):
        with patch.dict(os.environ, {"BINGO_TASK_TIME": ""}):
            self.assertFalse(BingoDFG(1, 2, 2, True, [0], core_type_ids=dict(self.TYPES)).task_time)
        with patch.dict(os.environ, {"BINGO_TASK_TIME": "1"}):
            self.assertTrue(BingoDFG(1, 2, 2, True, [0], core_type_ids=dict(self.TYPES)).task_time)
        graph = self.graph(task_time=False)
        self.copy(graph, 256)
        stream = io.StringIO()
        graph._emit_headers(stream, [])
        graph._emit_task_time_launch(stream, 0)
        self.assertNotIn("TASK_TIME", stream.getvalue())

    def test_cold_per_core_and_kernel(self):
        graph = self.graph()
        first, second = self.copy(graph, 256), self.copy(graph, 256)
        other_cluster = self.copy(graph, 256, cluster=1)
        gemm = self.gemm(graph)
        graph.bingo_add_edge(first, second)
        est = graph._task_time_estimates()
        self.assertEqual((est[first]["cold"], est[second]["cold"], est[other_cluster]["cold"], est[gemm]["cold"]),
                         (True, False, True, True))
        self.assertEqual((est[first]["e_tot"], est[second]["e_tot"]), (510, 100))
        self.assertEqual(est[gemm]["e_tot"], 2596)

    def test_conditional_tasks_stay_cold(self):
        graph = self.graph()
        a, b, c = self.copy(graph, 256, conditional=True), self.copy(graph, 256), self.copy(graph, 256)
        graph.bingo_add_edge(a, b)
        graph.bingo_add_edge(b, c)
        est = graph._task_time_estimates()
        self.assertEqual([est[n]["cold"] for n in (a, b, c)], [True, True, False])

    def test_type_bounds_need_every_task(self):
        graph = self.graph()
        self.copy(graph, 256)
        self.gemm(graph)
        stream = io.StringIO()
        graph._emit_task_time_launch(stream, 0)
        text = stream.getvalue()
        self.assertIn("bingo_test_cfg.wd_type_h[1] = BINGO_TASK_TIME_SCALE(5192u);", text)
        self.assertIn("bingo_test_cfg.wd_type_c[1] = BINGO_TASK_TIME_SCALE(10384u);", text)
        self.assertIn("bingo_wd_type_set(2, BINGO_TASK_TIME_SCALE(1020u), BINGO_TASK_TIME_SCALE(2040u));", text)
        # a copy without an estimate takes the bound of its type away
        self.copy(graph, 1 << 16)
        stream = io.StringIO()
        graph._emit_task_time_launch(stream, 0)
        self.assertIn("// core type 2: no bound", stream.getvalue())
        self.assertNotIn("wd_type_h[2]", stream.getvalue())
        self.assertIn("wd_type_h[1]", stream.getvalue())

    def test_header_checks_and_unknown_platform(self):
        graph = self.graph()
        self.copy(graph, 256)
        stream = io.StringIO()
        graph._emit_headers(stream, [])
        text = stream.getvalue()
        for line in ('#ifdef BINGO_TASK_TIME\n#include "bingo_test_cfg.h"',
                     '#error "BINGO_TASK_TIME: the estimates are calibrated for the standard device runtime"',
                     "#if BINGO_PM_NORMAL_POWER_LEVEL != 6",
                     "_Static_assert(BINGO_WD_TICK_PS == 28000",
                     "#define BINGO_TASK_TIME_SCALE(t) (2u * (t))"):
            self.assertIn(line, text)
        graph = self.graph(platform=None)
        graph.task_time = True
        self.copy(graph, 256)
        stream = io.StringIO()
        graph._emit_headers(stream, [])
        graph._emit_task_time_launch(stream, 0)
        self.assertIn("no task-time calibration of this platform", stream.getvalue())
        self.assertNotIn("wd_type", stream.getvalue())

    def test_fence_fallback_model_only_in_task_time_mode(self):
        graph = self.graph()
        node = BingoNode(0, 0, 1, kernel_name=ADD, kernel_args=SnaxBingoKernelInt32AddArgs(
            BingoMemAlloc("a", 4 * 32), BingoMemAlloc("b", 4 * 32), BingoMemAlloc("c", 4 * 32), 32))
        graph.bingo_add_node(node)
        self.assertIn("no host estimate", bingo_fence_fb.host_is_cheaper(node, graph))
        graph.task_time = False
        self.assertIn("measured at num_elements 512, not 32", bingo_fence_fb.host_is_cheaper(node, graph))
        self.assertIn("measured at num_elements 512, not 32", bingo_fence_fb.host_is_cheaper(node))
        with patch.dict(cost.HOST_US, {("__host_bingo_kernel_add_i32", 32): 10.0}):
            graph.task_time = True
            self.assertEqual(bingo_fence_fb.host_is_cheaper(node, graph), "")
        with patch.dict(cost.HOST_US, {("__host_bingo_kernel_add_i32", 32): 100.0}):
            self.assertIn("is not below 0.8 x", bingo_fence_fb.host_is_cheaper(node, graph))


if __name__ == "__main__":
    unittest.main()
