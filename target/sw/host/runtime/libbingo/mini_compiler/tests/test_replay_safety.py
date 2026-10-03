"""Replay-safety analysis and unchanged descriptor layout, without RTL."""

from pathlib import Path
import sys
import tempfile
import unittest
import warnings

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bingo_dfg import BingoDFG, region
from bingo_kernel_args import (
    SnaxBingoKernelGemmFullArgs, SnaxBingoKernelGemmMinimalArgs,
    SnaxBingoKernelGemmI8I8I32M1K16N32Args,
    SnaxBingoKernelGemmI8I8I8M1K16N32Args,
    SnaxBingoKernelIdma1dCopyArgs, SnaxBingoKernelInt32AddArgs,
)
from bingo_mem_handle import BingoMemAlloc, BingoMemFixedAddr, BingoMemSymbol
from bingo_node import BingoNode
from test_cerf_fallback import compile_graph, fixture

TYPES = {(0, 0): 1, (0, 1): 2, (0, 2): 0,
         (1, 0): 1, (1, 1): 2, (1, 2): 0}


def graph(**kwargs):
    return BingoDFG(1, 2, 2, True, [0], core_type_ids=TYPES, **kwargs)


def task(args=None, kernel=None, core=1):
    return BingoNode(0, 0, core, node_name="task",
                     kernel_name=kernel or getattr(args, "KERNEL_NAME", "") or "",
                     kernel_args=args)


def copy(src, dst):
    return task(SnaxBingoKernelIdma1dCopyArgs(src, dst, 16),
                "__snax_bingo_kernel_idma_1d_copy")


