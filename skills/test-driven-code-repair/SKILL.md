---
name: test-driven-code-repair
description: Repair an existing codebase by reproducing its failing tests, making a minimal production-code change, and proving the fix with real test execution. Use for bug fixes with an existing repository or test suite; do not use for greenfield implementation.
metadata: {"version":"0.1.0","required_agents":["file","coder"],"required_tools":["file_finder","python"],"required_plan_agents":["file","coder"],"plan_requirements":[["test","pytest","unittest","测试"],["root cause","根因","定位"],["rerun","regression","重新运行","回归"]],"forbidden_plan_terms":["modify existing tests","修改已有测试","删除测试"],"validator":"scripts/validate_repair.py"}
---
# Test-driven code repair

Use the existing test suite as the source of truth.

1. Inspect the repository and identify the relevant production code and tests.
2. Run the failing test before editing and record the actual failure.
3. Identify the root cause; do not patch only the displayed symptom.
4. Make the smallest relevant production-code change. Do not modify or delete existing tests merely to obtain a pass.
5. Re-run the focused failing test, then run the full available test suite.
6. Report the root cause, changed files, commands run, and real test results. Never claim success without a passing command result.

Read [the repair policy](references/repair-policy.md) before planning.
