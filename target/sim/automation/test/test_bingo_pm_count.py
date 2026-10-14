"""Unit tests of bingo_pm_count (the check of the power-domain counters)."""
import unittest

from bingo_pm_count import clock_period, level_class, pm_count_problems, read_time

P = 28000
LEVELS = "[BINGO_PM_COUNT] levels idle=25 normal=6 boost=3\n"


def log_of(events, eoc, extra=""):
    lines = [f"# [BINGO_PM] {t} domain={d} level={l}" for t, d, l in events]
    return "\n".join(lines) + "\n" + extra + f"# All chips finished successfully at {eoc}\n"


def uart_of(rows):
    return LEVELS + "".join(f"[BINGO_PM_COUNT] cluster={c} idle={i} normal={n} boost={b} other={o} active={a}\n"
                            for c, (i, n, b, o, a) in enumerate(rows))


class PmCountTests(unittest.TestCase):
    # domain 1: normal 100 cycles, boost 20, idle from then on
    EVENTS = [(5000, 1, 6), (5000 + 100 * P, 1, 3), (5000 + 120 * P, 1, 25)]
    DISPATCH = (f"# [BINGO_DISPATCH] {5000 + 10 * P} chip=0 task=1 core=0 cluster=0\n"
                f"# [BINGO_DONE] {5000 + 40 * P} chip=0 task=1 core=0 cluster=0\n"
                f"# [BINGO_DISPATCH] {5000 + 30 * P} chip=0 task=2 core=1 cluster=0\n"
                f"# [BINGO_DONE] {5000 + 60 * P} chip=0 task=2 core=1 cluster=0\n")

    def test_classes_follow_the_hardware_order(self):
        self.assertEqual(level_class(0, 25, 6, 3), None)
        self.assertEqual(level_class(6, 6, 6, 3), "idle")      # equal levels: idle first
        self.assertEqual(level_class(3, 25, 3, 3), "normal")   # then normal
        self.assertEqual(level_class(12, 25, 6, 0), "other")   # boost 0 is no level
        self.assertEqual(level_class(12, 25, 6, 3), "other")

    def test_period_is_the_grid_of_the_events(self):
        events = [(5000, 1, 6), (5000 + 3 * P, 1, 25), (5000 + 7 * P, 1, 6)]
        self.assertEqual(clock_period(log_of(events, 10 ** 9)), P)

    def test_read_time_is_found_on_the_last_level(self):
        events = [(t, l) for t, _, l in self.EVENTS]
        counts = dict(idle=7, normal=100, boost=20, other=0)
        self.assertEqual(read_time(events, counts, (25, 6, 3), P), 5000 + 127 * P)
        # an exact count of a closed class is required
        self.assertIsNone(read_time(events, dict(counts, normal=99), (25, 6, 3), P))
        # a read during the boost interval
        self.assertEqual(read_time(events, dict(idle=0, normal=100, boost=5, other=0), (25, 6, 3), P),
                         5000 + 105 * P)

    def test_matching_counters_pass(self):
        log = log_of(self.EVENTS, 5000 + 200 * P, self.DISPATCH)
        problems, results = pm_count_problems(log, uart_of([(50, 100, 20, 0, 60)]), 1, 2, P)
        self.assertEqual(problems, [])
        self.assertEqual(results[1]["t_read_ps"], 5000 + 170 * P)
        self.assertEqual(results[1]["busy_union_cycles"], 50)
        self.assertEqual(results[1]["energy_counters"], results[1]["energy_log"])

    def test_wrong_or_late_counters_fail(self):
        log = log_of(self.EVENTS, 5000 + 200 * P, self.DISPATCH)
        problems, _ = pm_count_problems(log, uart_of([(50, 101, 20, 0, 60)]), 1, 2, P)
        self.assertTrue(any("match no read time" in p for p in problems))
        problems, _ = pm_count_problems(log, uart_of([(90, 100, 20, 0, 60)]), 1, 2, P)
        self.assertTrue(any("after the end of the run" in p for p in problems))

    def test_extra_slot_is_not_in_the_domain(self):
        # core 2 is the extra slot of the cluster: its long task is no work of domain 1
        extra = (f"# [BINGO_DISPATCH] {5000 + 61 * P} chip=0 task=9 core=2 cluster=0\n"
                 f"# [BINGO_DONE] {5000 + 160 * P} chip=0 task=9 core=2 cluster=0\n")
        log = log_of(self.EVENTS, 5000 + 200 * P, self.DISPATCH + extra)
        problems, results = pm_count_problems(log, uart_of([(50, 100, 20, 0, 60)]), 1, 2, P)
        self.assertEqual(problems, [])
        self.assertEqual(results[1]["busy_union_cycles"], 50)

    def test_active_outside_its_bounds_fails(self):
        log = log_of(self.EVENTS, 5000 + 200 * P, self.DISPATCH)
        problems, _ = pm_count_problems(log, uart_of([(50, 100, 20, 0, 49)]), 1, 2, P)
        self.assertTrue(any("active 49 outside" in p for p in problems))
        problems, _ = pm_count_problems(log, uart_of([(50, 100, 20, 0, 10 ** 6)]), 1, 2, P)
        self.assertTrue(any("outside" in p for p in problems))

    def test_missing_lines_fail(self):
        log = log_of(self.EVENTS, 5000 + 200 * P)
        problems, _ = pm_count_problems(log, "", 1, 2, P)
        self.assertTrue(problems)
        problems, _ = pm_count_problems(log, uart_of([(50, 100, 20, 0, 60)]), 2, 2, P)
        self.assertTrue(problems)


if __name__ == "__main__":
    unittest.main()
