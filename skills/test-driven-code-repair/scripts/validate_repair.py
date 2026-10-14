from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    work_dir = args.work_dir.resolve()
    if not work_dir.is_dir():
        print(json.dumps({"passed": False, "errors": ["Work directory does not exist"]}))
        return 1
    tests = list(work_dir.rglob("test_*.py")) + list(work_dir.rglob("*_test.py"))
    if not tests:
        print(json.dumps({"passed": False, "errors": ["No Python tests found"]}))
        return 1
    pytest_available = importlib.util.find_spec("pytest") is not None
    command = (
        [sys.executable, "-m", "pytest", "-q"]
        if pytest_available
        else [sys.executable, "-m", "unittest", "discover", "-v"]
    )
    try:
        result = subprocess.run(
            command, cwd=work_dir,
            capture_output=True, text=True, timeout=120, check=False,
        )
        combined_output = f"{result.stdout}\n{result.stderr}"
        no_tests_ran = (
            "Ran 0 tests" in combined_output
            or "no tests ran" in combined_output.lower()
        )
        payload = {
            "passed": result.returncode == 0 and not no_tests_ran,
            "errors": (
                ["The test runner completed without executing any tests"]
                if no_tests_ran
                else ([] if result.returncode == 0 else ["Full test suite failed"])
            ),
            "command": command,
            "runner": "pytest" if pytest_available else "unittest",
            "returncode": result.returncode,
            "stdout_tail": result.stdout[-4000:],
            "stderr_tail": result.stderr[-2000:],
            "test_files": len(set(tests)),
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if payload["passed"] else 1
    except subprocess.TimeoutExpired:
        print(json.dumps({"passed": False, "errors": ["Full test suite timed out"]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
