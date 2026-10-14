import json
from pathlib import Path
import unittest

from benchmarks.run_recovery_ab import dataset, summarize


class RecoveryBenchmarkAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parents[1] / "test_outputs/recovery_ab_v1"
        if not (root / "current.json").exists():
            raise unittest.SkipTest("Run benchmarks/run_recovery_ab.py first")
        cls.results = {mode: json.loads((root / f"{mode}.json").read_text(encoding="utf-8"))["rows"]
                       for mode in ("legacy", "current", "current_restart")}

    def test_identical_cases_and_independent_ground_truth(self):
        cases = {c["id"]: c for c in dataset()["cases"]}
        self.assertEqual(len(cases), 54)
        for rows in self.results.values():
            self.assertEqual({r["id"] for r in rows}, set(cases))
            for row in rows:
                self.assertEqual(row["task_success"], row["actual"] == {"result": cases[row["id"]]["expected"]})

    def test_recompute_duplicate_metrics_from_trace(self):
        for rows in self.results.values():
            for row in rows:
                seen, successful = set(), set()
                repeats = redundant = 0
                for call in row["trace"]:
                    signature = json.dumps([call["tool"], call["args"]], sort_keys=True)
                    repeats += signature in seen
                    redundant += signature in successful
                    seen.add(signature)
                    if call["success"]:
                        successful.add(signature)
                self.assertEqual(row["repeated_calls"], repeats)
                self.assertEqual(row["repeat_after_success"], redundant)
                self.assertEqual(row["repeat_after_failure"], repeats - redundant)

    def test_denominator_excludes_controls_but_includes_persistent_faults(self):
        for rows in self.results.values():
            metrics = summarize(rows)
            self.assertEqual(metrics["fault_tasks"], 48)
            self.assertTrue(all(r["task_success"] for r in rows if r["family"] == "clean"))
            self.assertTrue(all(not r["task_success"] for r in rows if r["family"] == "wrong_result"))

    def test_checkpoint_saved_success_is_reused_and_gaps_remain_visible(self):
        for row in self.results["current"]:
            if row["family"] in ("crash_after_first", "crash_after_second"):
                self.assertTrue(row["task_success"])
                self.assertEqual(row["repeat_after_success"], 0)
                self.assertTrue(row["checkpoint_evidence"][0]["completed_steps"])
            if row["family"] in ("crash_before_checkpoint", "mid_node_timeout"):
                self.assertEqual(row["repeat_after_success"], 1)


if __name__ == "__main__":
    unittest.main()
