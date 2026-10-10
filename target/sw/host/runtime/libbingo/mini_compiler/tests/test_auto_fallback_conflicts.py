"""P7b-f type conflicts must escape the per-node skip path."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_auto_fallback import chain, copy
from test_cerf_fallback import compile_graph, node


def register_group(graph, representative_core=1):
    primary = copy(graph, "group_copy", 1, 7)
    representative = primary
    if representative_core == 0:
        representative = node(graph, "group_other_type", 1, 0, 7)
        graph.bingo_add_edge(primary, representative)
    backup = node(graph, "group_backup", 0, 2, 11)
    graph.bingo_add_cerf_group_fallback(representative, backup)


class AutoFallbackConflictTests(unittest.TestCase):
    def test_group_before_auto_raises_for_representative_and_member_types(self):
        for core in (0, 1):
            with self.subTest(core=core):
                graph, _, _ = chain(1)
                register_group(graph, core)
                with self.assertRaisesRegex(ValueError, "handwritten CERF group"):
                    graph.bingo_auto_host_fallback(0, 2)
                self.assertEqual(graph._auto_host_fallback_types, set())
                self.assertEqual(graph._host_fallbacks, {})

    def test_auto_before_group_raises_at_compile(self):
        graph, _, _ = chain(1)
        graph.bingo_auto_host_fallback(0, 2)
        register_group(graph)
        with tempfile.TemporaryDirectory() as out:
            with self.assertRaisesRegex(ValueError, "handwritten CERF group"):
                compile_graph(graph, out)

    def test_type_conflict_is_not_hidden_by_ineligible_candidates(self):
        for before in (True, False):
            with self.subTest(before=before):
                graph, tasks, _ = chain(1)
                tasks[0].non_idempotent = True
                if before:
                    register_group(graph)
                    with self.assertRaisesRegex(ValueError, "handwritten CERF group"):
                        graph.bingo_auto_host_fallback(0, 2)
                else:
                    self.assertEqual(graph.bingo_auto_host_fallback(0, 2), [])
                    register_group(graph)
                    with tempfile.TemporaryDirectory() as out:
                        with self.assertRaisesRegex(ValueError, "handwritten CERF group"):
                            compile_graph(graph, out)

    def test_individual_ineligibility_still_skips_with_reason(self):
        for change, reason in (
                (lambda n: setattr(n, "cond_exec_en", True), "unconditional"),
                (lambda n: setattr(n, "non_idempotent", True), "non-idempotent"),
                (lambda n: setattr(n, "kernel_name", "unknown"), "whitelisted"),
                (lambda n: setattr(n.kernel_args, "src_addr", 123), "bare"),
                (lambda n: setattr(n.kernel_args, "src_addr", n.kernel_args.dst_addr), "overlap")):
            with self.subTest(reason=reason):
                graph, tasks, _ = chain(1)
                change(tasks[0])
                self.assertEqual(graph.bingo_auto_host_fallback(0, 2), [])
                report, = graph._auto_host_fallback_report
                self.assertFalse(report["selected"])
                self.assertIn(reason, report["reason"])


if __name__ == "__main__":
    unittest.main()
