"""Deterministic, paired orchestration replay. No model/API calls.

Loads each repository's real Planner.process in a separate process. Plans and
node responses are controlled; independent arithmetic artifacts provide truth.
This measures the current orchestration bundle, not the transition table alone.
"""
import argparse
import asyncio
import contextlib
import copy
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
FAMILIES = (
    "clean", "transient_false", "transient_timeout", "permanent_false",
    "permanent_timeout", "empty_success", "early_exit", "semantic_wrong",
    "missing_dependency", "forward_dependency", "numeric_dependency", "duplicate_id",
)


def build_dataset():
    cases = []
    for family in FAMILIES:
        for variant, numbers in enumerate(([2, 3, 7], [-4, 9, 1], [0, 11, 23])):
            plan = [
                ["Read", {"id": "1", "agent": "coder", "task": "Read input numbers", "need": []}],
                ["Calculate", {"id": "2", "agent": "coder", "task": "Calculate sum", "need": ["1"]}],
                ["Report", {"id": "3", "agent": "coder", "task": "Publish result", "need": ["2"]}],
            ]
            if family == "missing_dependency":
                plan[1][1]["need"] = ["99"]
            elif family == "forward_dependency":
                plan[1][1]["need"] = ["3"]
            elif family == "numeric_dependency":
                plan[1][1]["need"] = [1]
            elif family == "duplicate_id":
                plan[1][1]["id"] = "1"
            cases.append({"id": f"{family}-{variant + 1}", "family": family,
                          "numbers": numbers, "expected_sum": sum(numbers), "plan": plan})
    return {"version": "1.0", "cases": cases,
            "scope": "12 synthetic fault families x 3 value variants; variants are not independent task families"}


async def replay(cls, case, mode):
    agent = cls.__new__(cls)
    agent.max_plan_attempts = 3
    agent.max_step_retries = 0 if mode == "current_no_retry" else 1
    agent.max_replans = 2
    agent.max_total_steps = 12
    agent.last_plan_attempts = 1
    agent.stop = False
    agent.status_message = ""
    agent.logger = MagicMock()
    agent.checkpoint_store = None
    agent.agents = {}
    agent.llm = None
    agent.node_retry_policy = MagicMock()
    agent.node_retry_policy.delay.return_value = 0
    plan = copy.deepcopy(case["plan"])
    agent.make_plan = AsyncMock(return_value=plan)
    # Identical NO_UPDATE behavior for all arms, including after failures.
    agent.update_plan = AsyncMock(side_effect=lambda *args, **kwargs: args[1])
    trace = []
    attempts = {}
    family = case["family"]
    with tempfile.TemporaryDirectory(prefix="workflow-ab-") as temp:
        artifact = Path(temp) / "result.json"

        async def execute(task, infos):
            name = task["task"]
            attempts[name] = attempts.get(name, 0) + 1
            entry = {"id": task["id"], "task": name, "attempt": attempts[name], "inputs": dict(infos or {})}
            trace.append(entry)
            if name == "Read input numbers":
                output = (json.dumps(case["numbers"]), True)
            elif name == "Calculate sum":
                first = attempts[name] == 1
                if family == "permanent_timeout" or (family == "transient_timeout" and first):
                    entry["exception"] = "TimeoutError: request timed out"
                    raise TimeoutError("request timed out")
                if family == "permanent_false" or (family == "transient_false" and first):
                    output = ("temporary_error: calculation unavailable", False)
                elif family == "empty_success":
                    output = ("", True)
                elif family == "early_exit":
                    output = ("cannot complete this calculation", True)
                else:
                    try:
                        values = json.loads((infos or {}).get("1", "null"))
                        value = sum(values)
                        if family == "semantic_wrong":
                            value += 1
                        output = (json.dumps({"sum": value}), True)
                    except (TypeError, ValueError):
                        output = ("missing input data", False)
            else:
                try:
                    value = json.loads((infos or {}).get("2", "null"))["sum"]
                    artifact.write_text(json.dumps({"sum": value}), encoding="utf-8")
                except (TypeError, ValueError, KeyError):
                    pass
                # Deliberately overconfident downstream node in EVERY arm.
                output = ("COMPLETE: report published", True)
            entry.update(answer=output[0], reported_success=output[1])
            return output

        agent.start_agent_process = execute
        answer, reasoning, error = "", "", None
        try:
            answer, reasoning = await asyncio.wait_for(agent.process("Sum input numbers and publish result.json", None), 5)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        produced = json.loads(artifact.read_text(encoding="utf-8")) if artifact.exists() else None
        correct = produced == {"sum": case["expected_sum"]}
        state = json.loads(reasoning).get("state") if reasoning else None
        claimed = state == "succeeded" if state else answer.startswith("COMPLETE:")
        return {"id": case["id"], "family": family, "mode": mode,
                "task_success": correct, "claimed_success": claimed,
                "false_success": claimed and not correct,
                "artifact": produced, "expected_sum": case["expected_sum"],
                "answer": answer, "state": state, "exception": error,
                "node_calls": len(trace), "trace": trace,
                "reasoning": json.loads(reasoning) if reasoning else None}


