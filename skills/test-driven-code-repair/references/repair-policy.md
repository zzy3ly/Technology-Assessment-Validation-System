# Repair policy

- Preserve existing tests unless the user explicitly asks to correct a demonstrably invalid test.
- Prefer a minimal production-code patch over broad refactoring.
- Do not install dependencies or access the network unless the task requires it and authorization permits it.
- If tests cannot run because of the environment, report the blocker instead of claiming success.
- The final verification must be a real test command, not an LLM assessment.
