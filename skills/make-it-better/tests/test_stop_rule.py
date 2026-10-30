import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))
from stop_rule import auto_decision, decide


def row(n, total, low=False):
    return {"pass": n, "total": total, "sha": str(n),
            "dims": {"clarity": {"score": 2 if low else 12, "max": 15}, "design": {"score": 15, "max": 20}}}


def rows(*totals):
    return [row(i, t) for i, t in enumerate(totals)]


class Defaults(unittest.TestCase):
    def test_default_runs_all_five(self):
        for i in range(4):
            self.assertEqual(decide(rows(50, 50, 50, 50, 50), i, {})[0], "continue")
        self.assertEqual(decide(rows(50), 5, {})[0], "iterations_reached")

    def test_default_ignores_flat_scores(self):
        self.assertEqual(decide(rows(50, 70, 70, 70), 3, {})[0], "continue")

    def test_default_ignores_target(self):
        self.assertEqual(decide(rows(50, 95), 1, {})[0], "continue")

    def test_default_ignores_no_change(self):
        self.assertEqual(decide(rows(50), 1, {}, no_change=True)[0], "continue")

    def test_higher_iteration_count(self):
        self.assertEqual(decide(rows(50), 5, {"iterations": 8})[0], "continue")
        self.assertEqual(decide(rows(50), 8, {"iterations": 8})[0], "iterations_reached")


class AutoStop(unittest.TestCase):
    on = {"auto_stop": True}

    def test_two_flat_passes_stop(self):
        self.assertEqual(decide(rows(50, 70, 71, 70.5), 3, self.on)[0], "diminishing_returns")

    def test_one_flat_pass_continues(self):
        self.assertEqual(decide(rows(50, 70, 70.5), 2, self.on)[0], "continue")

    def test_gain_at_threshold_continues(self):
        self.assertEqual(auto_decision(rows(50, 70, 71, 72))[0], "continue")

    def test_target(self):
        self.assertEqual(decide(rows(60, 80), 1, self.on)[0], "target_met")

    def test_weak_area_blocks_target(self):
        self.assertEqual(decide([row(0, 60), row(1, 85, low=True)], 1, self.on)[0], "continue")

    def test_regression(self):
        self.assertEqual(decide(rows(50, 70, 60, 61), 3, self.on)[0], "regressed")

    def test_no_change(self):
        self.assertEqual(decide(rows(50), 1, self.on, no_change=True)[0], "no_change")

    def test_cap_still_applies(self):
        self.assertEqual(decide(rows(50, 55, 60, 65, 70, 75), 5, self.on)[0], "iterations_reached")


if __name__ == "__main__":
    unittest.main()
