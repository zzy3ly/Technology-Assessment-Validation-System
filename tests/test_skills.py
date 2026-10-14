import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from sources.agents.planner_agent import PlannerAgent
from sources.skills import SkillRegistry, SkillRuntime, SkillSelector, SkillValidation
from sources.skills.loader import SkillLoadError, SkillLoader


ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "skills"


def compliant_plan():
    return [
        ["Inspect", {"id": "1", "agent": "file", "task": "Inspect tests and locate the root cause", "need": []}],
        ["Repair", {"id": "2", "agent": "coder", "task": "Run pytest, make a minimal fix, then rerun full regression tests", "need": ["1"]}],
    ]


class SkillLoaderTests(unittest.TestCase):
    def test_registry_discovers_metadata_then_loader_loads_reference(self):
        registry = SkillRegistry(SKILLS)
        self.assertTrue(registry.has("test-driven-code-repair"))
        self.assertEqual(registry.list()[0].required_agents, ("file", "coder"))
        skill = registry.load("test-driven-code-repair")
        self.assertIn("Use the existing test suite", skill.instructions)
        self.assertIn("references/repair-policy.md", skill.references)
        self.assertEqual(len(skill.content_hash), 64)

    def test_explicit_selector_removes_skill_prefix(self):
        selection = SkillSelector(SkillRegistry(SKILLS)).select(
            "$test-driven-code-repair fix normalize_email.py"
        )
        self.assertEqual(selection.skill.metadata.name, "test-driven-code-repair")
        self.assertEqual(selection.query, "fix normalize_email.py")

    def test_loader_rejects_path_escape(self):
        with tempfile.TemporaryDirectory() as folder:
            loader = SkillLoader(folder)
            with self.assertRaises(SkillLoadError):
                loader.read_metadata(Path(folder).parent)


class SkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.skill = SkillRegistry(SKILLS).load("test-driven-code-repair")

    def test_plan_validator_rejects_generic_code_plan(self):
        invalid = [["Code", {"id": "1", "agent": "coder", "task": "Change the code", "need": []}]]
        result = SkillRuntime.validate_plan(self.skill, invalid)
        self.assertFalse(result.passed)
        self.assertGreaterEqual(len(result.errors), 2)

    def test_plan_validator_accepts_test_driven_plan(self):
        self.assertTrue(SkillRuntime.validate_plan(self.skill, compliant_plan()).passed)

    def test_real_validator_runs_pytest(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "test_ok.py").write_text(
                "import unittest\n\n"
                "class TestMath(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(2 + 2, 4)\n",
                encoding="utf-8",
            )
            result = SkillRuntime.validate_outcome(self.skill, folder)
            self.assertTrue(result.passed, result.errors)
            self.assertEqual(result.details["returncode"], 0)

    def test_validator_rejects_zero_executed_tests(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "test_empty.py").write_text(
                '\"\"\"A discoverable file containing no executable tests.\"\"\"\n',
                encoding="utf-8",
            )
            result = SkillRuntime.validate_outcome(self.skill, folder)
            self.assertFalse(result.passed, result.details)


class SkillPlannerIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_skill_is_injected_and_recorded(self):
        skill = SkillRegistry(SKILLS).load("test-driven-code-repair")
        agent = PlannerAgent.__new__(PlannerAgent)
        agent.max_plan_attempts = 3; agent.max_step_retries = 1; agent.max_replans = 1; agent.max_total_steps = 8
        agent.last_plan_attempts = 1; agent.stop = False; agent.status_message = ""
        agent.logger = MagicMock(); agent.checkpoint_store = None; agent.llm = None
        coder = MagicMock(); coder.tools = {"python": object(), "file_finder": object()}
        file_agent = MagicMock(); file_agent.tools = {"file_finder": object()}
        agent.agents = {"coder": coder, "file": file_agent}
        agent.make_plan = AsyncMock(return_value=compliant_plan())
        agent.start_agent_process = AsyncMock(side_effect=[("inspected", True), ("fixed and tests passed", True)])
        with patch.object(SkillRuntime, "validate_outcome", return_value=SkillValidation(True, details={"returncode": 0})):
            answer, reasoning = await agent.process("fix the bug", None, selected_skill=skill)
        prompt = agent.make_plan.await_args.args[0]
        metadata = json.loads(reasoning)
        self.assertIn("Selected skill: test-driven-code-repair", prompt)
        self.assertIn("Run the failing test before editing", prompt)
        self.assertEqual(metadata["selected_skill"]["name"], "test-driven-code-repair")
        self.assertTrue(metadata["skill_validation"]["passed"])
        self.assertEqual(metadata["state"], "succeeded")
        self.assertEqual(answer, "fixed and tests passed")


if __name__ == "__main__":
    unittest.main()
