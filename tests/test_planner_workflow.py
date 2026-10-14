import json
import os
import sys
import unittest
import tempfile
from unittest.mock import AsyncMock, MagicMock

for mod_name in [
    'torch', 'transformers', 'kokoro', 'adaptive_classifier', 'text2emotion',
    'ollama', 'openai', 'together', 'IPython', 'IPython.display', 'playsound3',
    'soundfile', 'pyaudio', 'librosa', 'pypdf', 'langid', 'pypinyin',
    'fake_useragent', 'chromedriver_autoinstaller', 'num2words', 'sentencepiece',
    'sacremoses', 'scipy', 'numpy', 'selenium_stealth',
    'undetected_chromedriver', 'markdownify',
]:
    if mod_name not in sys.modules:
        sys.modules[mod_name] = MagicMock()

os.environ.setdefault('WORK_DIR', '/tmp')

from sources.agents.planner_agent import PlannerAgent
from sources.workflow_state_machine import WorkflowState
from sources.checkpoint import CheckpointStore
from sources.workflow_state_machine import WorkflowContext


def plan(*task_ids):
    result = []
    previous = []
    for task_id in task_ids:
        result.append([
            f"Task {task_id}",
            {"id": task_id, "agent": "coder", "task": f"work {task_id}", "need": list(previous)},
        ])
        previous.append(task_id)
    return result


class TestPlannerWorkflow(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        agent = PlannerAgent.__new__(PlannerAgent)
        agent.max_plan_attempts = 3
        agent.max_step_retries = 1
        agent.max_replans = 2
        agent.last_plan_attempts = 1
        agent.last_workflow_context = None
        agent.stop = False
        agent.status_message = ""
        agent.logger = MagicMock()
        agent.make_plan = AsyncMock(return_value=plan("1"))
        agent.update_plan = AsyncMock()
        agent.start_agent_process = AsyncMock(return_value=("done", True))
        return agent

    async def test_successful_workflow_reaches_terminal_success(self):
        agent = self.make_agent()
        answer, reasoning = await agent.process("goal", None)
        data = json.loads(reasoning)
        self.assertEqual(answer, "done")
        self.assertEqual(data["state"], "succeeded")
        self.assertEqual(agent.last_workflow_context.state, WorkflowState.SUCCEEDED)
        agent.update_plan.assert_not_awaited()

    async def test_failed_step_retries_once_then_succeeds(self):
        agent = self.make_agent()
        agent.start_agent_process = AsyncMock(side_effect=[("bad", False), ("recovered", True)])
        answer, reasoning = await agent.process("goal", None)
        events = [item["event"] for item in json.loads(reasoning)["transitions"]]
        self.assertEqual(answer, "recovered")
        self.assertEqual(events.count("step_started"), 2)
        self.assertIn("step_failed", events)
        agent.update_plan.assert_not_awaited()

    async def test_retry_exhaustion_triggers_bounded_replan(self):
        agent = self.make_agent()
        agent.start_agent_process = AsyncMock(side_effect=[("bad-1", False), ("bad-2", False), ("fixed", True)])
        agent.update_plan = AsyncMock(return_value=plan("1", "2"))
        answer, reasoning = await agent.process("goal", None)
        data = json.loads(reasoning)
        events = [item["event"] for item in data["transitions"]]
        self.assertEqual(answer, "fixed")
        self.assertEqual(data["replan_count"], 1)
        self.assertIn("retry_exhausted", events)
        self.assertIn("replan_succeeded", events)

    async def test_missing_recovery_plan_fails_cleanly(self):
        agent = self.make_agent()
        original = plan("1")
        agent.make_plan = AsyncMock(return_value=original)
        agent.start_agent_process = AsyncMock(side_effect=[("bad-1", False), ("bad-2", False)])
        agent.update_plan = AsyncMock(return_value=original)
        answer, reasoning = await agent.process("goal", None)
        data = json.loads(reasoning)
        self.assertTrue(answer.startswith("Workflow failed:"))
        self.assertEqual(data["state"], "failed")
        self.assertIn("recovery step", data["last_error"])

    async def test_resume_skips_completed_node(self):
        agent = self.make_agent()
        agent.agents = {}
        agent.llm = None
        with tempfile.TemporaryDirectory() as folder:
            agent.checkpoint_store = CheckpointStore(folder)
            context = WorkflowContext("goal", workflow_id="resume-001")
            context.install_plan(plan("1", "2"))
            context.state = WorkflowState.READY
            context.results["1"] = "already paid result"
            context.cursor = 1
            agent.checkpoint_store.save(context.workflow_id, context.snapshot())
            agent.start_agent_process = AsyncMock(return_value=("second done", True))
            answer, reasoning = await agent.resume_from_checkpoint("resume-001", None)
            data = json.loads(reasoning)
            self.assertEqual(answer, "second done")
            self.assertEqual(data["state"], "succeeded")
            self.assertEqual(agent.start_agent_process.await_count, 1)
            called_task = agent.start_agent_process.await_args.args[0]
            self.assertEqual(called_task["id"], "2")
            self.assertEqual(agent.last_workflow_context.results["1"], "already paid result")


    async def test_explicit_web_resume_of_stopped_workflow_reuses_completed_node(self):
        agent = self.make_agent()
        agent.agents = {}
        agent.llm = None
        with tempfile.TemporaryDirectory() as folder:
            agent.checkpoint_store = CheckpointStore(folder)
            context = WorkflowContext("goal", workflow_id="stopped-web")
            context.install_plan(plan("1", "2"))
            context.state = WorkflowState.STOPPED
            context.results["1"] = "already paid result"
            context.cursor = 1
            agent.checkpoint_store.save(context.workflow_id, context.snapshot())
            answer, reasoning = await agent.resume_from_checkpoint("stopped-web", resume_stopped=True)
            self.assertEqual(json.loads(reasoning)["state"], "succeeded")
            agent.start_agent_process.assert_awaited_once()
            self.assertEqual(agent.start_agent_process.await_args.args[0]["id"], "2")
            self.assertEqual(agent.last_workflow_context.results["1"], "already paid result")
            self.assertIn("resume_requested", [item["event"] for item in json.loads(reasoning)["transitions"]])


if __name__ == "__main__":
    unittest.main()