def metrics(rows):
    n = len(rows)
    claims = sum(r["claimed_success"] for r in rows)
    false = sum(r["false_success"] for r in rows)
    return {"n": n, "successes": sum(r["task_success"] for r in rows),
            "task_success_rate": sum(r["task_success"] for r in rows) / n,
            "false_successes": false, "false_success_rate_all": false / n,
            "claimed_successes": claims, "false_success_rate_claimed": false / claims if claims else None,
            "node_calls": sum(r["node_calls"] for r in rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-root", type=Path, default=Path(r"E:\CodeFile\agenticSeek - 副本"))
    parser.add_argument("--output", type=Path, default=ROOT / "test_outputs/workflow_ab_v1")
    parser.add_argument("--worker", choices=["legacy", "current", "current_no_retry"])
    parser.add_argument("--project-root", type=Path)
    args = parser.parse_args()
    if args.worker:
        os.environ["AGENTICSEEK_PROJECT_ROOT"] = str(args.project_root)
        # Reuse dependency stubs only; no test case from this helper is executed.
        with contextlib.redirect_stdout(io.StringIO()):
            namespace = runpy.run_path(str(ROOT / "benchmarks/test_planner_recovery_compat.py"), run_name="workflow_import")
            cls = namespace["PlannerAgent"]
            dataset = json.loads((args.output / "dataset.json").read_text(encoding="utf-8"))
            rows = [asyncio.run(replay(cls, case, args.worker)) for case in dataset["cases"]]
        module_path = Path(sys.modules[cls.__module__].__file__).resolve()
        assert module_path == (args.project_root / "sources/agents/planner_agent.py").resolve()
        (args.output / f"{args.worker}.json").write_text(json.dumps({"source": str(module_path),
            "source_sha256": hashlib.sha256(module_path.read_bytes()).hexdigest(), "rows": rows}, indent=2, ensure_ascii=False), encoding="utf-8")
        return
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "dataset.json").write_text(json.dumps(build_dataset(), indent=2, ensure_ascii=False), encoding="utf-8")
    summary = {}
    for mode, root in (("legacy", args.legacy_root), ("current", ROOT), ("current_no_retry", ROOT)):
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", mode,
                        "--project-root", str(root), "--output", str(args.output.resolve())],
                       cwd=ROOT, check=True, timeout=60)
        rows = json.loads((args.output / f"{mode}.json").read_text(encoding="utf-8"))["rows"]
        summary[mode] = {"overall": metrics(rows), "families": {
            family: metrics([r for r in rows if r["family"] == family]) for family in FAMILIES}}
    (args.output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({mode: data["overall"] for mode, data in summary.items()}, indent=2))


if __name__ == "__main__":
    main()
