"""Exit marking uses the first reserved descriptor bit, without shifting fields."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bingo_dfg import BingoDFG
from bingo_node import BingoNode


class ExitDescriptorTests(unittest.TestCase):
    def graph(self, clusters=2):
        return BingoDFG(1, clusters, 2, True, [0])

    def node(self, kernel):
        node = BingoNode(0, 0, 0, kernel_name=kernel)
        node.non_idempotent = False
        return node

    def test_only_exact_device_exit_kernel_is_marked(self):
        graph = self.graph()
        for kernel in ("__snax_bingo_kernel_exit", "__host_bingo_kernel_exit",
                       "__snax_bingo_kernel_dummy", "__snax_bingo_kernel_exit_extra", ""):
            with self.subTest(kernel=kernel):
                fields = graph.bingo_unpack_node(graph.bingo_pack_node(self.node(kernel)))
                self.assertEqual(fields["is_exit"], int(kernel == "__snax_bingo_kernel_exit"))

    def test_ci_layout_uses_bit_58_and_preserves_low_fields(self):
        graph = self.graph()
        node = self.node("__snax_bingo_kernel_dummy")
        node.node_id = 4095
        node.cond_exec_en = True
        node.cond_exec_group_id = 31
        node.cond_exec_invert = True
        node.assigned_cluster_id = 1
        node.assigned_core_id = 1
        node.dep_check_enable = True
        node.dep_check_list = [0, 2]
        node.dep_check_tag = 15
        node.dep_set_enable = True
        node.remote_dep_set_all = True
        node.dep_set_chiplet_id = 255
        node.dep_set_cluster_id = 1
        node.dep_set_list = [1, 2]
        node.dep_set_tag = 15
        before = graph.bingo_pack_node(node)
        old = graph.bingo_unpack_node(before)
        node.kernel_name = "__snax_bingo_kernel_exit"
        after = graph.bingo_pack_node(node)
        fields = graph.bingo_unpack_node(after)
        self.assertEqual(before ^ after, 1 << 58)
        self.assertEqual(after >> 59, 0)
        self.assertEqual(fields.pop("is_exit"), 1)
        old.pop("is_exit")
        self.assertEqual(fields, old)
        self.assertEqual(graph.bingo_pack_node(node), after)

    def test_exit_bit_tracks_cluster_dimension(self):
        for clusters, shift in ((1, 58), (2, 58), (4, 60)):
            with self.subTest(clusters=clusters):
                graph = self.graph(clusters)
                value = graph.bingo_pack_node(self.node("__snax_bingo_kernel_exit"))
                self.assertEqual(value, 1 << shift)
                self.assertEqual(graph.bingo_unpack_node(value)["is_exit"], 1)


if __name__ == "__main__":
    unittest.main()
