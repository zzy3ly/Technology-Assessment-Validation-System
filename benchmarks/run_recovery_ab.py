"""Controlled recovery benchmark: real Planner/checkpoints, deterministic local tools."""
import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
# Fixed before execution. Families, not numeric replicas, are the sampling units.
FAMILIES = {
    "clean": "control",
    "timeout_once": "transient",
    "rate_limit_once": "transient",
    "empty_once": "transient",
    "execution_once": "transient",
    "timeout_twice": "budget_boundary",
    "rate_limit_persistent": "persistent",
    "missing_file": "persistent",
    "invalid_argument": "persistent",
    "conflicting_sources": "persistent",
    "invalid_json": "persistent",
    "context_overflow": "persistent",
    "wrong_result": "semantic_negative_control",
    "crash_after_first": "interruption",
    "crash_after_second": "interruption",
    "crash_before_checkpoint": "interruption",
    "mid_node_timeout": "transient",
    "permission_denied": "persistent",
}


def oracle(workload, values):
    if workload == "sum":
        return sum(values)
    if workload == "unique":
        return sorted(set(values))
    return sum((i + 1) * value for i, value in enumerate(values))


def dataset():
    values = [4, -2, 4, 0, 9]
    return {"version": "1.0", "design": "18 fixed families x 3 local workloads; no production prevalence assumed",
            "cases": [{"id": f"{family}-{work}", "family": family, "stratum": stratum,
                       "workload": work, "values": values, "expected": oracle(work, values)}
                      for family, stratum in FAMILIES.items() for work in ("sum", "unique", "weighted")]}


class InjectedCrash(BaseException):
    pass


async def replay(cls, case, mode):
    family = case["family"]
    plan = [[name, {"id": str(i), "agent": "coder", "task": name,
                    "need": [str(i - 1)] if i > 1 else []}]
            for i, name in enumerate(("Read input", "Transform input", "Publish output"), 1)]
    trace, checkpoint_evidence = [], []
    compute_attempt = 0
    crashed = False
    fault_events = 0
    restart_count = 0
    with tempfile.TemporaryDirectory(prefix="recovery-ab-") as tmp:
        folder = Path(tmp)
        source = folder / "input.json"
        source.write_text(json.dumps(case["values"]), encoding="utf-8")
        target = folder / "result.json"

        def tool(name, args, operation):
            signature = json.dumps([name, args], sort_keys=True)
            entry = {"tool": name, "args": args, "signature": signature, "success": False}
            trace.append(entry)
            try:
                value = operation()
                entry.update(success=True, result=value)
                return value
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
                raise

        def compute(values):
            nonlocal compute_attempt, fault_events
            compute_attempt += 1
            error = None
            if family in ("timeout_once", "mid_node_timeout") and compute_attempt == 1:
                error = TimeoutError("request timed out")
            elif family == "timeout_twice" and compute_attempt <= 2:
                error = TimeoutError("request timed out")
            elif family == "rate_limit_persistent" or (family == "rate_limit_once" and compute_attempt == 1):
                error = RuntimeError("HTTP 429 too many requests")
            elif family == "empty_once" and compute_attempt == 1:
                error = ValueError("empty response")
            elif family == "execution_once" and compute_attempt == 1:
                error = RuntimeError("execution failed: worker temporarily unavailable")
            elif family == "missing_file":
                error = FileNotFoundError("file not found: required lookup")
            elif family == "invalid_argument":
                error = TypeError("invalid argument: unsupported data type")
            elif family == "conflicting_sources":
                error = ValueError("conflicting results from sources")
            elif family == "invalid_json":
                error = ValueError("invalid JSON: truncated data")
            elif family == "context_overflow":
                error = RuntimeError("maximum context length exceeded")
            elif family == "permission_denied":
                error = PermissionError("permission denied")
            if error:
                fault_events += 1
                raise error
            # Independent implementation from dataset oracle.
            if case["workload"] == "sum":
                result = 0
                for value in values:
                    result += value
            elif case["workload"] == "unique":
                result = sorted(dict.fromkeys(values))
            else:
                result = sum(value * position for position, value in enumerate(values, 1))
            if family == "wrong_result":
                result = [999] if isinstance(result, list) else result + 1
            return result

        async def execute(task, infos):
            nonlocal crashed, fault_events
            step = str(task["id"])
            try:
                if step == "1":
                    value = tool("read_json", {"path": "input.json"}, lambda: json.loads(source.read_text(encoding="utf-8")))
                elif step == "2":
                    values = json.loads((infos or {}).get("1", "null"))
                    if family == "mid_node_timeout":
                        tool("read_reference", {"table": "v1"}, lambda: {"version": 1})
                    value = tool("transform", {"workload": case["workload"], "values": values}, lambda: compute(values))
                else:
                    value = json.loads((infos or {}).get("2", "null"))
                    def publish():
                        target.write_text(json.dumps({"result": value}), encoding="utf-8")
                        return value
                    tool("write_json", {"path": "result.json", "value": value}, publish)
                if family == "crash_before_checkpoint" and step == "2" and not crashed:
                    crashed = True
                    fault_events += 1
                    raise InjectedCrash("tool completed; node result not yet checkpointed")
                if mode == "legacy" and not crashed and ((family == "crash_after_first" and step == "2") or (family == "crash_after_second" and step == "3")):
                    # Legacy interruption must happen before this node's tool calls.
                    raise AssertionError("legacy crash hook should have intercepted node entry")
                return json.dumps(value), True
            except Exception as exc:
                # Mirrors child Agent reporting a failed tool to Planner.
                return f"{type(exc).__name__}: {exc}", False

        async def execute_with_legacy_crash(task, infos):
            nonlocal crashed, fault_events
            step = str(task["id"])
            if mode == "legacy" and not crashed and ((family == "crash_after_first" and step == "2") or (family == "crash_after_second" and step == "3")):
                crashed = True
                fault_events += 1
                raise InjectedCrash("between completed nodes")
            return await execute(task, infos)

        def make_agent():
            agent = cls.__new__(cls)
            agent.max_plan_attempts = 3
            agent.max_step_retries = 1
            agent.max_replans = 2
            agent.max_total_steps = 12
            agent.last_plan_attempts = 1
            agent.stop = False
            agent.status_message = ""
            agent.logger = MagicMock()
            agent.agents = {}
            agent.llm = None
            agent.node_retry_policy = MagicMock()
            agent.node_retry_policy.delay.return_value = 0
            agent.make_plan = AsyncMock(return_value=plan)
            agent.update_plan = AsyncMock(side_effect=lambda *args, **kwargs: args[1])
            agent.start_agent_process = execute_with_legacy_crash
            agent.checkpoint_store = None
            if mode != "legacy":
                from sources.checkpoint import CheckpointStore
                agent.checkpoint_store = CheckpointStore(folder / "checkpoints")
                save = agent._save_checkpoint
                def crash_safe_save(context):
                    nonlocal crashed, fault_events
                    save(context)
                    threshold = {"crash_after_first": 1, "crash_after_second": 2}.get(family)
                    if not crashed and threshold and context.cursor == threshold and context.state.value == "ready":
                        crashed = True
                        fault_events += 1
                        raise InjectedCrash("after atomic checkpoint")
                agent._save_checkpoint = crash_safe_save
            return agent

        agent = make_agent()
        error, answer, reasoning = None, "", ""
        try:
            try:
                answer, reasoning = await agent.process("Transform local input and publish output", None)
            except InjectedCrash:
                restart_count += 1
                if mode != "legacy":
                    task_id = agent.last_workflow_context.workflow_id
                    checkpoint_evidence.append(agent.checkpoint_store.load(task_id))
                agent = make_agent()  # discard Planner instance and volatile state
                if mode == "current":
                    answer, reasoning = await agent.resume_from_checkpoint(task_id, None)
                else:
                    answer, reasoning = await agent.process("Transform local input and publish output", None)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        actual = json.loads(target.read_text(encoding="utf-8")) if target.exists() else None
        correct = actual == {"result": case["expected"]}
        seen, succeeded = set(), set()
        repeat_calls = repeated_success = 0
        for entry in trace:
            signature = entry["signature"]
            repeat_calls += signature in seen
            repeated_success += signature in succeeded
            seen.add(signature)
            if entry["success"]:
                succeeded.add(signature)
        return {"id": case["id"], "family": family, "stratum": case["stratum"], "mode": mode,
                "task_success": correct, "fault_events": fault_events, "tool_calls": len(trace),
                "repeated_calls": repeat_calls, "repeat_after_success": repeated_success,
                "repeat_after_failure": repeat_calls - repeated_success,
                "actual": actual, "expected": case["expected"], "restart_count": restart_count,
                "exception": error, "answer": answer, "trace": trace, "checkpoint_evidence": checkpoint_evidence,
                "reasoning": json.loads(reasoning) if reasoning else None}


