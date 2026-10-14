from __future__ import annotations

from pathlib import Path

from .loader import SkillLoader, SkillLoadError
from .schema import LoadedSkill, SkillMetadata


class SkillRegistry:
    def __init__(self, skills_root: str | Path = "skills"):
        self.loader = SkillLoader(skills_root)
        self._metadata: dict[str, SkillMetadata] = {}
        self._loaded: dict[str, LoadedSkill] = {}
        self.refresh()

    def refresh(self) -> None:
        self._metadata.clear(); self._loaded.clear()
        root = self.loader.skills_root
        if not root.is_dir():
            return
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            if not (directory / "SKILL.md").is_file():
                continue
            metadata = self.loader.read_metadata(directory)
            if metadata.name in self._metadata:
                raise SkillLoadError(f"Duplicate skill name: {metadata.name}")
            self._metadata[metadata.name] = metadata

    def list(self) -> list[SkillMetadata]:
        return list(self._metadata.values())

    def has(self, name: str) -> bool:
        return name in self._metadata

    def load(self, name: str) -> LoadedSkill:
        if name not in self._metadata:
            raise SkillLoadError(f"Unknown skill: {name}")
        if name not in self._loaded:
            self._loaded[name] = self.loader.load(self._metadata[name].root)
        return self._loaded[name]
