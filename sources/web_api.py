"""Non-blocking, single-user Web API with durable records and real checkpoints."""
from __future__ import annotations

import asyncio
import configparser
import copy
import json
import logging
import os
import re
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sources.checkpoint import CheckpointStore, utc_now
from sources.skills import SkillRegistry, SkillSelector
from sources.web_runtime import AgentRuntime, public_workflow

TERMINAL = {"succeeded", "failed", "stopped", "interrupted"}
MODES = Literal["planner", "casual", "web", "file", "coder", "auto"]
logger = logging.getLogger(__name__)


class TaskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=32000)
    mode: MODES = "planner"
    skill: str | None = None


class TaskManager:
    def __init__(self, root, work_dir, registry, runtime_factory):
        self.root = root
        self.registry = registry
        self.runtime_factory = runtime_factory
        self.checkpoints = CheckpointStore(work_dir / ".agenticseek_checkpoints")
        self.store = CheckpointStore(work_dir / ".agenticseek_web")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="agent-web")
        self.lock = threading.RLock()
        self.records = {}
        self.active_id = None
        self.runtime = None
        self.closed = False
        for path in self.store.root.glob("*.json"):
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
                if row["status"] not in TERMINAL:
                    row.update(status="interrupted", error="服务重启中断了任务；可从非终态 checkpoint 继续。", success=False)
                    self.store.save(row["id"], row)
                self.records[row["id"]] = row
            except (ValueError, KeyError, OSError):
                logger.warning("Skipped invalid Web task record: %s", path.name)

    def _save(self, row):
        row["updated_at"] = utc_now()
        self.store.save(row["id"], row)

    def submit(self, query, mode="planner", skill=None, checkpoint_id=None):
        query = query.strip()
        if not query:
            raise HTTPException(422, "请输入任务内容。")
        try:
            selection = SkillSelector(self.registry).select(query)
            if skill:
                self.registry.load(skill)
            if selection and skill and selection.skill.metadata.name != skill:
                raise ValueError("输入中的 Skill 与选择框不一致。")
            if selection:
                skill = selection.skill.metadata.name
        except (ValueError, OSError) as exc:
            raise HTTPException(422, str(exc)) from exc
        if skill or checkpoint_id:
            mode = "planner"
        with self.lock:
            if self.closed:
                raise HTTPException(503, "服务正在关闭。")
            if self.active_id:
                raise HTTPException(409, "已有任务在运行，请等待完成或停止后再提交。")
            task_id = uuid.uuid4().hex
            row = {"id": task_id, "query": query, "mode": mode, "skill": skill,
                   "checkpoint_id": checkpoint_id, "status": "queued", "success": False,
                   "answer": "", "error": None, "workflow": None, "blocks": [],
                   "token_usage": {}, "phase": "等待执行", "stop_requested": False,
                   "created_at": utc_now(), "updated_at": utc_now()}
            self.records[task_id] = row
            self._save(row)
            self.active_id = task_id
            self.executor.submit(self._run, task_id)
            return copy.deepcopy(row)

    def _run(self, task_id):
        runtime = None
        try:
            with self.lock:
                row = self.records[task_id]
                row.update(status="running", phase="正在加载 Agent 和模型配置")
                self._save(row)
            runtime = self.runtime_factory()
            with self.lock:
                self.runtime = runtime
                if row["stop_requested"]:
                    runtime.request_stop()
            result = asyncio.run(runtime.run(copy.deepcopy(row)))
            snapshot = runtime.snapshot()
            with self.lock:
                row.update(snapshot)
                state = result.get("status", "failed")
                if state not in {"succeeded", "failed", "stopped"}:
                    state = "failed"
                if row["stop_requested"]:
                    state = "stopped"
                row.update(answer=result.get("answer", ""), status=state, success=state == "succeeded")
                if row.get("workflow"):
                    row["checkpoint_id"] = row["workflow"]["task_id"]
                if state == "failed":
                    row["error"] = (row.get("workflow") or {}).get("last_error") or row["answer"] or "任务未通过完成校验。"
        except BaseException as exc:
            # Older providers may call exit(1); never leave a task stuck.
            logger.exception("Web task %s failed", task_id)
            with self.lock:
                row = self.records[task_id]
                if runtime:
                    try:
                        row.update(runtime.snapshot())
                        if row.get("workflow"):
                            row["checkpoint_id"] = row["workflow"]["task_id"]
                    except Exception:
                        logger.exception("Could not collect failure evidence")
                row.update(status="stopped" if row["stop_requested"] else "failed", success=False,
                           error=f"{type(exc).__name__}: {exc}")
        finally:
            if runtime:
                try:
                    runtime.close()
                except Exception:
                    logger.exception("Runtime cleanup failed")
            with self.lock:
                try:
                    self._save(self.records[task_id])
                finally:
                    self.runtime = None
                    self.active_id = None

    def get(self, task_id):
        with self.lock:
            if task_id not in self.records:
                raise HTTPException(404, "任务不存在。")
            row = self.records[task_id]
            if self.active_id == task_id and self.runtime:
                snapshot = self.runtime.snapshot()
                row.update(snapshot)
                checkpoint_id = (snapshot.get("workflow") or {}).get("task_id")
                if checkpoint_id and row.get("checkpoint_id") != checkpoint_id:
                    row["checkpoint_id"] = checkpoint_id
                    self._save(row)
            return copy.deepcopy(row)

    def list(self):
        with self.lock:
            rows = sorted(self.records.values(), key=lambda r: r["created_at"], reverse=True)
            keys = ("id", "query", "mode", "skill", "status", "created_at", "updated_at", "checkpoint_id")
            return [{key: row.get(key) for key in keys} for row in rows[:100]]

    def stop(self, task_id):
        with self.lock:
            row = self.get(task_id)
            if row["status"] in TERMINAL:
                return row
            self.records[task_id].update(stop_requested=True, status="stopping", phase="等待当前调用结束后停止")
            if self.runtime:
                self.runtime.request_stop()
            self._save(self.records[task_id])
            return self.get(task_id)

    def checkpoint(self, checkpoint_id):
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", checkpoint_id):
            raise HTTPException(422, "无效的 checkpoint ID。")
        try:
            return self.checkpoints.load(checkpoint_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, "Checkpoint 不存在。") from exc

    def close(self):
        with self.lock:
            self.closed = True
            if self.active_id:
                self.stop(self.active_id)
        self.executor.shutdown(wait=False, cancel_futures=True)


