"""Stable maximum matching and actual tag emission across Python hash seeds."""
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import unittest

import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bingo_dfg import _deterministic_bipartite_matching


class DeterministicDepTagTests(unittest.TestCase):
    def graph(self, count, edges):
        graph = nx.Graph()
        graph.add_nodes_from(("L", n) for n in range(count))
        graph.add_nodes_from(("R", n) for n in range(count))
        graph.add_edges_from((("L", a), ("R", b)) for a, b in edges)
        return graph

    def test_multiple_solutions_have_a_fixed_ascending_match(self):
        graph = self.graph(4, [(0, 2), (0, 3), (1, 2), (1, 3)])
        match = _deterministic_bipartite_matching(graph, 4)
        self.assertEqual({a[1]: b[1] for a, b in match.items() if a[0] == "L"},
                         {0: 3, 1: 2})

    def test_random_small_dags_have_the_same_minimum_chain_count(self):
        rng = random.Random(921)
        for count in range(16):
            for _ in range(20):
                dag = nx.DiGraph()
                dag.add_nodes_from(range(count))
                dag.add_edges_from((a, b) for a in range(count) for b in range(a + 1, count)
                                   if rng.random() < 0.25)
                graph = self.graph(count, nx.transitive_closure_dag(dag).edges())
                new = _deterministic_bipartite_matching(graph, count)
                old = nx.algorithms.bipartite.hopcroft_karp_matching(
                    graph, top_nodes=[("L", a) for a in range(count)])
                self.assertEqual(count - len(new) // 2, count - len(old) // 2)
                self.assertTrue(all(graph.has_edge(a, b) and new[b] == a for a, b in new.items()))

    def test_actual_multi_solution_workload_is_identical_across_hash_seeds(self):
        tests = Path(__file__).resolve().parent
        source = (
            "import json,sys\n"
            f"sys.path.insert(0, {str(tests)!r})\n"
            "from test_early_exit_workload import EarlyExitWorkloadTests\n"
            "test=EarlyExitWorkloadTests()\n"
            "try:\n"
            " test.setUp(); test.generate()\n"
            " print(json.dumps(test.output))\n"
            "finally: test.doCleanups()\n"
        )
        outputs = []
        for seed in ("0", "1", "2", "random"):
            outputs.append(subprocess.check_output(
                [sys.executable, "-c", source],
                env=dict(os.environ, PYTHONHASHSEED=seed, PYTHONDONTWRITEBYTECODE="1"),
                text=True))
        self.assertTrue(all(output == outputs[0] for output in outputs[1:]))
        self.assertIn("bingo_hw_scheduler_task_desc_list", json.loads(outputs[0]))


if __name__ == "__main__":
    unittest.main()
