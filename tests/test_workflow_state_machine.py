import unittest

from sources.workflow_state_machine import (
    InvalidTransition,
    WorkflowContext,
    WorkflowEvent,
    WorkflowState,
)


def step(task_id="1", need=None):
    return [f"Task {task_id}", {"id": task_id, "agent": "coder", "task": "work", "need": need or []}]


class TestWorkflowStateMachine(unittest.TestCase):
    def test_success_path_is_explicit(self):
        context = WorkflowContext("goal")
        context.transition(WorkflowEvent.START_PLANNING)
        context.install_plan([step()])
        context.transition(WorkflowEvent.PLAN_CREATED)
        context.transition(WorkflowEvent.STEP_STARTED)
        context.record_success("done")
        context.transition(WorkflowEvent.STEP_SUCCEEDED)
        context.transition(WorkflowEvent.ALL_COMPLETED)
        self.assertEqual(context.state, WorkflowState.SUCCEEDED)
        self.assertEqual([item["event"] for item in context.trace()], [
            "start_planning", "plan_created", "step_started", "step_succeeded", "all_completed"
        ])

    def test_invalid_transition_is_rejected(self):
        context = WorkflowContext("goal")
        with self.assertRaises(InvalidTransition):
            context.transition(WorkflowEvent.STEP_STARTED)

    def test_plan_rejects_forward_dependency(self):
        context = WorkflowContext("goal")
        with self.assertRaisesRegex(ValueError, "forward or unknown"):
            context.install_plan([step("1", ["2"]), step("2")])

    def test_plan_rejects_duplicate_ids(self):
        context = WorkflowContext("goal")
        with self.assertRaisesRegex(ValueError, "duplicate task id"):
            context.install_plan([step("1"), step("1")])

    def test_retry_budget_is_deterministic(self):
        context = WorkflowContext("goal", max_step_retries=1)
        context.install_plan([step()])
        self.assertTrue(context.can_retry_current_step())
        self.assertEqual(context.consume_retry(), 1)
        self.assertFalse(context.can_retry_current_step())


if __name__ == "__main__":
    unittest.main()