def create_app(root: Path, runtime_factory=None, work_dir=None):
    root = Path(root).resolve()
    work_dir = Path(work_dir or os.getenv("WORK_DIR") or root / "workspace").resolve()
    registry = SkillRegistry(os.getenv("SKILLS_ROOT", str(root / "skills")))
    manager = TaskManager(root, work_dir, registry, runtime_factory or (lambda: AgentRuntime(root, registry)))

    @asynccontextmanager
    async def lifespan(app):
        yield
        manager.close()

    app = FastAPI(title="Research & Verify · AgenticSeek", version="0.2.0", lifespan=lifespan)
    app.state.manager = manager
    port = os.getenv("BACKEND_PORT") or "7777"
    origins = [f"http://{host}:{p}" for host in ("localhost", "127.0.0.1") for p in (port, "3000")]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", "backend"])

    @app.middleware("http")
    async def local_origin_only(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if origin and origin not in origins:
                return JSONResponse({"detail": "本地工具执行接口拒绝来自外部网页的请求。"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/health")
    @app.get("/api/health")
    def health():
        config = configparser.ConfigParser()
        config.read(root / "config.ini", encoding="utf-8")
        return {"status": "healthy", "version": "0.2.0", "active_task_id": manager.active_id,
                "model": config.get("MAIN", "provider_model", fallback="未配置"),
                "provider": config.get("MAIN", "provider_name", fallback="未配置"),
                "workspace": str(work_dir), "single_user": True}

    @app.get("/api/capabilities")
    async def capabilities():
        url = (os.getenv("SEARXNG_BASE_URL") or "http://127.0.0.1:8080").rstrip("/")
        if url == "http://searxng:8080" and not Path("/.dockerenv").exists():
            url = "http://127.0.0.1:8080"
        try:
            async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
                response = await client.get(url + "/")
                available = response.is_success
        except httpx.HTTPError:
            available = False
        return {"search_available": available, "search_url": url,
                "search_hint": "SearxNG 已连接" if available else "SearxNG 未连接；联网搜索需启动 Docker 中的 redis、searxng。问答、文件和代码任务不受此依赖影响。",
                "routing_hint": "默认使用 Planner 编排；原自动路由可选，首次使用可能加载或下载分类模型。",
                "stop_hint": "停止是协作式的，正在进行的模型请求或工具操作结束后才会退出。"}

    @app.get("/api/skills")
    def skills():
        return [{"name": s.name, "description": s.description, "version": s.version,
                 "required_agents": s.required_agents, "validator": bool(s.validator)} for s in registry.list()]

    @app.get("/api/tasks")
    def tasks():
        return manager.list()

    @app.post("/api/tasks", status_code=202)
    def submit(request: TaskRequest):
        return manager.submit(request.query, request.mode, request.skill)

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str):
        return manager.get(task_id)

    @app.post("/api/tasks/{task_id}/stop")
    def stop(task_id: str):
        return manager.stop(task_id)

    @app.get("/api/checkpoints")
    def checkpoints():
        rows = []
        for path in manager.checkpoints.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                state = data.get("current_state")
                rows.append({"task_id": data["task_id"], "goal": data.get("goal", ""),
                             "state": state, "completed": len(data.get("completed_steps", [])),
                             "total": len(data.get("plan", [])), "updated_at": data.get("updated_at", ""),
                             "resumable": state not in ("succeeded", "failed"),
                             "skill": data.get("selected_skill", {}).get("name")})
            except (ValueError, KeyError, OSError):
                continue
        return sorted(rows, key=lambda r: r["updated_at"], reverse=True)[:100]

    @app.get("/api/checkpoints/{checkpoint_id}")
    def checkpoint(checkpoint_id: str):
        return public_workflow(manager.checkpoint(checkpoint_id))

    @app.post("/api/checkpoints/{checkpoint_id}/resume", status_code=202)
    def resume(checkpoint_id: str):
        payload = manager.checkpoint(checkpoint_id)
        if payload.get("current_state") in ("succeeded", "failed"):
            raise HTTPException(409, "已完成或最终失败的任务不进行自动重放；请按结果调整后新建任务。")
        return manager.submit(payload["goal"], checkpoint_id=checkpoint_id)

    @app.get("/api/tasks/{task_id}/screenshot")
    def screenshot(task_id: str):
        row = manager.get(task_id)
        path = root / ".screenshots" / "updated_screen.png"
        if manager.active_id != task_id or not row.get("browser_used") or not path.is_file():
            raise HTTPException(404, "当前任务暂无浏览器画面。")
        return FileResponse(path, headers={"Cache-Control": "no-store"})

    @app.post("/query")
    async def legacy_query(request: TaskRequest):
        row = manager.submit(request.query, request.mode, request.skill)
        while row["status"] not in TERMINAL:
            await asyncio.sleep(0.3)
            row = manager.get(row["id"])
        return legacy_shape(row)

    def legacy_shape(row):
        return {"uid": row["id"], "done": str(row["status"] in TERMINAL).lower(),
                "success": str(row["success"]).lower(), "answer": row["answer"],
                "reasoning": "", "status": row["status"], "agent_name": row.get("agent_name", ""),
                "blocks": {str(i): b for i, b in enumerate(row["blocks"])}}

    @app.get("/latest_answer")
    def latest():
        rows = manager.list()
        if not rows:
            raise HTTPException(404, "暂无任务。")
        return legacy_shape(manager.get(rows[0]["id"]))

    @app.get("/is_active")
    def active():
        return {"is_active": True, "is_generating": manager.active_id is not None}

    build = root / "frontend" / "agentic-seek-front" / "build"
    if build.is_dir():
        app.mount("/", StaticFiles(directory=build, html=True), name="frontend")
    else:
        @app.get("/")
        def not_built():
            return JSONResponse({"message": "前端尚未构建，请运行 start_web.ps1。", "docs": "/docs"})
    return app
