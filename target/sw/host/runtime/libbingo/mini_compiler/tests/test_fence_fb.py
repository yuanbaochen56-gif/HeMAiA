"""Fence fallback: the cost table, the per-type decision and the generated call."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bingo_dfg import BingoDFG
from bingo_fence_fb import KERNEL_COST_US, fence_fb_report, fence_fb_types, host_is_cheaper
from bingo_kernel_args import (
    HostBingoKernelDummyArgs, SnaxBingoKernelIdma1dCopyArgs, SnaxBingoKernelInt32AddArgs,
)
from bingo_mem_handle import BingoMemAlloc, BingoMemSymbol
from bingo_node import BingoNode
from test_cerf_fallback import compile_graph

TYPES = {(0, 0): 1, (0, 1): 2, (0, 2): 0,
         (1, 0): 1, (1, 1): 2, (1, 2): 0}
ADD, COPY = "__snax_bingo_kernel_int32_add", "__snax_bingo_kernel_idma_1d_copy"


def graph():
    return BingoDFG(1, 2, 2, True, [0], core_type_ids=TYPES)


def consumer(g, node, name="check"):
    check = BingoNode(0, 0, 2, node_name=name, kernel_name="__host_bingo_kernel_dummy",
                      kernel_args=HostBingoKernelDummyArgs(0))
    g.bingo_add_node(check)
    g.bingo_add_edge(node, check)
    return check


def add_node(g, name="add", cluster=0, count=512):
    node = BingoNode(0, cluster, 1, node_name=name, kernel_name=ADD,
                     kernel_args=SnaxBingoKernelInt32AddArgs(
                         BingoMemSymbol(f"a_{name}"), BingoMemSymbol(f"b_{name}"),
                         BingoMemAlloc(f"c_{name}", 4 * count, "L3", chip_id=0), count))
    g.bingo_add_node(node)
    return node


def copy_node(g, name="copy", cluster=0, size=256):
    node = BingoNode(0, cluster, 1, node_name=name, kernel_name=COPY,
                     kernel_args=SnaxBingoKernelIdma1dCopyArgs(
                         BingoMemSymbol(f"source_{name}"),
                         BingoMemAlloc(f"output_{name}", size, "L3", chip_id=0), size))
    g.bingo_add_node(node)
    return node


class FenceFallbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    def compile(self, g):
        return compile_graph(g, self.directory.name)

    def decision(self, g):
        text = self.compile(g)
        return fence_fb_types(g, 0), fence_fb_report(g, 0), text

    def test_cost_table_holds_the_measured_points(self):
        self.assertEqual(KERNEL_COST_US[ADD], ("num_elements", {512: (1817.088, 2290.400, 287.112),
                                                                4096: (14461.496, 18248.888, 2246.440)}))
        self.assertEqual(KERNEL_COST_US[COPY], ("size", {256: (2.744, 9.632, 10.668)}))
        g = graph()
        self.assertEqual(host_is_cheaper(add_node(g)), "")
        self.assertEqual(host_is_cheaper(add_node(g, "large", count=4096)), "")
        self.assertIn("not cheaper", host_is_cheaper(copy_node(g)))
        self.assertIn("measured at num_elements 512, 4096, not 16", host_is_cheaper(add_node(g, "small", count=16)))
        self.assertIn("measured at size 256, not 64", host_is_cheaper(copy_node(g, "c64", size=64)))

    def test_int32_add_with_a_backup_selects_its_type(self):
        g = graph()
        add = add_node(g)
        consumer(g, add)
        g.bingo_add_host_fallback(add)
        types, report, text = self.decision(g)
        self.assertEqual(types, [2])
        self.assertEqual(report, [dict(chip=0, core_type=2, enable=True, reasons=[], kernels=[ADD])])
        self.assertIn('#if defined(BINGO_FENCE_FB) && BINGO_TEST_CFG\n#include "bingo_test_cfg.h"\n#endif\n',
                      text)
        block = text[text.index("#ifdef BINGO_FENCE_FB\n"):text.index("        bingo_hw_scheduler_init(")]
        self.assertEqual(block.splitlines()[2:], [
            '        _Static_assert(BINGO_CORE_TYPE_ID(0, 1) == 2, "Fence fallback core type mismatch");',
            "#if BINGO_TEST_CFG",
            "        bingo_fence_fb_set(((1u << BINGO_CORE_TYPE_ID(0, 1))) & bingo_test_cfg.fence_fb);",
            "#else",
            "        bingo_fence_fb_set((1u << BINGO_CORE_TYPE_ID(0, 1)));",
            "#endif", "#endif", ""])
        # After the degradation table, before the first task can be dispatched
        self.assertLess(text.index("bingo_cerf_fb_enable("), text.index("bingo_fence_fb_set("))
        self.assertEqual(text.count("bingo_fence_fb_set("), 2)

    def test_large_int32_add_with_a_backup_selects_its_type(self):
        g = graph()
        add = add_node(g, count=4096)
        consumer(g, add)
        g.bingo_add_host_fallback(add)
        types, report, text = self.decision(g)
        self.assertEqual(types, [2])
        self.assertEqual(report, [dict(chip=0, core_type=2, enable=True, reasons=[], kernels=[ADD])])
        self.assertEqual(text.count("bingo_fence_fb_set("), 2)

    def test_adds_on_both_clusters_select_the_type_once(self):
        g = graph()
        for cluster in (0, 1):
            consumer(g, add_node(g, f"add{cluster}", cluster), f"check{cluster}")
        g.bingo_auto_host_fallback(0, 2)
        types, report, text = self.decision(g)
        self.assertEqual(types, [2])
        self.assertEqual(text.count("bingo_fence_fb_set("), 2)

    def test_copy_with_a_backup_keeps_the_substitute(self):
        for size in (256, 64, 512):
            with self.subTest(size=size):
                g = graph()
                copy = copy_node(g, size=size)
                consumer(g, copy)
                g.bingo_add_host_fallback(copy)
                types, report, text = self.decision(g)
                self.assertEqual(types, [])
                self.assertFalse(report[0]["enable"])
                self.assertIn("not cheaper" if size == 256 else "measured at size 256",
                              report[0]["reasons"][0])
                self.assertNotIn("bingo_fence_fb", text)
                self.assertNotIn("BINGO_FENCE_FB", text)

    def test_copy_chain_with_automatic_backups_keeps_the_substitute(self):
        g = graph()
        previous = None
        for index in range(3):
            copy = copy_node(g, f"copy{index}")
            if previous is not None:
                g.bingo_add_edge(previous, copy)
            previous = copy
        consumer(g, previous)
        self.assertEqual(len(g.bingo_auto_host_fallback(0, 2)), 3)
        types, report, text = self.decision(g)
        self.assertEqual(types, [])
        self.assertEqual(len(report[0]["reasons"]), 3)
        self.assertNotIn("bingo_fence_fb", text)

    def test_one_copy_keeps_a_mixed_type_on_the_substitute(self):
        g = graph()
        add = add_node(g)
        copy = copy_node(g, cluster=1)
        consumer(g, add, "check_add")
        consumer(g, copy, "check_copy")
        g.bingo_auto_host_fallback(0, 2)
        types, report, text = self.decision(g)
        self.assertEqual(types, [])
        self.assertEqual(len(report[0]["reasons"]), 1)
        self.assertIn(COPY, report[0]["reasons"][0])
        self.assertNotIn("bingo_fence_fb", text)

    def test_add_at_another_size_keeps_the_substitute(self):
        g = graph()
        add = add_node(g, count=16)
        consumer(g, add)
        g.bingo_add_host_fallback(add)
        types, report, text = self.decision(g)
        self.assertEqual(types, [])
        self.assertIn("measured at num_elements 512, 4096, not 16", report[0]["reasons"][0])
        self.assertNotIn("bingo_fence_fb", text)

    def test_a_task_without_a_backup_blocks_its_type(self):
        g = graph()
        consumer(g, add_node(g))
        types, report, text = self.decision(g)
        self.assertEqual(types, [])
        self.assertEqual(report[0]["reasons"], ["no CERF degradation entry", "add: no host backup"])
        self.assertNotIn("bingo_fence_fb", text)

        g = graph()
        protected = add_node(g, "protected")
        consumer(g, protected, "check_protected")
        g.bingo_add_host_fallback(protected)
        consumer(g, add_node(g, "open", cluster=1), "check_open")
        with self.assertWarns(UserWarning):
            types, report, text = self.decision(g)
        self.assertEqual(types, [])
        self.assertEqual(report[0]["reasons"], ["open: no host backup"])
        self.assertNotIn("bingo_fence_fb", text)

    def test_other_types_are_left_alone(self):
        g = graph()
        add = add_node(g)
        consumer(g, add)
        g.bingo_add_host_fallback(add)
        other = BingoNode(0, 0, 0, node_name="acc", kernel_name="__snax_bingo_kernel_dummy")
        g.bingo_add_node(other)
        types, report, _ = self.decision(g)
        self.assertEqual(types, [2])
        self.assertEqual([(row["core_type"], row["enable"]) for row in report], [(1, False), (2, True)])
        self.assertEqual(report[0]["reasons"], ["no CERF degradation entry", "acc: no host backup"])

    def test_generated_call_writes_the_compiler_mask_narrowed_by_the_test_word(self):
        g = graph()
        add = add_node(g)
        consumer(g, add)
        g.bingo_add_host_fallback(add)
        text = self.compile(g)
        block = text[text.index("#ifdef BINGO_FENCE_FB\n"):text.index("        bingo_hw_scheduler_init(")]
        source = Path(self.directory.name) / "fence.c"
        source.write_text(
            "#include <stdint.h>\n#include <stdio.h>\n"
            "#define BINGO_CORE_TYPE_ID(cluster, core) ((core) == 1 ? 2 : (core) == 0 ? 1 : 0)\n"
            "static uint32_t written = 0xdeadbeefu;\n"
            "static void bingo_fence_fb_set(uint32_t mask) { written = mask; }\n"
            "#if BINGO_TEST_CFG\nstatic struct { uint32_t fence_fb; } bingo_test_cfg = { WORD };\n#endif\n"
            "int main(void) {\n" + block + '    printf("%x\\n", written);\n    return 0;\n}\n')
        exe = Path(self.directory.name) / "fence"

        def run(*flags):
            subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror", *flags,
                            str(source), "-o", str(exe)], check=True, capture_output=True)
            return subprocess.run([str(exe)], check=True, capture_output=True, text=True).stdout.strip()

        self.assertEqual(run("-DBINGO_FENCE_FB", "-DBINGO_TEST_CFG=1", "-DWORD=4"), "4")
        self.assertEqual(run("-DBINGO_FENCE_FB", "-DBINGO_TEST_CFG=1", "-DWORD=0"), "0")
        self.assertEqual(run("-DBINGO_FENCE_FB", "-DBINGO_TEST_CFG=1", "-DWORD=0xfffb"), "0")
        self.assertEqual(run("-DBINGO_FENCE_FB", "-DBINGO_TEST_CFG=1", "-DWORD=0xffff"), "4")
        self.assertEqual(run("-DBINGO_FENCE_FB", "-DBINGO_TEST_CFG=0"), "4")
        # Without the image flag the register is never written
        self.assertEqual(run("-DBINGO_TEST_CFG=1", "-DWORD=4", "-Wno-unused-function",
                             "-Wno-unused-variable"), "deadbeef")
        self.assertEqual(run("-DBINGO_TEST_CFG=0", "-Wno-unused-function"), "deadbeef")


if __name__ == "__main__":
    unittest.main()
