import json
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
import unittest
from benchmarks.run_reflection_objective import build_dataset, score, summarize


class ObjectiveReflectionTests(unittest.TestCase):
    def test_independent_oracles(self):
        for case in build_dataset()["cases"]:
            family = case["family"]
            expected = case["expected"]
            if family == "code_trace":
                # Execute only benchmark-authored fixed code, never model output.
                namespace = {}
                exec(case["evidence"][0], namespace)
                self.assertEqual(expected, {"acc": namespace["acc"], "output": namespace["output"]})
            elif family == "business_days":
                # For every supplied start (Oct 9-12), eligible days are 13,14,15,16,19.
                self.assertEqual(expected["due"], "2026-10-19")
            elif family == "ledger":
                fee = int((Decimal(expected["gross"]) * Decimal("0.023")).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                self.assertEqual(expected["fee"], fee)

    def test_saved_results_rescore_without_api(self):
        root = Path(__file__).resolve().parents[1] / "test_outputs/reflection_objective_v1"
        if not (root / "summary.json").exists():
            self.skipTest("Run the live experiment first")
        cases = build_dataset()["cases"]
        rows = []
        for case in cases:
            row = json.loads((root / case["id"] / "result.json").read_text(encoding="utf-8"))
            for key, text_key in (("before", "before_answer"), ("after", "after_answer"), ("final", "final_answer")):
                self.assertEqual(row[key], score(case, row[text_key]))
            # Oracle must not be provided in any API message.
            for name in ("baseline", "reflection", "evaluation", "evaluation_swapped"):
                call = json.loads((root / case["id"] / f"{name}.json").read_text(encoding="utf-8"))
                public = json.loads(call["messages"][1]["content"].split("\nOutput strict JSON only.")[0])
                self.assertNotIn("expected", public)
            rows.append(row)
        saved = json.loads((root / "summary.json").read_text(encoding="utf-8"))
        for key, value in summarize(rows).items():
            self.assertEqual(saved[key], value)

    def test_oracle_accepts_gold_and_rejects_missing_or_extra_fields(self):
        cases = build_dataset()["cases"]
        self.assertEqual(len({c["id"] for c in cases}), 24)
        for case in cases:
            self.assertTrue(score(case, json.dumps(case["expected"]))["correct"])
            self.assertFalse(score(case, "{} ")["correct"])
            self.assertFalse(score(case, json.dumps({**case["expected"], "extra": 1}))["correct"])
            self.assertFalse(score(case, "```json\n" + json.dumps(case["expected"]) + "\n```")["correct"])

    def test_boolean_not_accepted_as_integer(self):
        self.assertFalse(score({"expected": {"n": 1}}, '{"n":true}')["correct"])

    def test_wins_ties_losses_and_denominators(self):
        rows = [{"before": {"correct": a}, "after": {"correct": b},
                 "final": {"correct": b}, "swapped_final": {"correct": a}}
                for a, b in [(False, True), (True, True), (True, False), (False, False)]]
        summary = summarize(rows)
        self.assertEqual(summary["pipeline_gain_pp"], 0)
        self.assertEqual(summary["revision_win_rate_all"], .25)
        self.assertEqual(summary["revision_win_rate_decisive"], .5)
        self.assertEqual(summary["ties"], 2)


if __name__ == "__main__":
    unittest.main()