def summarize(rows):
    faults = [r for r in rows if r["stratum"] not in ("control", "semantic_negative_control")]
    calls = sum(r["tool_calls"] for r in rows)
    return {"tasks": len(rows), "successes": sum(r["task_success"] for r in rows),
            "fault_tasks": len(faults), "recovered": sum(r["task_success"] for r in faults),
            "recovery_rate": sum(r["task_success"] for r in faults) / len(faults) if faults else None,
            "tool_calls": calls, "repeated_calls": sum(r["repeated_calls"] for r in rows),
            "repeat_after_failure": sum(r["repeat_after_failure"] for r in rows),
            "repeat_after_success": sum(r["repeat_after_success"] for r in rows),
            "repeat_after_success_rate": sum(r["repeat_after_success"] for r in rows) / calls if calls else 0}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "test_outputs/recovery_ab_v1")
    parser.add_argument("--legacy-root", type=Path, default=Path(r"E:\CodeFile\agenticSeek - 副本"))
    parser.add_argument("--worker", choices=("legacy", "current", "current_restart"))
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    if args.worker:
        os.environ["AGENTICSEEK_PROJECT_ROOT"] = str(args.project_root)
        with contextlib.redirect_stdout(io.StringIO()):
            ns = runpy.run_path(str(ROOT / "benchmarks/test_planner_recovery_compat.py"), run_name="recovery_import")
            cls = ns["PlannerAgent"]
            cases = json.loads((args.output / "dataset.json").read_text(encoding="utf-8"))["cases"]
            rows = [asyncio.run(asyncio.wait_for(replay(cls, case, args.worker), 10)) for case in cases]
        source = Path(sys.modules[cls.__module__].__file__).resolve()
        assert source == (args.project_root / "sources/agents/planner_agent.py").resolve()
        (args.output / f"{args.worker}.json").write_text(json.dumps({"source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(), "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "dataset.json").write_text(json.dumps(dataset(), ensure_ascii=False, indent=2), encoding="utf-8")
    report = {}
    for mode, root in (("legacy", args.legacy_root), ("current", ROOT), ("current_restart", ROOT)):
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", mode, "--project-root", str(root),
                        "--output", str(args.output.resolve())], cwd=ROOT, check=True, timeout=90)
        rows = json.loads((args.output / f"{mode}.json").read_text(encoding="utf-8"))["rows"]
        report[mode] = {"overall": summarize(rows), "families": {f: summarize([r for r in rows if r["family"] == f]) for f in FAMILIES}}
    (args.output / "summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({m: r["overall"] for m, r in report.items()}, indent=2))


if __name__ == "__main__":
    main()
