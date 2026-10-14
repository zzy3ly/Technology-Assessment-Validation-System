import tempfile
import unittest
from pathlib import Path

from sources.checkpoint import CheckpointStore
from sources.workflow_state_machine import WorkflowContext, WorkflowEvent, WorkflowState


def plan():
    return [
        ["one", {"id": "1", "agent": "coder", "task": "first", "need": []}],
        ["two", {"id": "2", "agent": "file", "task": "second", "need": ["1"]}],
    ]


class TestCheckpointStore(unittest.TestCase):
    def test_round_trip_contains_required_recovery_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CheckpointStore(folder)
            context = WorkflowContext("goal", workflow_id="task-123")
            context.transition(WorkflowEvent.START_PLANNING)
            context.install_plan(plan())
            context.transition(WorkflowEvent.PLAN_CREATED)
            context.transition(WorkflowEvent.STEP_STARTED)
            context.record_success("first output")
            context.transition(WorkflowEvent.STEP_SUCCEEDED)
            context.model_messages = {"coder": [{"role": "user", "content": "x"}]}
            context.token_usage["total_tokens"] = 42
            path = store.save(context.workflow_id, context.snapshot())
            self.assertTrue(path.exists())
            payload = store.load("task-123")
            for field in (
                "task_id", "current_state", "completed_steps", "pending_steps",
                "tool_outputs", "model_messages", "token_usage", "error_history",
                "created_at", "updated_at",
            ):
                self.assertIn(field, payload)
            restored = WorkflowContext.from_snapshot(payload)
            self.assertEqual(restored.cursor, 1)
            self.assertEqual(restored.results["1"], "first output")
            self.assertEqual(restored.current_task["id"], "2")
            self.assertEqual(restored.state, WorkflowState.READY)

    def test_running_checkpoint_resumes_as_ready_same_cursor(self):
        context = WorkflowContext("goal", workflow_id="task-running")
        context.install_plan(plan())
        context.state = WorkflowState.RUNNING
        payload = context.snapshot()
        restored = WorkflowContext.from_snapshot(payload)
        self.assertEqual(restored.state, WorkflowState.READY)
        self.assertEqual(restored.cursor, 0)


if __name__ == "__main__":
    unittest.main()
