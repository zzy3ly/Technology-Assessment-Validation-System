"""Version-neutral fault-injection tests for comparing Planner implementations."""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock

project_root = os.environ.get("AGENTICSEEK_PROJECT_ROOT")
if project_root:
    sys.path.insert(0, project_root)

for module_name in (
    "torch", "transformers", "kokoro", "adaptive_classifier", "text2emotion",
    "ollama", "openai", "together", "IPython", "IPython.display", "playsound3",
    "soundfile", "pyaudio", "librosa", "pypdf", "langid", "pypinyin",
    "fake_useragent", "chromedriver_autoinstaller", "num2words", "sentencepiece",
    "sacremoses", "scipy", "numpy", "selenium_stealth",
    "undetected_chromedriver", "markdownify",
):
    sys.modules.setdefault(module_name, MagicMock())

os.environ.setdefault("WORK_DIR", os.getcwd())

from sources.agents.planner_agent import PlannerAgent


def one_step_plan():
    return [["Task 1", {"id": "1", "agent": "coder", "task": "work", "need": []}]]


class PlannerRecoveryCompatibility(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        agent = PlannerAgent.__new__(PlannerAgent)
        agent.max_plan_attempts = 3
        agent.max_step_retries = 1
        agent.max_replans = 2
        agent.max_total_steps = 12
        agent.last_plan_attempts = 1
        agent.last_workflow_context = None
        agent.stop = False
        agent.status_message = ""
        agent.logger = MagicMock()
        agent.make_plan = AsyncMock(return_value=one_step_plan())
        agent.update_plan = AsyncMock(return_value=one_step_plan())
        agent.start_agent_process = AsyncMock(return_value=("done", True))
        return agent

    async def test_transient_failure_retries_only_failed_node(self):
        agent = self.make_agent()
        agent.start_agent_process = AsyncMock(side_effect=[("temporary", False), ("recovered", True)])
        answer, _ = await agent.process("goal", None)
        self.assertEqual(answer, "recovered")
        self.assertEqual(agent.start_agent_process.await_count, 2)

    async def test_uncaught_tool_exception_becomes_terminal_result(self):
        agent = self.make_agent()
        agent.start_agent_process = AsyncMock(side_effect=TimeoutError("request timed out"))
        answer, _ = await agent.process("goal", None)
        self.assertIn("failed", answer.lower())

    async def test_retry_exhaustion_is_not_reported_as_success(self):
        agent = self.make_agent()
        agent.start_agent_process = AsyncMock(side_effect=[("bad-1", False), ("bad-2", False)])
        answer, reasoning = await agent.process("goal", None)
        self.assertIn("failed", answer.lower())
        if reasoning:
            self.assertEqual(json.loads(reasoning).get("state"), "failed")

    async def test_execution_has_machine_readable_terminal_state(self):
        agent = self.make_agent()
        answer, reasoning = await agent.process("goal", None)
        self.assertEqual(answer, "done")
        self.assertEqual(json.loads(reasoning).get("state"), "succeeded")


if __name__ == "__main__":
    unittest.main(verbosity=2)
