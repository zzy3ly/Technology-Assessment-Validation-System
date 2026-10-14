"""Atomic, resumable workflow checkpoints."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CheckpointStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def path_for(self, task_id: str) -> Path:
        safe = "".join(ch for ch in str(task_id) if ch.isalnum() or ch in "-_" )
        if not safe:
            raise ValueError("task_id must contain at least one safe character")
        return self.root / f"{safe}.json"

    def save(self, task_id: str, payload: dict[str, Any]) -> Path:
        path = self.path_for(task_id)
        with self._lock:
            previous = self.load(task_id) if path.exists() else {}
            data = dict(payload)
            data["task_id"] = task_id
            data["created_at"] = previous.get("created_at", data.get("created_at", utc_now()))
            data["updated_at"] = utc_now()
            fd, temp_name = tempfile.mkstemp(prefix=path.stem + "-", suffix=".tmp", dir=self.root)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(data, stream, ensure_ascii=False, indent=2, default=str)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, path)
            except Exception:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
                raise
        return path

    def load(self, task_id: str) -> dict[str, Any]:
        path = self.path_for(task_id)
        if not path.exists():
            raise FileNotFoundError(f"Checkpoint not found: {path}")
        with self._lock, path.open(encoding="utf-8") as stream:
            return json.load(stream)

    def exists(self, task_id: str) -> bool:
        return self.path_for(task_id).exists()

    def mark_terminal(self, task_id: str, state: str) -> None:
        payload = self.load(task_id)
        payload["current_state"] = state
        payload["terminal"] = True
        self.save(task_id, payload)
