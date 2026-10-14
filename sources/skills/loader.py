from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .schema import LoadedSkill, SkillMetadata


class SkillLoadError(ValueError):
    pass


class SkillLoader:
    NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    REFERENCE_PATTERN = re.compile(r"\[[^\]]+\]\((references/[^)]+)\)")

    def __init__(self, skills_root: str | Path, max_chars: int = 20_000):
        self.skills_root = Path(skills_root).resolve()
        self.max_chars = max_chars

    def _inside(self, path: Path, root: Path | None = None) -> Path:
        resolved = path.resolve()
        boundary = (root or self.skills_root).resolve()
        if resolved != boundary and boundary not in resolved.parents:
            raise SkillLoadError(f"Skill path escapes allowed root: {path}")
        return resolved

    @staticmethod
    def _parse_value(value: str):
        value = value.strip()
        if value.startswith("[") or value.startswith("{"):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise SkillLoadError(f"Invalid JSON-like frontmatter value: {value}") from exc
        return value.strip('"\'')

    def read_metadata(self, skill_dir: str | Path) -> SkillMetadata:
        root = self._inside(Path(skill_dir))
        path = self._inside(root / "SKILL.md", root)
        if not path.is_file():
            raise SkillLoadError(f"Missing SKILL.md in {root}")
        text = path.read_text(encoding="utf-8")
        if len(text) > self.max_chars:
            raise SkillLoadError(f"SKILL.md exceeds {self.max_chars} characters")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise SkillLoadError("SKILL.md must contain YAML-style frontmatter")
        raw, _ = text[4:].split("\n---\n", 1)
        data = {}
        for line in raw.splitlines():
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if ":" not in line:
                raise SkillLoadError(f"Invalid frontmatter line: {line}")
            key, value = line.split(":", 1)
            data[key.strip()] = self._parse_value(value)
        name = str(data.get("name", ""))
        description = str(data.get("description", ""))
        if not self.NAME_PATTERN.fullmatch(name):
            raise SkillLoadError(f"Invalid skill name: {name}")
        if root.name != name:
            raise SkillLoadError(f"Skill folder '{root.name}' must match name '{name}'")
        if not description:
            raise SkillLoadError("Skill description is required")
        metadata = data.get("metadata", {})
        if not isinstance(metadata, dict):
            raise SkillLoadError("Skill metadata must be an object")
        groups = metadata.get("plan_requirements", [])
        return SkillMetadata(
            name=name,
            description=description,
            version=str(metadata.get("version", "0.1.0")),
            root=root,
            required_agents=tuple(map(str, metadata.get("required_agents", []))),
            required_tools=tuple(map(str, metadata.get("required_tools", []))),
            required_plan_agents=tuple(map(str, metadata.get("required_plan_agents", []))),
            plan_requirements=tuple(tuple(map(str, group)) for group in groups),
            forbidden_plan_terms=tuple(map(str, metadata.get("forbidden_plan_terms", []))),
            validator=str(metadata["validator"]) if metadata.get("validator") else None,
        )

    def load(self, skill_dir: str | Path) -> LoadedSkill:
        metadata = self.read_metadata(skill_dir)
        text = (metadata.root / "SKILL.md").read_text(encoding="utf-8")
        _, body = text[4:].split("\n---\n", 1)
        references = {}
        for relative in dict.fromkeys(self.REFERENCE_PATTERN.findall(body)):
            path = self._inside(metadata.root / relative, metadata.root)
            if not path.is_file():
                raise SkillLoadError(f"Referenced file does not exist: {relative}")
            content = path.read_text(encoding="utf-8")
            if len(content) > self.max_chars:
                raise SkillLoadError(f"Reference exceeds {self.max_chars} characters: {relative}")
            references[relative] = content
        digest = hashlib.sha256()
        digest.update(text.encode("utf-8"))
        for name, content in sorted(references.items()):
            digest.update(name.encode("utf-8")); digest.update(content.encode("utf-8"))
        return LoadedSkill(metadata, body.strip(), references, digest.hexdigest())
