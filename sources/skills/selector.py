from __future__ import annotations

import re

from .registry import SkillRegistry
from .schema import SkillSelection


class SkillSelector:
    EXPLICIT = re.compile(r"^\$([a-z0-9]+(?:-[a-z0-9]+)*)\s+(.+)$", re.DOTALL)

    def __init__(self, registry: SkillRegistry):
        self.registry = registry

    def select(self, query: str) -> SkillSelection | None:
        match = self.EXPLICIT.match(query.strip())
        if not match:
            return None
        name, clean_query = match.group(1), match.group(2).strip()
        if not clean_query:
            raise ValueError("A skill invocation must include a task after the skill name")
        return SkillSelection(self.registry.load(name), clean_query, explicit=True)
