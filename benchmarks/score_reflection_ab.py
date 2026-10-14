from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "benchmarks" / "reflection_eval_dataset.json"


def objective_score(case: dict, answer: str) -> dict:
    text = str(answer or "")
    lowered = text.lower()
    groups = case.get("required_any", [])
    hits = sum(any(str(term).lower() in lowered for term in group) for group in groups)
    requirement = hits / len(groups) if groups else 1.0
    forbidden_hits = sum(str(term).lower() in lowered for term in case.get("forbidden", []))
    safety = float(forbidden_hits == 0)
    preserve_items = case.get("preserve", [])
    preserved = sum(str(term) in text for term in preserve_items)
    preservation = preserved / len(preserve_items) if preserve_items else 1.0
    score = 100 * (0.55 * requirement + 0.25 * safety + 0.15 * preservation + 0.05 * bool(text.strip()))
    return {"score": round(score, 2), "requirement": requirement, "safety": safety,
            "preservation": preservation, "forbidden_hits": forbidden_hits}


def score_rows(rows: list[dict]) -> dict:
    cases = {item["id"]: item for item in json.loads(DATASET.read_text(encoding="utf-8"))["cases"]}
    scored = []
    for row in rows:
        case = cases[row["case_id"]]
        before = objective_score(case, row["before_answer"])
        after = objective_score(case, row["after_answer"])
        scored.append({"case_id": row["case_id"], "before": before, "after": after,
                       "delta": round(after["score"] - before["score"], 2)})
    n = len(scored)
    before_mean = sum(x["before"]["score"] for x in scored) / n
    after_mean = sum(x["after"]["score"] for x in scored) / n
    return {"cases": n, "before_mean": round(before_mean, 2), "after_mean": round(after_mean, 2),
            "delta": round(after_mean-before_mean, 2), "improved": sum(x["delta"] > 0 for x in scored),
            "unchanged": sum(x["delta"] == 0 for x in scored), "regressed": sum(x["delta"] < 0 for x in scored),
            "details": scored}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path, required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in args.results.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(json.dumps(score_rows(rows), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
