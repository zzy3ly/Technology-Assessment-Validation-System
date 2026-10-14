from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from dotenv import load_dotenv

from benchmarks.score_reflection_ab import DATASET, objective_score
from sources.agents.reflection_agent import ReflectionAgent
from sources.llm_provider import Provider

ROOT = Path(__file__).resolve().parents[1]


async def run(output: Path) -> dict:
    load_dotenv(ROOT / ".env")
    cases = json.loads(DATASET.read_text(encoding="utf-8"))["cases"]
    provider = Provider("deepseek", "deepseek-chat", "https://api.deepseek.com/v1", is_local=False)
    agent = ReflectionAgent("Reflection", "prompts/base/reflection_agent.txt", provider, verbose=False)
    rows = []
    for case in cases:
        before = objective_score(case, case["answer"])
        started = time.perf_counter()
        revised, reasoning = await agent.process({
            "query": case["query"],
            "answer": case["answer"],
            "requirements": case.get("requirements", []),
            "evidence": case.get("evidence", []),
            "tool_status": case.get("tool_status", []),
            "forbidden": case.get("forbidden", []),
        }, None)
        latency_ms = round((time.perf_counter() - started) * 1000)
        after = objective_score(case, revised)
        row = {
            "case_id": case["id"],
            "before_answer": case["answer"],
            "after_answer": revised,
            "before": before,
            "after": after,
            "delta": round(after["score"] - before["score"], 2),
            "latency_ms": latency_ms,
            "reflection": json.loads(reasoning),
        }
        rows.append(row)
        print(f"{case['id']}: {before['score']:.2f} -> {after['score']:.2f} ({row['delta']:+.2f})")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    before_mean = sum(row["before"]["score"] for row in rows) / len(rows)
    after_mean = sum(row["after"]["score"] for row in rows) / len(rows)
    summary = {
        "cases": len(rows),
        "before_mean": round(before_mean, 2),
        "after_mean": round(after_mean, 2),
        "delta": round(after_mean - before_mean, 2),
        "relative_improvement_percent": round(100 * (after_mean - before_mean) / before_mean, 2),
        "improved": sum(row["delta"] > 0 for row in rows),
        "unchanged": sum(row["delta"] == 0 for row in rows),
        "regressed": sum(row["delta"] < 0 for row in rows),
        "mean_latency_ms": round(sum(row["latency_ms"] for row in rows) / len(rows)),
        "token_usage": provider.usage_totals,
    }
    summary_path = output.with_suffix(".summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "test_outputs" / "reflection_ab.jsonl")
    args = parser.parse_args()
    asyncio.run(run(args.output))


if __name__ == "__main__":
    main()
