from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "dataset_v0.1.json"
CATEGORIES = {"single_tool", "multi_tool", "code", "file", "long_horizon", "robustness_safety"}

def load_dataset() -> dict:
    return json.loads(DATASET.read_text(encoding="utf-8"))

def validate(data: dict) -> list[str]:
    errors, cases = [], data.get("cases", [])
    ids = [c.get("id") for c in cases]
    dup = [x for x, n in Counter(ids).items() if n > 1]
    if dup: errors.append(f"duplicate ids: {dup}")
    if len(cases) != 30: errors.append(f"expected 30 cases, found {len(cases)}")
    missing = CATEGORIES - {c.get("category") for c in cases}
    if missing: errors.append(f"missing categories: {sorted(missing)}")
    routes = set(data.get("route_labels", []))
    for i, case in enumerate(cases):
        tag = f"case[{i}] {case.get('id', '<missing>')}"
        for field in ("id", "category", "difficulty", "language", "prompt", "expected", "limits"):
            if field not in case: errors.append(f"{tag}: missing {field}")
        exp = case.get("expected", {})
        if exp.get("route") not in routes: errors.append(f"{tag}: invalid route")
        if exp.get("planner") and exp.get("route") != "planification": errors.append(f"{tag}: planner route mismatch")
        if case.get("fixture") and not (ROOT / case["fixture"]).exists(): errors.append(f"{tag}: missing fixture")
        lim = case.get("limits", {})
        if lim.get("max_steps", 0) < 1 or lim.get("max_latency_ms", 0) < 1: errors.append(f"{tag}: invalid limits")
    return errors

def main() -> int:
    data = load_dataset(); errors = validate(data)
    counts = Counter(c["category"] for c in data["cases"])
    print(f"dataset={DATASET.name} version={data['version']} cases={len(data['cases'])}")
    print("categories=" + ", ".join(f"{k}:{v}" for k, v in sorted(counts.items())))
    for error in errors: print(f"ERROR: {error}")
    print("validation=" + ("FAIL" if errors else "PASS"))
    return int(bool(errors))

if __name__ == "__main__": raise SystemExit(main())
