"""Adapter for the real agents; no mock answers and no second workflow engine."""
from __future__ import annotations

import asyncio
import configparser
import copy
import os
import shutil
import sys
import threading
from pathlib import Path


class LazyBrowser:
    """Keep chat/file/code usable before Chrome is needed."""

    def __init__(self):
        self.instance = None

    def __getattr__(self, name):
        if self.instance is None:
            candidates = [os.getenv("CHROME_EXECUTABLE_PATH"), shutil.which("google-chrome"),
                          shutil.which("chromium"), shutil.which("chromium-browser")]
            if sys.platform.startswith("win"):
                candidates += [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                               r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                               str(Path(os.getenv("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe")]
            elif sys.platform == "darwin":
                candidates += ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
            else:
                candidates += ["/opt/chrome/chrome", "/opt/google/chrome/chrome-headless-shell"]
            if not any(path and Path(path).is_file() for path in candidates):
                raise FileNotFoundError("Web 浏览器未找到 Chrome；请安装或设置 CHROME_EXECUTABLE_PATH。")
            from sources.browser import Browser, create_driver
            self.instance = Browser(create_driver(headless=True, stealth_mode=False))
        return getattr(self.instance, name)

    def close(self):
        if self.instance is not None:
            self.instance.driver.quit()


def public_workflow(payload):
    """Expose execution evidence, not internal prompts / model message history."""
    keys = (
        "task_id", "goal", "current_state", "plan", "cursor", "completed_steps",
        "pending_steps", "tool_outputs", "failures", "token_usage", "error_history",
        "state_history", "step_retries", "plan_attempts", "replan_count", "executed_steps",
        "last_error", "selected_skill", "skill_validation", "review_contexts", "terminal",
    )
    return copy.deepcopy({key: payload[key] for key in keys if key in payload})


class AgentRuntime:
    """One independent runtime per Web task. Checkpoints carry memory forward."""

    def __init__(self, root: Path, registry):
        from sources.llm_provider import Provider
        from sources.agents import PlannerAgent

        config = configparser.ConfigParser()
        config.read(root / "config.ini", encoding="utf-8")
        name = config["MAIN"]["provider_name"]
        if not config.getboolean("MAIN", "is_local") and name.lower() != "test":
            if not os.getenv(f"{name.upper()}_API_KEY"):
                raise ValueError(f"请在 .env 配置 {name.upper()}_API_KEY 后重试。")
        provider = Provider(name, config["MAIN"]["provider_model"],
                            config["MAIN"]["provider_server_address"],
                            config.getboolean("MAIN", "is_local"))
        provider.retry_policy.max_attempts = config.getint("RECOVERY", "model_max_attempts", fallback=3)
        if config.getboolean("RECOVERY", "fallback_enabled", fallback=False):
            fallback_name = config["RECOVERY"]["fallback_provider_name"]
            if not os.getenv(f"{fallback_name.upper()}_API_KEY"):
                raise ValueError("备用模型 API Key 未配置。")
            provider.set_fallback_provider(Provider(
                fallback_name, config["RECOVERY"]["fallback_provider_model"],
                config["RECOVERY"]["fallback_provider_server_address"], False))
        self.provider = provider
        self.browser = LazyBrowser()
        personality = "jarvis" if config.getboolean("MAIN", "jarvis_personality") else "base"
        self.planner = PlannerAgent("Planner", str(root / "prompts" / personality / "planner_agent.txt"),
                                    provider, browser=self.browser)
        self.planner.skill_registry = registry
        # The legacy interpreter uses exec() + process-wide stdout redirection.
        # A Web tool must not change the HTTP server's cwd/stdout/event loop.
        from sources.tools.WebPythonInterpreter import WebPythonInterpreter
        from sources.tools.WebShellInterpreter import WebShellInterpreter
        for agent in self.planner.agents.values():
            if "python" in agent.tools:
                agent.tools["python"] = WebPythonInterpreter()
            if "bash" in agent.tools:
                agent.tools["bash"] = WebShellInterpreter()
            if "bash" in agent.tools and os.name == "nt":
                agent.memory.push("system", (
                    "Web execution environment: Windows. The registered `bash` tool actually runs "
                    "PowerShell with the project virtualenv on PATH. Use PowerShell syntax, "
                    "Get-ChildItem/Get-Content and literal Windows paths. Do not use ls -la, cat, "
                    "/mnt paths or Linux shell syntax. The tool cwd is already WORK_DIR. "
                    "Use the python tool for Python code. Send executable commands only; "
                    "do not repeat commands in fenced blocks merely to document them."
                ))
        self.planner.max_step_retries = config.getint("RECOVERY", "node_max_retries", fallback=1)
        self.planner.max_replans = config.getint("RECOVERY", "workflow_max_replans", fallback=2)
        self.planner.max_total_steps = config.getint("RECOVERY", "workflow_max_steps", fallback=24)
        self.registry = registry
        self.current_agent = None
        self.stop_event = threading.Event()
        self.answer = ""
        self.routing = "explicit"

    async def run(self, request):
        from sources.skills import SkillSelector

        query = request["query"]
        selection = SkillSelector(self.registry).select(query)
        skill = self.registry.load(request["skill"]) if request.get("skill") else (
            selection.skill if selection else None)
        if selection:
            query = selection.query
        mode = request["mode"]
        self.current_agent = self.planner if skill or request.get("checkpoint_id") or mode == "planner" else (
            self.planner.agents.get(mode))
        if mode == "auto" and not skill and not request.get("checkpoint_id"):
            # Original learned router remains opt-in; it may download weights.
            from sources.router import AgentRouter
            agents = [self.planner, *[self.planner.agents[k] for k in ("casual", "web", "file", "coder")]]
            self.current_agent = AgentRouter(agents).select_agent(query)
            self.routing = "learned_router"
        if self.current_agent is None:
            raise ValueError("未找到可执行该任务的 Agent。")
        if self.stop_event.is_set():
            return {"answer": "任务在执行前已停止。", "status": "stopped"}
        if request.get("checkpoint_id"):
            work = self.planner.resume_from_checkpoint(request["checkpoint_id"], resume_stopped=True)
        elif self.current_agent is self.planner:
            work = self.planner.process(query, None, selected_skill=skill)
        else:
            work = self.current_agent.process(query, None)

        # Cooperative stop, including children which reset stop in process().
        task = asyncio.create_task(work)
        while not task.done():
            if self.stop_event.is_set():
                self.request_stop()
            await asyncio.wait({task}, timeout=0.2)
        self.answer, _ = await task
        if self.current_agent is not self.planner and self.current_agent.type != "casual_agent":
            self.answer = self.current_agent.raw_answer_blocks(self.answer) or self.answer
        context = self.planner.last_workflow_context
        if context is not None:
            state = context.state.value
        else:
            state = "succeeded" if self.current_agent.success and bool(self.answer and self.answer.strip()) else "failed"
        if self.stop_event.is_set():
            state = "stopped"
        return {"answer": self.answer or "", "status": state}

    def request_stop(self):
        self.stop_event.set()
        for agent in [self.planner, *self.planner.agents.values()]:
            agent.request_stop()

    def snapshot(self):
        context = self.planner.last_workflow_context
        blocks = []
        for name, agent in self.planner.agents.items():
            for block in list(agent.get_blocks_result()):
                blocks.append({"agent": name, **block.jsonify()})
        return {
            "agent_name": getattr(self.current_agent, "type", None),
            "phase": getattr(self.current_agent, "status_message", "正在初始化"),
            "routing": self.routing,
            "workflow": public_workflow(context.snapshot()) if context else None,
            "blocks": blocks,
            "token_usage": self.provider.get_usage(),
            "browser_used": self.browser.instance is not None,
        }

    def close(self):
        try:
            self.browser.close()
        finally:
            for agent in [self.planner, *self.planner.agents.values()]:
                agent.executor.shutdown(wait=False)
