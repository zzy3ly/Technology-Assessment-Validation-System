"""Deterministic workflow state machine used by the planner runtime.

The LLM may propose a plan, but it never decides runtime control flow.  Retry,
replan, stop, success, and failure are governed by the transition table below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class WorkflowState(str, Enum):
    RECEIVED = "received"
    PLANNING = "planning"
    READY = "ready"
    RUNNING = "running"
    RETRYING = "retrying"
    REPLANNING = "replanning"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"


class WorkflowEvent(str, Enum):
    START_PLANNING = "start_planning"
    PLAN_CREATED = "plan_created"
    PLAN_FAILED = "plan_failed"
    STEP_STARTED = "step_started"
    STEP_SUCCEEDED = "step_succeeded"
    STEP_FAILED = "step_failed"
    RETRY_EXHAUSTED = "retry_exhausted"
    REPLAN_SUCCEEDED = "replan_succeeded"
    REPLAN_FAILED = "replan_failed"
    ALL_COMPLETED = "all_completed"
    STOP_REQUESTED = "stop_requested"
    RESUME_REQUESTED = "resume_requested"
    FATAL_ERROR = "fatal_error"


TERMINAL_STATES = {
    WorkflowState.SUCCEEDED,
    WorkflowState.FAILED,
    WorkflowState.STOPPED,
}


TRANSITIONS: dict[tuple[WorkflowState, WorkflowEvent], WorkflowState] = {
    (WorkflowState.STOPPED, WorkflowEvent.RESUME_REQUESTED): WorkflowState.READY,
    (WorkflowState.RECEIVED, WorkflowEvent.START_PLANNING): WorkflowState.PLANNING,
    (WorkflowState.PLANNING, WorkflowEvent.PLAN_CREATED): WorkflowState.READY,
    (WorkflowState.PLANNING, WorkflowEvent.PLAN_FAILED): WorkflowState.FAILED,
    (WorkflowState.READY, WorkflowEvent.STEP_STARTED): WorkflowState.RUNNING,
    (WorkflowState.RUNNING, WorkflowEvent.STEP_SUCCEEDED): WorkflowState.READY,
    (WorkflowState.RUNNING, WorkflowEvent.STEP_FAILED): WorkflowState.RETRYING,
    (WorkflowState.RETRYING, WorkflowEvent.STEP_STARTED): WorkflowState.RUNNING,
    (WorkflowState.RETRYING, WorkflowEvent.RETRY_EXHAUSTED): WorkflowState.REPLANNING,
    (WorkflowState.REPLANNING, WorkflowEvent.REPLAN_SUCCEEDED): WorkflowState.READY,
    (WorkflowState.REPLANNING, WorkflowEvent.REPLAN_FAILED): WorkflowState.FAILED,
    (WorkflowState.READY, WorkflowEvent.ALL_COMPLETED): WorkflowState.SUCCEEDED,
}

# Stop and fatal error are valid from every non-terminal state.
for _state in WorkflowState:
    if _state not in TERMINAL_STATES:
        TRANSITIONS[(_state, WorkflowEvent.STOP_REQUESTED)] = WorkflowState.STOPPED
        TRANSITIONS[(_state, WorkflowEvent.FATAL_ERROR)] = WorkflowState.FAILED


class InvalidTransition(RuntimeError):
    """Raised when runtime code attempts an undefined state transition."""


@dataclass(frozen=True)
class StateTransition:
    previous: WorkflowState
    event: WorkflowEvent
    current: WorkflowState
    detail: str = ""


@dataclass
class WorkflowContext:
    goal: str
    workflow_id: str = field(default_factory=lambda: uuid4().hex)
    max_plan_attempts: int = 3
    max_step_retries: int = 1
    max_replans: int = 2
    max_total_steps: int = 24
    state: WorkflowState = WorkflowState.RECEIVED
    plan: list[list[Any]] = field(default_factory=list)
    cursor: int = 0
    plan_attempts: int = 0
    replan_count: int = 0
    step_retries: dict[str, int] = field(default_factory=dict)
    results: dict[str, str] = field(default_factory=dict)
    failures: dict[str, str] = field(default_factory=dict)
    transitions: list[StateTransition] = field(default_factory=list)
    last_error: str = ""
    executed_steps: int = 0
    error_history: list[dict[str, Any]] = field(default_factory=list)
    model_messages: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    token_usage: dict[str, int | float] = field(default_factory=lambda: {
        "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost": 0.0
    })
    trace_counts: dict[str, int] = field(default_factory=dict)
    selected_skill: dict[str, str] = field(default_factory=dict)
    skill_validation: dict[str, Any] = field(default_factory=dict)
    review_contexts: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def current_task(self) -> dict[str, Any] | None:
        if self.cursor < 0 or self.cursor >= len(self.plan):
            return None
        return self.plan[self.cursor][1]

    @property
    def current_task_name(self) -> str | None:
        if self.cursor < 0 or self.cursor >= len(self.plan):
            return None
        return str(self.plan[self.cursor][0])

    def transition(self, event: WorkflowEvent, detail: str = "") -> WorkflowState:
        key = (self.state, event)
        if key not in TRANSITIONS:
            raise InvalidTransition(f"invalid transition: {self.state.value} + {event.value}")
        previous = self.state
        self.state = TRANSITIONS[key]
        self.transitions.append(StateTransition(previous, event, self.state, detail))
        if self.state == WorkflowState.FAILED and detail:
            self.last_error = detail
        return self.state

    def install_plan(self, plan: list[list[Any]]) -> None:
        if not plan:
            raise ValueError("plan cannot be empty")
        self._validate_plan(plan)
        self.plan = plan
        self.cursor = 0

    @staticmethod
    def _validate_plan(plan: list[list[Any]]) -> None:
        """Reject duplicate IDs, forward dependencies, and malformed steps."""
        seen: set[str] = set()
        for index, item in enumerate(plan):
            if not isinstance(item, (list, tuple)) or len(item) != 2 or not isinstance(item[1], dict):
                raise ValueError(f"plan item {index} must be [name, task]")
            task = item[1]
            task_id = str(task.get("id", "")).strip()
            agent = str(task.get("agent", "")).strip()
            description = str(task.get("task", "")).strip()
            if not task_id or not agent or not description:
                raise ValueError(f"plan item {index} is missing id, agent, or task")
            if task_id in seen:
                raise ValueError(f"duplicate task id: {task_id}")
            needs = [str(value) for value in (task.get("need") or [])]
            unknown = [value for value in needs if value not in seen]
            if unknown:
                raise ValueError(
                    f"task {task_id} has forward or unknown dependencies: {unknown}"
                )
            seen.add(task_id)

    def task_id(self) -> str:
        task = self.current_task
        if task is None:
            raise IndexError("workflow has no current task")
        return str(task["id"])

    def dependencies(self) -> dict[str, str]:
        task = self.current_task
        if task is None:
            return {}
        needs = task.get("need", []) or []
        return {str(key): self.results[str(key)] for key in needs if str(key) in self.results}

    def record_success(self, answer: str) -> None:
        task_id = self.task_id()
        self.results[task_id] = answer
        self.failures.pop(task_id, None)
        self.cursor += 1
        self.executed_steps += 1

    def record_failure(self, error: str) -> None:
        task_id = self.task_id()
        self.failures[task_id] = error
        self.results[task_id] = error
        self.last_error = error
        self.executed_steps += 1

    def append_error(self, error: dict[str, Any]) -> None:
        self.error_history.append(dict(error))

    def step_budget_exhausted(self) -> bool:
        return self.executed_steps >= self.max_total_steps

    def can_retry_current_step(self) -> bool:
        task_id = self.task_id()
        return self.step_retries.get(task_id, 0) < self.max_step_retries

    def consume_retry(self) -> int:
        task_id = self.task_id()
        count = self.step_retries.get(task_id, 0) + 1
        self.step_retries[task_id] = count
        return count

    def can_replan(self) -> bool:
        return self.replan_count < self.max_replans

    def install_replanned_tail(self, plan: list[list[Any]]) -> None:
        """Install a complete revised plan and advance past the failed step.

        Planner prompts require completed tasks to remain unchanged. Advancing the
        cursor prevents replaying successful side effects and executes only the new
        recovery tail.
        """
        if not plan:
            raise ValueError("replanned workflow cannot be empty")
        self._validate_plan(plan)
        next_cursor = self.cursor + 1
        if len(plan) <= next_cursor:
            raise ValueError("replanned workflow contains no recovery step")
        self.plan = plan
        self.cursor = next_cursor
        self.replan_count += 1

    def trace(self) -> list[dict[str, str]]:
        return [
            {
                "from": item.previous.value,
                "event": item.event.value,
                "to": item.current.value,
                "detail": item.detail,
            }
            for item in self.transitions
        ]

    def snapshot(self) -> dict[str, Any]:
        completed_ids = [task_id for task_id in self.results if task_id not in self.failures]
        pending = [item for item in self.plan[self.cursor:]] if self.plan else []
        return {
            "task_id": self.workflow_id,
            "goal": self.goal,
            "current_state": self.state.value,
            "plan": self.plan,
            "cursor": self.cursor,
            "completed_steps": completed_ids,
            "pending_steps": pending,
            "tool_outputs": self.results,
            "failures": self.failures,
            "model_messages": self.model_messages,
            "token_usage": self.token_usage,
            "error_history": self.error_history,
            "state_history": self.trace(),
            "step_retries": self.step_retries,
            "trace_counts": self.trace_counts,
            "plan_attempts": self.plan_attempts,
            "replan_count": self.replan_count,
            "executed_steps": self.executed_steps,
            "max_plan_attempts": self.max_plan_attempts,
            "max_step_retries": self.max_step_retries,
            "max_replans": self.max_replans,
            "max_total_steps": self.max_total_steps,
            "last_error": self.last_error,
            "selected_skill": self.selected_skill,
            "skill_validation": self.skill_validation,
            "review_contexts": self.review_contexts,
            "terminal": self.is_terminal,
        }

    @classmethod
    def from_snapshot(cls, payload: dict[str, Any]) -> "WorkflowContext":
        context = cls(
            goal=str(payload["goal"]),
            workflow_id=str(payload["task_id"]),
            max_plan_attempts=int(payload.get("max_plan_attempts", 3)),
            max_step_retries=int(payload.get("max_step_retries", 1)),
            max_replans=int(payload.get("max_replans", 2)),
            max_total_steps=int(payload.get("max_total_steps", 24)),
        )
        context.plan = payload.get("plan", [])
        if context.plan:
            context._validate_plan(context.plan)
        context.cursor = int(payload.get("cursor", 0))
        context.results = {str(k): str(v) for k, v in payload.get("tool_outputs", {}).items()}
        context.failures = {str(k): str(v) for k, v in payload.get("failures", {}).items()}
        context.model_messages = payload.get("model_messages", {})
        context.token_usage = payload.get("token_usage", context.token_usage)
        context.error_history = payload.get("error_history", [])
        context.step_retries = {str(k): int(v) for k, v in payload.get("step_retries", {}).items()}
        context.trace_counts = {str(k): int(v) for k, v in payload.get("trace_counts", {}).items()}
        context.plan_attempts = int(payload.get("plan_attempts", 0))
        context.replan_count = int(payload.get("replan_count", 0))
        context.executed_steps = int(payload.get("executed_steps", 0))
        context.last_error = str(payload.get("last_error", ""))
        context.selected_skill = dict(payload.get("selected_skill", {}))
        context.skill_validation = dict(payload.get("skill_validation", {}))
        context.review_contexts = dict(payload.get("review_contexts", {}))
        state = WorkflowState(str(payload.get("current_state", WorkflowState.READY.value)))
        # A process may have died while RUNNING/RETRYING/REPLANNING. Resume from
        # READY at the same cursor so only the incomplete node is replayed.
        context.state = state if state in TERMINAL_STATES else WorkflowState.READY
        context.transitions = [
            StateTransition(
                WorkflowState(item["from"]),
                WorkflowEvent(item["event"]),
                WorkflowState(item["to"]),
                str(item.get("detail", "")),
            )
            for item in payload.get("state_history", [])
        ]
        return context
