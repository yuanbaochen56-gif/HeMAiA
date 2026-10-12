import unittest
from s1b_observations import readiness


def node(task, core=2, check=False, cond=False, dummy=False):
    return dict(task=task, cluster=0, core=core, check_en=int(check),
                check_code=4 if check else 0, check_tag=0, type=int(dummy), cond=int(cond))


class ReadinessTests(unittest.TestCase):
    def test_real_tag_and_slot_completion(self):
        rows = [node(10), node(0, check=True)]
        text = ("SCHEMA 1 C=3 CL=2 TAG=4\n0 START\n28 DISPATCH 10 0 2\n"
                "56 DONE 10 0 2\n84 SET 0 2 4 0\n112 CHECK 0 0 2 4 0\n"
                "112 CHECKOUT_RETIRE 10 0 2 0\n140 DISPATCH 0 0 2\n168 DONE 0 0 2\n")
        result = readiness(text, rows, 28)
        self.assertEqual(result["eligibility"]["0"], 84)
        self.assertEqual(result["waits_ps"]["0"], 56)

    def test_skipped_retirement_is_not_done(self):
        rows = [node(10), node(2, core=1, cond=True), node(0, check=True)]
        text = ("SCHEMA 1 C=3 CL=2 TAG=4\n0 START\n28 DISPATCH 10 0 2\n"
                "56 DONE 10 0 2\n84 CHECKOUT_RETIRE 2 0 1 1\n84 SET 0 2 4 0\n"
                "112 CHECK 0 0 2 4 0\n140 DISPATCH 0 0 2\n")
        result = readiness(text, rows, 28)
        self.assertNotIn(2, result["done"])
        self.assertNotIn(2, result["dispatch"])
        self.assertIn(2, result["checkout_retire"])

    def test_late_ingress_is_not_exempt(self):
        rows = [node(10), node(0, check=True)]
        text = ("SCHEMA 1 C=3 CL=2 TAG=4\n0 START\n28 DISPATCH 10 0 2\n"
                "56 DONE 10 0 2\n84 SET 0 2 4 0\n2000096 CHECK 0 0 2 4 0\n"
                "2000124 DISPATCH 0 0 2\n")
        result = readiness(text, rows, 28)
        self.assertEqual(result["eligibility"]["0"], 84)
        self.assertEqual(result["waits_ps"]["0"], 2000040)

    def test_live_tag_reuse_rejected(self):
        with self.assertRaises(AssertionError):
            readiness("SCHEMA 1 C=3 CL=2 TAG=4\n0 START\n28 SET 0 2 4 0\n"
                      "56 SET 0 2 4 0\n", [], 28)

    def test_missing_tag_rejected(self):
        with self.assertRaises(AssertionError):
            readiness("SCHEMA 1 C=3 CL=2 TAG=4\n0 START\n28 CHECK 0 0 2 4 0\n",
                      [node(0, check=True)], 28)

    def test_same_edge_set_cannot_satisfy_check(self):
        with self.assertRaises(AssertionError):
            readiness("SCHEMA 1 C=3 CL=2 TAG=4\n0 START\n28 SET 0 2 4 0\n"
                      "28 CHECK 0 0 2 4 0\n", [node(0, check=True)], 28)

    def test_absolute_clock_phase_is_not_assumed(self):
        result = readiness("SCHEMA 1 C=3 CL=2 TAG=4\n"
                           "7 CLOCK\n35 CLOCK\n63 CLOCK\n91 CLOCK\n", [], 28)
        self.assertEqual(result["clock_edges_ps"], [7, 35, 63, 91])

    def test_actual_clock_period_mismatch_rejected(self):
        with self.assertRaises(AssertionError):
            readiness("SCHEMA 1 C=3 CL=2 TAG=4\n"
                      "7 CLOCK\n35 CLOCK\n64 CLOCK\n91 CLOCK\n", [], 28)


if __name__ == "__main__":
    unittest.main()
