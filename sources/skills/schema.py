from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    version: str
    root: Path
    required_agents: tuple[str, ...] = ()
    required_tools: tuple[str, ...] = ()
    required_plan_agents: tuple[str, ...] = ()
    plan_requirements: tuple[tuple[str, ...], ...] = ()
    forbidden_plan_terms: tuple[str, ...] = ()
    validator: str | None = None


@dataclass(frozen=True)
class LoadedSkill:
    metadata: SkillMetadata
    instructions: str
    references: dict[str, str]
    content_hash: str

    def checkpoint_metadata(self) -> dict[str, str]:
        return {
            "name": self.metadata.name,
            "version": self.metadata.version,
            "content_hash": self.content_hash,
        }


@dataclass(frozen=True)
class SkillSelection:
    skill: LoadedSkill
    query: str
    explicit: bool = True

