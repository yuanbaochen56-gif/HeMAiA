"""A7 type/kernel classes, optional reserved bits and default constant folding."""
import io
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
import warnings
from contextlib import redirect_stdout
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bingo_dfg import BingoDFG
from bingo_node import BingoNode


class AdaptiveClassTests(unittest.TestCase):
    def graph(self, clusters=2, cores=3, chips=1):
        return BingoDFG(chips, clusters, cores, False, list(range(chips)),
            core_type_ids={(cl, c): c + 1 for cl in range(clusters) for c in range(cores)})

    def add(self, graph, kernel, cluster=0, core=0, chip=0, **kwargs):
        node = BingoNode(chip, cluster, core, kernel_name=kernel)
        node.non_idempotent = False
        graph.bingo_add_node(node, **kwargs)
        return node

    def test_first_occurrence_per_chip_and_type_not_per_slot(self):
        graph = self.graph(chips=2)
        nodes = [self.add(graph, "__snax_a"), self.add(graph, "__snax_b"),
                 self.add(graph, "__snax_a", cluster=1),
                 self.add(graph, "__snax_b", core=1),
                 self.add(graph, "__snax_a", chip=1)]
        self.assertEqual([graph._wd_classes()[n] for n in nodes], [1, 2, 1, 1, 1])

    def test_overflow_is_zero_and_warns_once_per_group(self):
        graph = self.graph()
        nodes = [self.add(graph, f"__snax_{i}") for i in range(9)]
        nodes += [self.add(graph, f"__snax_other_{i}", core=1) for i in range(8)]
        stream, stdout = io.StringIO(), io.StringIO()
        with warnings.catch_warnings(record=True) as caught, redirect_stdout(stdout):
            warnings.simplefilter("always")
            first, second = graph._wd_classes(), graph._wd_classes()
            graph._emit_headers(stream, [])
        self.assertEqual([first[n] for n in nodes[:9]], list(range(1, 8)) + [0, 0])
        self.assertEqual([first[n] for n in nodes[9:]], list(range(1, 8)) + [0])
        self.assertEqual(first, second)
        self.assertEqual(caught, [])
        self.assertEqual(stdout.getvalue(), "")
        header = stream.getvalue()
        self.assertIn('#ifdef BINGO_WD_ADAPT\n#warning "A7: chiplet/type (0, 1) '
                      'has more than 7 kernels; class 0 for __snax_7, __snax_8"\n', header)
        self.assertIn('#warning "A7: chiplet/type (0, 2) has more than 7 kernels; '
                      'class 0 for __snax_other_7"\n#endif', header)
        self.assertEqual(header.count('#warning "A7:'), 2)
        self.assertNotIn('#error "A7', header)

    def test_task_override_and_opt_out(self):
        graph = self.graph()
        off = self.add(graph, "__snax_a", wd_class=0)
        custom = self.add(graph, "__snax_b", wd_class=7)
        self.assertEqual(graph._wd_classes(), {off: 0, custom: 7})
        self.assertEqual(graph.bingo_unpack_node(graph.bingo_pack_node(custom, wd_adapt=True))["wd_class"], 7)
        for bad in (-1, 8, True, "1"):
            with self.assertRaises(ValueError):
                self.add(graph, "__snax_bad", wd_class=bad)

    def test_optional_class_five_core_capacity_warning(self):
        graph = self.graph(clusters=1, cores=5)
        node = self.add(graph, "__snax_a", wd_class=7)
        self.assertEqual(graph._wd_class_shift(), 64)
        stream, stdout = io.StringIO(), io.StringIO()
        with warnings.catch_warnings(record=True) as caught, redirect_stdout(stdout):
            warnings.simplefilter("always")
            self.assertEqual(graph.bingo_pack_node(node, wd_adapt=True), graph.bingo_pack_node(node))
            self.assertEqual(graph._wd_classes()[node], 0)
            graph._emit_headers(stream, [])
        self.assertEqual(caught, [])
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn('#ifdef BINGO_WD_ADAPT\n'
                      '#error "A7 needs 3 reserved descriptor bits; this cfg has 0"\n'
                      '#endif\n#define BINGO_WD_CLASS_BITS(c) 0ULL', stream.getvalue())
        self.assertNotIn('#warning "A7', stream.getvalue())

    def test_available_capacity_without_overflow_has_no_a7_diagnostics(self):
        graph = self.graph()
        nodes = [self.add(graph, f"__snax_{i}") for i in range(7)]
        stream, stdout = io.StringIO(), io.StringIO()
        with warnings.catch_warnings(record=True) as caught, redirect_stdout(stdout):
            warnings.simplefilter("always")
            graph._emit_headers(stream, [])
            self.assertEqual([graph._wd_classes()[n] for n in nodes], list(range(1, 8)))
        self.assertEqual(caught, [])
        self.assertEqual(stdout.getvalue(), "")
        self.assertNotIn("#error", stream.getvalue())
        self.assertNotIn("#warning", stream.getvalue())

    def test_ci_and_four_cluster_shift_preserve_legacy_fields(self):
        for clusters, shift in ((2, 59), (4, 61)):
            graph = self.graph(clusters=clusters)
            node = self.add(graph, "__snax_bingo_kernel_exit")
            legacy = graph.bingo_pack_node(node)
            adaptive = graph.bingo_pack_node(node, wd_adapt=True)
            self.assertEqual(graph._wd_class_shift(), shift)
            self.assertEqual(legacy, 1 << (shift - 1))
            self.assertEqual(adaptive, legacy | (1 << shift))
            self.assertEqual(graph.bingo_unpack_node(adaptive)["wd_class"], 1)
            self.assertEqual(graph.bingo_unpack_node(legacy)["wd_class"], 0)

    def test_default_header_macro_folds_to_zero(self):
        for clusters, shift in ((2, 59), (4, 61)):
            graph = self.graph(clusters=clusters)
            node = self.add(graph, "__snax_a", wd_class=7)
            stream = io.StringIO()
            graph._emit_headers(stream, [])
            macros = stream.getvalue().split("\n\n", 1)[1]
            assignment = graph.bingo_emit_task_desc_list().split(" = ")[-1].split(";")[0]
            self.assertIn("ULL | BINGO_WD_CLASS_BITS(7)", assignment)
            source = "#include <stdint.h>\n" + macros
            source += "int main(void) { uint64_t value = 0x123ULL | BINGO_WD_CLASS_BITS(7);\n"
            source += "#ifdef BINGO_WD_ADAPT\n"
            source += f"return value != (0x123ULL | (7ULL << {shift}));\n"
            source += "#else\nreturn value != 0x123ULL;\n#endif\n}\n"
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "macro.c").write_text(source)
                for flags in ([], ["-DBINGO_WD_ADAPT"]):
                    subprocess.run(["cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                                    *flags, str(root / "macro.c"), "-o", str(root / "macro")], check=True)
                    subprocess.run([str(root / "macro")], check=True)
            self.assertEqual(graph.bingo_pack_node(node), 0)

    def test_legacy_generator_uses_real_platform_type_table(self):
        with tempfile.TemporaryDirectory() as directory:
            platform = Path(directory) / "occamy.h"
            platform.write_text("#define N_CHIPLETS 1\n#define CHIPLET_ID_0 0\n"
                "#define N_CLUSTERS_PER_CHIPLET 2\n#define N_CORES_PER_CLUSTER 2\n"
                "#define BINGO_CORE_TYPE_ID(cluster, core) (((cluster) == 0 && (core) == 0) ? 1 : "
                "((cluster) == 0 && (core) == 1) ? 2 : "
                "((cluster) == 1 && (core) == 0) ? 1 : "
                "((cluster) == 1 && (core) == 1) ? 2 : 0)\n")
            with patch.dict(os.environ, {"BINGO_PLATFORM_CFG": str(platform)}):
                graph = BingoDFG(1, 2, 2, True, [0])
                self.assertEqual(graph.core_type_ids, {})
                self.assertEqual(graph._wd_type_ids[(1, 1)], 2)
                self.assertEqual(graph._wd_type_ids[(0, 2)], 0)
                node = self.add(graph, "__snax_a", cluster=1, core=1)
                self.assertEqual(graph._wd_classes()[node], 1)
                explicit = BingoDFG(1, 2, 2, True, [0], core_type_ids={(0, 0): 3})
                self.assertEqual(explicit.core_type_ids, {(0, 0): 3})
                self.assertEqual(explicit._wd_type_ids, {(0, 0): 3})
                empty = BingoDFG(1, 2, 2, True, [0], core_type_ids={})
                self.assertEqual(empty.core_type_ids, {})
                self.assertEqual(empty._wd_type_ids, {})


if __name__ == "__main__":
    unittest.main()
