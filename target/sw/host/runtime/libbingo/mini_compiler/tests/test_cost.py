"""Task-time estimates (bingo_cost.py) and their use in the generated code."""
import io
import itertools
import math
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
        a, b, w_max = cost.CALIBRATION[ci]["models"]["copy L3>L1"]
        self.assertEqual(value, math.ceil(a + b * 2048))
        self.assertEqual(w_max, 262144)                    # the large copies are calibrated
        self.assertEqual(cost.estimate(COPY, "copy", "size=1048576 L3>L1", 1 << 20, False, ci)[0], None)
        self.assertEqual(cost.estimate(GEMM, "gemm", "M=9 K=9 N=9 quantization_enable=1", 729, False, ci)[0],
                         None)
        # a warm execution of a size measured only cold takes the cold value
        self.assertEqual(cost.estimate(ADD, "eltwise", "n=512", 512, False, ci), (64925, "measured (cold)"))
        for kernel in ("__snax_bingo_kernel_sdc_compare", "__host_bingo_kernel_idma", ""):
            self.assertIsNone(cost.estimate(kernel, "x", "size=64", 64, False, ci)[0])
        self.assertIsNone(cost.estimate(COPY, "copy", "size=256 L3>L1", 256, False, None)[0])

    def test_gemm_cycles(self):
        self.assertEqual(cost.gemm_cycles("M=8 K=512 N=8"), 8 * 8 * 512)
        self.assertEqual(cost.gemm_cycles("M=2 K=4 N=2"), 2 * 2 * 32)            # write-back bound
        self.assertEqual(cost.gemm_cycles("M=1 K=2 N=1 array_shape_idx=1"), 2)   # 1x32 outputs: 1 cycle
        self.assertEqual(cost.gemm_cycles("M=1 K=4 N=1 array_shape_idx=2"), 8)   # 16x16 outputs: 8 cycles
        for key in ("M=1 K=16 N=1 quantization_enable=1", "M=1 K=16 N=1 accumPrevC=1",
                    "M=1 K=16 N=1 transpose_A=1", "M=1 K=16 N=1 array_shape_idx=3", "M=1 K=16", "",
                    "M=x K=16 N=1"):
            self.assertIsNone(cost.gemm_cycles(key), key)

    def test_gemm_model(self):
        ci, cal = "hemaia_ci", cost.CALIBRATION["hemaia_ci"]["gemm"]
        tpc = cost.GEMM_TICKS_PER_CYCLE
        # a measured size keeps its measured worst case
        self.assertEqual(cost.estimate(GEMM, "gemm", "M=8 K=128 N=1", 1024, False, ci), (1449, "measured"))
        self.assertEqual(cost.estimate(GEMM, "gemm", "M=8 K=512 N=8", 32768, True, ci), (16524, "measured (cold)"))
        # an unseen size: fixed part + array cycles, warm and cold
        self.assertEqual(cost.estimate(GEMM, "gemm", "M=4 K=256 N=4", 4096, False, ci),
                         (math.ceil(cal["warm"] + tpc * 4096), "gemm model"))
        self.assertEqual(cost.estimate(GEMM, "gemm", "M=4 K=256 N=4", 4096, True, ci),
                         (math.ceil(cal["cold"] + tpc * 4096), "gemm model (cold)"))
        four = cost.CALIBRATION["hemaia_ci_4cluster"]["gemm"]
        self.assertEqual(cost.estimate(GEMM, "gemm", "M=8 K=512 N=8", 32768, False, "hemaia_ci_4cluster")[0],
                         math.ceil(four["warm"] + tpc * 32768))

    def test_gemm_model_covers_the_calibrated_worst_cases(self):
        """The fixed parts are the largest calibrated residuals: the model is not below
        any measured worst case of a size it applies to (the tables round up by < 1)."""
        for platform, cal in cost.CALIBRATION.items():
            for table, cold in (("warm", False), ("cold", True)):
                for (kernel, key), measured in cal[table].items():
                    if kernel != GEMM or cost.gemm_cycles(key) is None:
                        continue
                    model = cost.gemm_estimate(cal, kernel, key, cold)
                    self.assertGreaterEqual(model, measured - 1, (platform, key, cold))

    def test_interval(self):
        ci, cal = "hemaia_ci", cost.CALIBRATION["hemaia_ci"]["gemm"]
        # GEMM: the fixed part (the array phase beats), capped at E_tot
        self.assertEqual(cost.interval(GEMM, "M=8 K=512 N=8", 16524, True, ci, {}), cal["cold"])
        self.assertEqual(cost.interval(GEMM, "M=4 K=256 N=4", 2936, False, ci, {}), cal["warm"])
        self.assertEqual(cost.interval(GEMM, "M=1 K=2 N=1 array_shape_idx=1", 2596, True, ci, {}),
                         min(2596, cal["cold"]))
        # outside the GEMM model: E_tot >> interval_shift
        self.assertEqual(cost.interval(GEMM, "M=1 K=16 N=1 quantization_enable=1", 3000, False, ci, {}), 3000)
        # a copy: the fixed part of its direction's model (+ the cold term), capped at E_tot
        a = cost.CALIBRATION[ci]["models"]["copy L3>L1"][0]
        self.assertEqual(cost.interval(COPY, "size=262144 L3>L1", 8410, False, ci, dict(size=262144)), math.ceil(a))
        self.assertEqual(cost.interval(COPY, "size=262144 L3>L1", 8410, True, ci, dict(size=262144)),
                         math.ceil(a + cost.CALIBRATION[ci]["cold_term"][COPY]))
        self.assertEqual(cost.interval(COPY, "size=64 L3>L1", 50, False, ci, dict(size=64)), 50)
        self.assertEqual(cost.interval(COPY, "size=128 L3>L3", 482, True, ci, dict(size=128)), 482)   # no model
        self.assertEqual(cost.interval(ADD, "n=512", 64925, True, ci, dict(num_elements=512)), 32463)
        self.assertIsNone(cost.interval(COPY, "size=64 L3>L1", None, False, ci, dict(size=64)))
        self.assertEqual(cost.interval(GEMM, "M=8 K=512 N=8", 16524, True, None, {}), 16524)

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
        # own estimates (the copy of cluster 1 adds its wait on the L3 port to E_tot)
        self.assertEqual((est[first]["own"], est[second]["own"]), (510, 100))
        self.assertEqual(est[gemm]["own"], 2596)
        self.assertEqual(est[gemm]["e_tot"], 2596 + est[other_cluster]["own"])

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
        # the cold GEMM (E_tot 2596): E_int = the GEMM model's cold fixed part
        e = min(2596, cost.CALIBRATION["hemaia_ci"]["gemm"]["cold"])
        self.assertIn("bingo_test_cfg.wd_type_h[1] = (bingo_test_cfg.wd_type_h[1] & keep) | "
                      f"(BINGO_TASK_TIME_SCALE({2 * e}u) & ~keep);", text)
        self.assertIn("bingo_test_cfg.wd_type_c[1] = (bingo_test_cfg.wd_type_c[1] & keep) | "
                      f"(BINGO_TASK_TIME_SCALE({4 * e}u) & ~keep);", text)
        self.assertNotIn("if (", text)                     # branch-free: same host time
        self.assertIn('__asm__ volatile("" : "+r"(keep));', text)  # GCC keeps the masks
        # the cold 256 B copy (E_tot 510): E_int = the fixed part of the copy model (the
        # transfer beats), capped at E_tot
        a = cost.CALIBRATION["hemaia_ci"]["models"]["copy L3>L1"][0]
        e = min(510, math.ceil(a + cost.CALIBRATION["hemaia_ci"]["cold_term"][COPY]))
        self.assertIn(f"bingo_wd_type_set(2, BINGO_TASK_TIME_SCALE({2 * e}u), BINGO_TASK_TIME_SCALE({4 * e}u));", text)
        # a copy without an estimate takes the bound of its type away
        self.copy(graph, 1 << 20)
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

    def test_cost_bucket_classes_and_table(self):
        graph = self.graph()
        first, warm = self.copy(graph, 256), self.copy(graph, 256)     # 510 cold, 100 warm
        graph.bingo_add_edge(first, warm)
        gemm = self.gemm(graph)                                          # 2596 cold
        unknown = self.copy(graph, 1 << 20)                             # no estimate
        graph.bingo_add_edge(warm, unknown)
        classes = graph._wd_classes()
        # type 2: the kernel without estimate first (class 1), then the buckets
        self.assertEqual(classes[unknown], 1)
        self.assertEqual(sorted({classes[first], classes[warm]}), [2, 3])
        self.assertEqual(classes[gemm], 1)                               # type 1: one bucket
        table = graph._task_time_table
        self.assertEqual(table[(2, classes[warm])], (100, 0))
        self.assertEqual(table[(2, classes[first])], (510, 0))
        self.assertEqual(table[(1, 1)], (2596, 0))
        self.assertNotIn((2, 1), table)                                  # learns as before
        stream = io.StringIO()
        graph._emit_task_time_launch(stream, 0)
        text = stream.getvalue()
        self.assertIn(f"bingo_wd_est_set(1, 1, bingo_task_time_entry(0x{cost.entry(2596, 0):03x}u));", text)
        self.assertIn(f"bingo_wd_est_set(2, {classes[first]}, bingo_task_time_entry("
                      f"0x{cost.entry(1020, 0):03x}u));", text)          # PM off: twice
        self.assertEqual(text.count("bingo_wd_est_set("), 2 * len(table))

    def test_buckets_cover_e_int_with_the_shift(self):
        graph = self.graph()
        add = BingoNode(0, 0, 1, kernel_name=ADD, kernel_args=SnaxBingoKernelInt32AddArgs(
            BingoMemAlloc("a", 2048), BingoMemAlloc("b", 2048), BingoMemAlloc("c", 2048), 512))
        graph.bingo_add_node(add)
        graph._wd_classes()
        e_tot, s = graph._task_time_table[(2, 1)]
        self.assertEqual((e_tot, s), (64925, 1))
        self.assertGreaterEqual(cost.decode(cost.encode(e_tot)) >> s, -(-64925 // 2))

    def test_small_and_large_copies_get_their_own_classes(self):
        """1 KiB, 16 KiB and 256 KiB copies in one chain on one core: each size has an
        estimate and a bucket of its own, so a learned small copy never sets the bound
        of a large one (with one class per kernel it would: test below)."""
        graph = self.graph()
        small, medium, large = self.copy(graph, 1024), self.copy(graph, 16384), self.copy(graph, 262144)
        graph.bingo_add_edge(small, medium)
        graph.bingo_add_edge(medium, large)
        est = graph._task_time_estimates()
        self.assertEqual(est[large]["e_tot"], est[large]["own"])          # alone: no wait
        a = cost.CALIBRATION["hemaia_ci"]["models"]["copy L3>L1"][0]
        self.assertEqual(est[large]["e_int"], math.ceil(a))              # the transfer beats
        classes = graph._wd_classes()
        self.assertEqual(len({classes[small], classes[medium], classes[large]}), 3)
        table = graph._task_time_table
        self.assertEqual(table[(2, classes[large])], (est[large]["e_tot"], 3))
        self.assertEqual(table[(2, classes[small])][0], 479)             # the first copy is cold

    def test_large_gemm_bucket_shift_covers_the_fixed_part(self):
        """A large GEMM's E_int is the fixed part, far below E_tot: the bucket keeps a
        shift S > 0 that still covers it."""
        graph = self.graph()
        node = BingoNode(0, 0, 0, kernel_name=GEMM, kernel_args=SnaxBingoKernelGemmFullArgs(
            0, 0, 0, 0, 8, 1024, 8, 0, 0, 0, 0))
        graph.bingo_add_node(node)
        est = graph._task_time_estimates()[node]
        cal = cost.CALIBRATION["hemaia_ci"]["gemm"]
        self.assertEqual(est["source"], "gemm model (cold)")
        self.assertEqual(est["e_tot"], math.ceil(cal["cold"] + cost.GEMM_TICKS_PER_CYCLE * 8 * 8 * 1024))
        self.assertEqual(est["e_int"], cal["cold"])
        graph._wd_classes()
        e_tot, shift = graph._task_time_table[(1, 1)]
        self.assertGreater(shift, 0)
        self.assertGreaterEqual(cost.decode(cost.encode(e_tot)) >> shift, cal["cold"])

    def test_classes_unchanged_without_task_time(self):
        graph = self.graph(task_time=False)
        nodes = [self.copy(graph, 256), self.copy(graph, 1024), self.gemm(graph)]
        self.assertEqual([graph._wd_classes()[n] for n in nodes], [1, 1, 1])

    def test_test_image_entry_function(self):
        graph = self.graph()
        self.copy(graph, 256)
        stream = io.StringIO()
        graph._emit_headers(stream, [])
        text = stream.getvalue()
        self.assertIn("static inline uint32_t bingo_task_time_entry(uint32_t entry) {", text)
        self.assertIn("#define bingo_task_time_entry(entry) (entry)", text)
        body = text.split("static inline uint32_t bingo_task_time_entry", 1)[1].split("}", 1)[0]
        self.assertNotIn("if", body.replace("shift", ""))               # branch-free

    def test_test_image_entry_values(self):
        """The generated C function: [3:0] divides by 2^n (the exponent saturates at 0),
        [4] writes no estimate."""
        import shutil, subprocess
        cc = shutil.which("cc") or shutil.which("gcc")
        if cc is None:
            self.skipTest("no C compiler")
        graph = self.graph()
        self.copy(graph, 256)
        stream = io.StringIO()
        graph._emit_headers(stream, [])
        body = "static inline uint32_t bingo_task_time_entry" + stream.getvalue().split(
            "static inline uint32_t bingo_task_time_entry", 1)[1].split("}\n", 1)[0] + "}\n"
        source = ("#include <stdint.h>\n#include <stdio.h>\nstruct { uint32_t task_time; } bingo_test_cfg;\n"
                  + body + "int main(void) { uint32_t e[] = {0x0a3, 0x2ff, 0x011, 0x1b4};\n"
                  "for (uint32_t w = 0; w < 32; w++) for (int i = 0; i < 4; i++) {\n"
                  "bingo_test_cfg.task_time = w; printf(\"%u %u %u\\n\", w, e[i], bingo_task_time_entry(e[i])); }\n"
                  "return 0; }\n")
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "t.c").write_text(source)
            subprocess.run([cc, "-O2", "-o", str(Path(tmp, "t")), str(Path(tmp, "t.c"))], check=True)
            rows = [tuple(map(int, line.split())) for line in
                    subprocess.run([str(Path(tmp, "t"))], check=True, capture_output=True, text=True).stdout.split("\n")
                    if line]
        for word, entry, out in rows:
            shift, off = word & 15, word >> 4 & 1
            e = max(((entry >> 4) & 15) - shift, 0) if shift <= ((entry >> 4) & 15) else 0
            expected = 0 if off else (entry & 0x30F) | (e << 4)
            self.assertEqual(out, expected, (word, hex(entry)))
            if not off and shift <= ((entry >> 4) & 15):
                self.assertEqual(cost.decode(out & 0xFF), cost.decode(entry & 0xFF) >> shift)

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

    def test_shared_levels(self):
        self.assertEqual(cost.shared_levels(COPY, "size=1024 L3>L1"), {"L3"})
        self.assertEqual(cost.shared_levels(COPY, "size=1024 L1>L3"), {"L3"})
        self.assertEqual(cost.shared_levels(COPY, "size=64 L2>L3"), {"L2", "L3"})
        self.assertEqual(cost.shared_levels(COPY, "size=64 L1>L1r"), frozenset())
        self.assertEqual(cost.shared_levels(COPY, ""), frozenset())
        self.assertEqual(cost.shared_levels(GEMM, "M=1 K=16 N=1"), frozenset())

    def chains(self, graph, sizes, clusters):
        out = []
        for cluster in clusters:
            chain = [self.copy(graph, size, cluster) for size in sizes]
            for a, b in zip(chain, chain[1:]):
                graph.bingo_add_edge(a, b)
            out.append(chain)
        return out

    def test_copies_wait_for_the_other_clusters_copies_on_the_l3_port(self):
        """The small-then-large chains of both clusters (dma_small_large_2cluster): a
        copy may wait for every copy of the other cluster's chain."""
        graph = self.graph()
        chains = self.chains(graph, (1024, 16384, 262144), (0, 1))
        est = graph._task_time_estimates()
        self.assertEqual(est[chains[0][0]]["own"], 479)                 # the cold 1 KiB copy
        a = cost.CALIBRATION["hemaia_ci"]["models"]["copy L3>L1"][0]
        for cluster in (0, 1):
            others = sum(est[node]["own"] for node in chains[1 - cluster])
            for node in chains[cluster]:
                e = est[node]
                fixed = math.ceil(a + (cost.CALIBRATION["hemaia_ci"]["cold_term"][COPY] if e["cold"] else 0))
                self.assertEqual(e["wait"], others)
                self.assertEqual(e["e_tot"], e["own"] + others)
                self.assertEqual(e["e_int"], min(fixed, e["own"]) + others)  # a code fetch may wait too
                self.assertIn("shared port", e["source"])
        # the table entry (the budget) of the small copy's class covers its wait
        classes = graph._wd_classes()
        e_tot, s = graph._task_time_table[(2, classes[chains[0][0]])]
        self.assertGreaterEqual(e_tot, est[chains[0][0]]["e_tot"])
        self.assertGreaterEqual(cost.decode(cost.encode(e_tot)) >> s, est[chains[0][0]]["e_int"])

    def test_ordered_tasks_and_l1_copies_do_not_wait(self):
        """An ordered pair does not wait, and a copy between L1s keeps nobody waiting;
        any other task waits (its code comes from the L3)."""
        graph = self.graph()
        first, second = self.copy(graph, 262144, 0), self.copy(graph, 1024, 1)
        graph.bingo_add_edge(first, second)
        local = BingoNode(0, 1, 1, kernel_name=COPY, kernel_args=SnaxBingoKernelIdma1dCopyArgs(
            BingoMemAlloc("l1a", 256, "L1", 0, 0), BingoMemAlloc("l1b", 256, "L1", 0, 1), 256))
        graph.bingo_add_node(local)
        gemm = self.gemm(graph, 1)
        est = graph._task_time_estimates()
        self.assertEqual(est[local]["key"], "size=256 L1r>L1")
        self.assertEqual([est[n]["wait"] for n in (first, second)], [0, 0])
        self.assertEqual(est[second]["e_tot"], est[second]["own"])
        self.assertEqual(est[gemm]["wait"], est[first]["own"])
        graph.bingo_add_edge(first, gemm)
        self.assertEqual(graph._task_time_estimates()[gemm]["wait"], 0)

    def test_a_copy_without_estimate_on_the_port_takes_the_estimate_away(self):
        graph = self.graph()
        small, huge = self.copy(graph, 1024, 0), self.copy(graph, 1 << 20, 1)
        est = graph._task_time_estimates()
        self.assertIsNone(est[huge]["e_tot"])                           # beyond the calibration
        self.assertEqual(est[small]["own"], 479)
        self.assertIsNone(est[small]["e_tot"])
        self.assertIsNone(est[small]["e_int"])

    def test_four_clusters_wait_for_the_copies_of_every_other_cluster(self):
        types = {(cluster, core): kind for cluster in range(4) for core, kind in ((0, 1), (1, 2), (2, 0))}
        with patch.dict(os.environ, {"BINGO_TASK_TIME": "1"}, clear=False):
            os.environ.pop("BINGO_PLATFORM_CFG", None)
            graph = BingoDFG(1, 4, 2, True, [0], core_type_ids=types)
        graph._task_time_platform = "hemaia_ci_4cluster"
        chains = self.chains(graph, (512, 512), range(4))
        est = graph._task_time_estimates()
        for cluster in range(4):
            node = chains[cluster][1]
            others = sum(est[n]["own"] for c in range(4) if c != cluster for n in chains[c])
            self.assertEqual(est[node]["e_tot"], est[node]["own"] + others)


if __name__ == "__main__":
    unittest.main()
