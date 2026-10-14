"""The descriptor packer rejects values that do not fit their field, and dummies
that neither check nor set (or do both): the hardware does not check them."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bingo_dfg import BingoDFG
from bingo_node import BingoNode


def graph():
    # one chiplet, two clusters, three bingo slots per cluster (two cores and the host's)
    return BingoDFG(1, 2, 2, True, [0])


def node(cluster=0, core=1):
    return BingoNode(0, cluster, core, node_name="task", kernel_name="__snax_bingo_kernel_dummy")


class DescriptorFieldTests(unittest.TestCase):
    def test_valid_node_packs(self):
        n = node()
        n.node_id = 4095
        n.dep_check_enable, n.dep_check_list = True, [0, 2]
        n.dep_set_enable, n.dep_set_list, n.dep_set_cluster_id = True, [1], 1
        graph().bingo_pack_node(n)

    def test_values_beyond_their_field_are_rejected(self):
        cases = [
            ("task id", dict(node_id=4096)),
            ("cluster id", dict(assigned_cluster_id=2)),
            ("core id", dict(assigned_core_id=3)),
            ("dep set cluster id", dict(dep_set_enable=True, dep_set_list=[0], dep_set_cluster_id=2)),
            ("dep check core", dict(dep_check_enable=True, dep_check_list=[3])),
            ("dep set core", dict(dep_set_enable=True, dep_set_list=[5])),
            ("dep check tag", dict(dep_check_tag=16)),
            ("CERF group", dict(cond_exec_group_id=32)),
        ]
        for what, fields in cases:
            with self.subTest(what=what):
                n = node()
                for key, value in fields.items():
                    setattr(n, key, value)
                with self.assertRaisesRegex(ValueError, what):
                    graph().bingo_pack_node(n)

    def test_dummy_checks_or_sets(self):
        for check, sets, ok in ((True, False, True), (False, True, True), (False, False, False), (True, True, False)):
            with self.subTest(check=check, sets=sets):
                n = node()
                n.node_type = "dummy"
                n.dep_check_enable = check
                n.dep_set_enable = sets
                if sets:
                    n.dep_set_list = [0]
                if ok:
                    graph().bingo_pack_node(n)
                else:
                    with self.assertRaisesRegex(ValueError, "either check or set"):
                        graph().bingo_pack_node(n)


if __name__ == "__main__":
    unittest.main()