class ReplaySafetyTests(unittest.TestCase):
    def test_bare_address_error_describes_both_checks(self):
        with self.assertRaisesRegex(
                ValueError, "Replay-safety and host-fallback checks need memory handles"):
            region(1234)

    def test_unsafe_replay_warns_once_per_node_even_when_packed_again(self):
        shared = BingoMemAlloc("shared", 32)
        first, second = copy(shared, shared), copy(shared, shared)
        first.non_idempotent = second.non_idempotent = False
        g = graph(allow_unsafe_replay=True)
        with warnings.catch_warnings(record=True) as emitted:
            warnings.simplefilter("always")
            for node in (first, first, second, second, first):
                self.assertFalse(g._node_no_replay(node))
                g.bingo_pack_node(node)
        self.assertEqual(len(emitted), 2)
        self.assertTrue(all("unsafe replay" in str(w.message) for w in emitted))
        with self.assertWarns(UserWarning):
            graph(allow_unsafe_replay=True)._node_no_replay(first)

    def test_add_overlap_either_input_and_disjoint(self):
        shared = BingoMemAlloc("shared", 64)
        other = BingoMemAlloc("other", 64)
        cases = [
            (shared, other, shared, True),
            (other, shared, shared.view(15), True),
            (shared, other, shared.view(16), False),
            (shared, shared, other, False),
            (100, 200, 100, False),
            (BingoMemSymbol("x"), other, BingoMemSymbol("x", 15), True),
            (BingoMemFixedAddr(100), other, BingoMemFixedAddr(116), False),
        ]
        for a, b, c, expected in cases:
            with self.subTest(a=a, b=b, c=c):
                n = task(SnaxBingoKernelInt32AddArgs(a, b, c, 4))
                self.assertEqual(graph()._node_no_replay(n), expected)

    def test_copy_regions_match_emitted_addresses(self):
        shared = BingoMemAlloc("shared", 64, offset=8)
        cases = [
            (shared, shared.view(8), True),
            (shared, shared.view(23), True),
            (shared, shared.view(24), False),
            (shared, BingoMemAlloc("separate", 64), False),
            (BingoMemSymbol("x", 4), BingoMemSymbol("x", 19), True),
            (BingoMemFixedAddr(100), BingoMemFixedAddr(115), True),
            (100, BingoMemFixedAddr(100), False),
        ]
        for src, dst, expected in cases:
            with self.subTest(src=src, dst=dst):
                self.assertEqual(graph()._node_no_replay(copy(src, dst)), expected)

    def test_gemm_full_minimal_and_wrapper_families(self):
        for accum in (0, 1, 2):
            args_list = [
                SnaxBingoKernelGemmFullArgs(0, 0, 0, 0, 1, 1, 1, 1, 0, 0, accum),
                SnaxBingoKernelGemmMinimalArgs(0, 0, 0, 0),
                SnaxBingoKernelGemmI8I8I32M1K16N32Args(
                    0, 0, 0, 0, 1, 1, 1, accumPrevC=accum),
                SnaxBingoKernelGemmI8I8I8M1K16N32Args(
                    0, 0, 0, 0, 1, 1, 1, 0, 1, 0, 0, accumPrevC=accum),
            ]
            args_list[1].accumPrevC = accum
            for args in args_list:
                kernel = getattr(args, "KERNEL_NAME", None) or (
                    "__snax_bingo_kernel_gemm_full" if isinstance(
                        args, SnaxBingoKernelGemmFullArgs) else "__snax_bingo_kernel_gemm_minimal")
                with self.subTest(args=type(args).__name__, accum=accum):
                    self.assertEqual(graph()._node_no_replay(task(args, kernel, 0)), accum != 0)

    def test_unknown_kernel_and_unresolvable_minimal_are_opt_in(self):
        g = graph()
        n = task(SnaxBingoKernelInt32AddArgs(0, 0, 0, 4), "__unknown")
        self.assertFalse(g._node_no_replay(n))
        n.non_idempotent = True
        self.assertTrue(g._node_no_replay(n))
        self.assertFalse(g._node_no_replay(task(
            SnaxBingoKernelGemmMinimalArgs(0, 0, 0, 0),
            "__snax_bingo_kernel_gemm_minimal", 0)))

    def test_override_precedence_and_unsafe_false(self):
        shared = BingoMemAlloc("x", 32)
        n = copy(shared, shared)
        self.assertTrue(graph()._node_no_replay(n))
        n.non_idempotent = True
        self.assertTrue(graph()._node_no_replay(n))
        n.non_idempotent = False
        with self.assertRaisesRegex(ValueError, "unsafe replay"):
            graph()._node_no_replay(n)
        with self.assertWarnsRegex(UserWarning, "unsafe replay"):
            self.assertFalse(graph(allow_unsafe_replay=True)._node_no_replay(n))
        safe = copy(shared, BingoMemAlloc("other", 32))
        safe.non_idempotent = False
        self.assertFalse(graph()._node_no_replay(safe))
        safe.non_idempotent = True
        self.assertTrue(graph()._node_no_replay(safe))

    def test_excluded_nodes_never_marked(self):
        for kind, core, kernel, encoded in [
            ("normal", 2, "__host_bingo_kernel_int32_add", 0),
            ("dummy", 1, "", 1),
            ("gating", 1, "", 2),
            ("entry", 1, "", 0),
            ("exit", 1, "", 0),
            ("normal", 1, "__snax_bingo_kernel_exit", 0),
            ("normal", 1, "__snax_bingo_kernel_entry", 0),
        ]:
            n = task(core=core, kernel=kernel)
            n.node_type = kind
            with self.subTest(kind=kind, core=core, kernel=kernel):
                for override in (None, False):
                    n.non_idempotent = override
                    self.assertEqual(graph().bingo_unpack_node(
                        graph().bingo_pack_node(n))["task_type"], encoded)
                n.non_idempotent = True
                with self.assertRaisesRegex(ValueError, "cannot be non_idempotent"):
                    graph().bingo_pack_node(n)

    def test_type3_round_trip_and_only_two_type_bits_change(self):
        g = BingoDFG(1, 1, 4, True, [0])
        n = task()
        n.node_id = 123
        n.assigned_core_id = 3
        n.dep_check_enable = True
        n.dep_check_list = [1, 4]
        n.dep_check_tag = 15
        n.dep_set_enable = True
        n.dep_set_list = [0, 4]
        n.dep_set_tag = 15
        before = g.bingo_pack_node(n)
        n.non_idempotent = True
        after = g.bingo_pack_node(n)
        self.assertEqual(before ^ after, 3 << 7)
        self.assertLess(after, 1 << 63)
        self.assertTrue(after & (1 << 62))
        fields = g.bingo_unpack_node(after)
        self.assertEqual(fields["task_type"], 3)
        self.assertEqual(fields["node_type"], "normal")
        self.assertTrue(fields["non_idempotent"])
        del fields["task_type"], fields["non_idempotent"]
        old = g.bingo_unpack_node(before)
        del old["task_type"], old["non_idempotent"]
        self.assertEqual(fields, old)
        restored = task()
        restored.node_type = g.bingo_unpack_node(after)["node_type"]
        restored.non_idempotent = g.bingo_unpack_node(after)["non_idempotent"]
        self.assertEqual(g.bingo_unpack_node(g.bingo_pack_node(restored))["task_type"], 3)

    def test_host_fallback_rejects_automatic_and_explicit_marks(self):
        shared = BingoMemAlloc("x", 32)
        for n in (copy(shared, shared), copy(shared, BingoMemAlloc("other", 32))):
            g = graph()
            g.bingo_add_node(n)
            if n.kernel_args.src_addr is not n.kernel_args.dst_addr:
                n.non_idempotent = True
            with self.assertRaisesRegex(ValueError, "non-idempotent"):
                g.bingo_add_host_fallback(n)

    def test_cerf_primary_group_warns_and_still_compiles(self):
        g, primary, _, _ = fixture()
        primary.non_idempotent = True
        with tempfile.TemporaryDirectory() as directory, \
                self.assertWarnsRegex(UserWarning, "backup must not read"):
            output = compile_graph(g, directory)
        self.assertIn("Type=3", output)

    def test_unsafe_header_is_explicit_and_safe_header_unchanged(self):
        for unsafe in (False, True):
            g = graph(allow_unsafe_replay=unsafe)
            n = task()
            g.bingo_add_node(n)
            with tempfile.TemporaryDirectory() as directory, \
                    warnings.catch_warnings():
                warnings.simplefilter("ignore")
                output = compile_graph(g, directory)
            self.assertEqual("TEST ONLY: allow_unsafe_replay=True" in output, unsafe)


if __name__ == "__main__":
    unittest.main()
