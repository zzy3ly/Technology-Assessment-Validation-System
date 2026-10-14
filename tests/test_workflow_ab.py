"""Audit benchmark scoring and saved observations without loading model SDKs."""
import json
from pathlib import Path
import unittest

from benchmarks.run_workflow_ab import build_dataset, metrics


class WorkflowBenchmarkAudit(unittest.TestCase):
    def test_false_success_denominators_and_correct_rejection(self):
        rows = [
            {"task_success": True, "claimed_success": True, "false_success": False, "node_calls": 3},
            {"task_success": False, "claimed_success": True, "false_success": True, "node_calls": 3},
            {"task_success": False, "claimed_success": False, "false_success": False, "node_calls": 2},
        ]
        score = metrics(rows)
        self.assertEqual(score["task_success_rate"], 1 / 3)
        self.assertEqual(score["false_success_rate_all"], 1 / 3)
        self.assertEqual(score["false_success_rate_claimed"], 1 / 2)

    def test_saved_results_match_independent_artifact_oracle(self):
        root = Path(__file__).resolve().parents[1] / "test_outputs/workflow_ab_v1"
        cases = {c["id"]: c for c in build_dataset()["cases"]}
        for mode in ("legacy", "current", "current_no_retry"):
            rows = json.loads((root / f"{mode}.json").read_text(encoding="utf-8"))["rows"]
            self.assertEqual({r["id"] for r in rows}, set(cases))
            for row in rows:
                expected = {"sum": sum(cases[row["id"]]["numbers"])}
                self.assertEqual(row["task_success"], row["artifact"] == expected)
                self.assertEqual(row["false_success"], row["claimed_success"] and row["artifact"] != expected)
            # Both a positive control and a deliberately undetected negative control.
            self.assertTrue(all(r["task_success"] for r in rows if r["family"] == "clean"))
            self.assertTrue(all(r["false_success"] for r in rows if r["family"] == "semantic_wrong"))


if __name__ == "__main__":
    unittest.main()
