from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from .schema import LoadedSkill


@dataclass
class SkillValidation:
    passed: bool
    errors: list[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"passed": self.passed, "errors": self.errors, "details": self.details}


class SkillRuntime:
    @staticmethod
    def validate_dependencies(skill: LoadedSkill, agents: dict) -> SkillValidation:
        available_agents = {str(name).lower() for name in agents}
        available_tools = {
            str(tool).lower()
            for agent in agents.values()
            for tool in getattr(agent, "tools", {})
        }
        missing_agents = [x for x in skill.metadata.required_agents if x.lower() not in available_agents]
        missing_tools = [x for x in skill.metadata.required_tools if x.lower() not in available_tools]
        errors = [f"Missing required agent: {x}" for x in missing_agents]
        errors += [f"Missing required tool: {x}" for x in missing_tools]
        return SkillValidation(not errors, errors)

    @staticmethod
    def render_for_planner(skill: LoadedSkill, user_goal: str) -> str:
        references = "\n\n".join(
            f"### Reference: {name}\n{content}" for name, content in skill.references.items()
        )
        return (
            f"User goal:\n{user_goal}\n\n"
            f"Selected skill: {skill.metadata.name} v{skill.metadata.version}\n"
            f"Skill instructions:\n{skill.instructions}\n\n{references}\n\n"
            "Create a plan that satisfies both the user goal and every selected-skill requirement."
        )

    @staticmethod
    def validate_plan(skill: LoadedSkill, plan: list[list]) -> SkillValidation:
        agents = [str(item[1].get("agent", "")).lower() for item in plan]
        text = "\n".join(str(item[1].get("task", "")).lower() for item in plan)
        errors = []
        for required in skill.metadata.required_plan_agents:
            if required.lower() not in agents:
                errors.append(f"Plan does not use required agent: {required}")
        for alternatives in skill.metadata.plan_requirements:
            if not any(term.lower() in text for term in alternatives):
                errors.append(f"Plan misses requirement terms: {', '.join(alternatives)}")
        for forbidden in skill.metadata.forbidden_plan_terms:
            if forbidden.lower() in text:
                errors.append(f"Plan contains forbidden instruction: {forbidden}")
        return SkillValidation(not errors, errors)

    @staticmethod
    def validate_outcome(skill: LoadedSkill, work_dir: str | Path) -> SkillValidation:
        if not skill.metadata.validator:
            return SkillValidation(True)
        script = (skill.metadata.root / skill.metadata.validator).resolve()
        if skill.metadata.root not in script.parents or not script.is_file():
            return SkillValidation(False, ["Skill validator path is invalid"])
        try:
            completed = subprocess.run(
                [sys.executable, str(script), "--work-dir", str(Path(work_dir).resolve())],
                capture_output=True, text=True, timeout=180, check=False,
            )
            payload = json.loads(completed.stdout.strip().splitlines()[-1])
            return SkillValidation(bool(payload.get("passed")), list(payload.get("errors", [])), payload)
        except Exception as exc:
            return SkillValidation(False, [f"Skill validator failed: {type(exc).__name__}: {exc}"])
