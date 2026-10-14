import unittest
from unittest.mock import MagicMock

from sources.recovery import (
    FailureClassifier,
    FailureKind,
    RecoveryAction,
    RepeatedTraceDetector,
    RetryPolicy,
    repair_arguments,
    run_with_backoff,
)


class TestFailureClassifier(unittest.TestCase):
    def test_required_tool_failures(self):
        cases = {
            "request timed out": FailureKind.NETWORK_TIMEOUT,
            "HTTP 429 too many requests": FailureKind.RATE_LIMIT,
            "No search results": FailureKind.EMPTY_RESULT,
            "TypeError: invalid argument": FailureKind.INVALID_ARGUMENT,
            "File not found": FailureKind.FILE_NOT_FOUND,
            "AssertionError: execution failed": FailureKind.CODE_EXECUTION,
            "Sources conflict and are contradictory sources": FailureKind.CONFLICTING_RESULTS,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(FailureClassifier.classify(text).kind, expected)

    def test_required_model_failures(self):
        cases = {
            "JSONDecodeError: invalid JSON": FailureKind.INVALID_JSON,
            "Unknown tool requested": FailureKind.TOOL_HALLUCINATION,
            "Maximum context length exceeded": FailureKind.CONTEXT_OVERFLOW,
            "Unable to answer; requested exit": FailureKind.EARLY_EXIT,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(FailureClassifier.classify(text).kind, expected)

    def test_parameter_repair_does_not_mutate_original(self):
        task = {"id": "1", "agent": "file", "task": "read it", "need": []}
        failure = FailureClassifier.classify("TypeError: invalid parameter")
        repaired = repair_arguments(task, failure)
        self.assertEqual(task["task"], "read it")
        self.assertIn("Validate every tool argument", repaired["task"])
        self.assertEqual(repaired["recovery"]["action"], RecoveryAction.REPAIR_ARGUMENTS.value)

    def test_exponential_backoff_and_retry(self):
        operation = MagicMock(side_effect=[TimeoutError("timed out"), "ok"])
        sleeps = []
        result, failures = run_with_backoff(
            operation,
            RetryPolicy(max_attempts=2, base_delay_seconds=1, jitter_ratio=0),
            sleep=sleeps.append,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(len(failures), 1)
        self.assertEqual(sleeps, [1])

    def test_repeated_trace_detector(self):
        detector = RepeatedTraceDetector(max_repetitions=2)
        signature = detector.signature("web", "search python")
        detector.record(signature); detector.record(signature)
        self.assertTrue(detector.is_repeated(signature))


if __name__ == "__main__":
    unittest.main()
