"""Failure classification and deterministic recovery policies."""

from __future__ import annotations

import hashlib
import json
import random
import re
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Callable


class FailureKind(str, Enum):
    NETWORK_TIMEOUT = "network_timeout"
    RATE_LIMIT = "rate_limit"
    EMPTY_RESULT = "empty_result"
    INVALID_ARGUMENT = "invalid_argument"
    FILE_NOT_FOUND = "file_not_found"
    CODE_EXECUTION = "code_execution_failed"
    CONFLICTING_RESULTS = "conflicting_results"
    INVALID_JSON = "invalid_json"
    TOOL_HALLUCINATION = "tool_hallucination"
    REPEATED_CALL = "repeated_call"
    PLAN_LOOP = "plan_loop"
    CONTEXT_OVERFLOW = "context_overflow"
    EARLY_EXIT = "early_exit"
    INTERRUPTED = "interrupted"
    UNKNOWN = "unknown"


class RecoveryAction(str, Enum):
    RETRY = "retry"
    REPAIR_ARGUMENTS = "repair_arguments"
    FALLBACK = "fallback"
    REPLAN = "replan"
    COMPACT_CONTEXT = "compact_context"
    FAIL = "fail"


@dataclass
class FailureRecord:
    kind: FailureKind
    message: str
    retryable: bool
    action: RecoveryAction
    source: str = "runtime"
    attempt: int = 0
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        data["action"] = self.action.value
        return data


class FailureClassifier:
    """Classifies exceptions and textual tool/model failures."""

    RULES: list[tuple[FailureKind, tuple[str, ...], bool, RecoveryAction]] = [
        (FailureKind.RATE_LIMIT, ("429", "rate limit", "too many requests", "quota exceeded"), True, RecoveryAction.RETRY),
        (FailureKind.NETWORK_TIMEOUT, ("timeout", "timed out", "connection reset", "temporarily unavailable", "temporary_error", "server overloaded"), True, RecoveryAction.RETRY),
        (FailureKind.CONTEXT_OVERFLOW, ("context length", "maximum context", "too many tokens", "context window"), True, RecoveryAction.COMPACT_CONTEXT),
        (FailureKind.EMPTY_RESULT, ("no search results", "no results found", "empty response", "no output generated"), True, RecoveryAction.FALLBACK),
        (FailureKind.FILE_NOT_FOUND, ("file not found", "not found", "no such file", "does not exist"), False, RecoveryAction.REPLAN),
        (FailureKind.INVALID_ARGUMENT, ("invalid argument", "invalid parameter", "typeerror", "missing field", "no filename provided", "empty search query"), True, RecoveryAction.REPAIR_ARGUMENTS),
        (FailureKind.INVALID_JSON, ("jsondecodeerror", "json parsing", "invalid json", "not a valid plan"), True, RecoveryAction.REPAIR_ARGUMENTS),
        (FailureKind.CODE_EXECUTION, ("syntaxerror", "traceback", "assertionerror", "execution failed", "non-zero exit", "couldn't find a solution"), True, RecoveryAction.RETRY),
        (FailureKind.CONFLICTING_RESULTS, ("sources conflict", "conflicting results", "contradictory sources"), False, RecoveryAction.REPLAN),
        (FailureKind.TOOL_HALLUCINATION, ("unknown tool", "tool does not exist", "unsupported tool", "unknown agent"), False, RecoveryAction.REPLAN),
        (FailureKind.EARLY_EXIT, ("unable to answer", "cannot complete", "could not complete", "requested exit"), False, RecoveryAction.REPLAN),
        (FailureKind.INTERRUPTED, ("request_exit", "keyboardinterrupt", "interrupted by user"), False, RecoveryAction.FAIL),
    ]

    @classmethod
    def classify(cls, value: Any, source: str = "runtime", attempt: int = 0) -> FailureRecord:
        if isinstance(value, BaseException):
            text = f"{type(value).__name__}: {value}"
        else:
            text = str(value or "")
        lowered = text.lower().strip()
        if not lowered:
            return FailureRecord(FailureKind.EMPTY_RESULT, "Empty result.", True, RecoveryAction.FALLBACK, source, attempt)
        for kind, markers, retryable, action in cls.RULES:
            if any(marker in lowered for marker in markers):
                return FailureRecord(kind, text, retryable, action, source, attempt)
        return FailureRecord(FailureKind.UNKNOWN, text, False, RecoveryAction.REPLAN, source, attempt)


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 8.0
    jitter_ratio: float = 0.2

    def delay(self, attempt: int) -> float:
        raw = min(self.max_delay_seconds, self.base_delay_seconds * (2 ** max(0, attempt - 1)))
        jitter = raw * self.jitter_ratio
        return max(0.0, raw + random.uniform(-jitter, jitter))


class RepeatedTraceDetector:
    """Detects repeated node/tool signatures before they become infinite loops."""

    def __init__(self, max_repetitions: int = 2):
        self.max_repetitions = max_repetitions
        self._counts: dict[str, int] = {}

    @staticmethod
    def signature(agent: str, task: str, payload: Any = None) -> str:
        normalized = {
            "agent": str(agent).strip().lower(),
            "task": re.sub(r"\s+", " ", str(task).strip().lower()),
            "payload": payload,
        }
        encoded = json.dumps(normalized, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def record(self, signature: str) -> int:
        count = self._counts.get(signature, 0) + 1
        self._counts[signature] = count
        return count

    def is_repeated(self, signature: str) -> bool:
        return self._counts.get(signature, 0) >= self.max_repetitions

    def snapshot(self) -> dict[str, int]:
        return dict(self._counts)

    def restore(self, values: dict[str, int]) -> None:
        self._counts = {str(key): int(value) for key, value in values.items()}


def repair_arguments(task: dict[str, Any], failure: FailureRecord) -> dict[str, Any]:
    """Create a bounded repair instruction without mutating the original plan."""
    repaired = dict(task)
    repaired["task"] = (
        f"{task.get('task', '')}\n\n"
        "Recovery constraint: the previous call had invalid or missing parameters. "
        "Validate every tool argument against its documented type and required fields before calling it. "
        f"Previous failure: {failure.message[:500]}"
    )
    repaired["recovery"] = {"action": RecoveryAction.REPAIR_ARGUMENTS.value, "kind": failure.kind.value}
    return repaired


def run_with_backoff(
    operation: Callable[[], Any],
    policy: RetryPolicy,
    classifier: type[FailureClassifier] = FailureClassifier,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[Any, list[FailureRecord]]:
    failures: list[FailureRecord] = []
    for attempt in range(1, policy.max_attempts + 1):
        try:
            result = operation()
            if result is None or (isinstance(result, str) and not result.strip()):
                raise ValueError("Empty response")
            return result, failures
        except Exception as exc:
            failure = classifier.classify(exc, source="model", attempt=attempt)
            failures.append(failure)
            if not failure.retryable or attempt >= policy.max_attempts:
                raise
            sleep(policy.delay(attempt))
    raise RuntimeError("retry policy exhausted")
