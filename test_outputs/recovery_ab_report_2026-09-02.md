# AgenticSeek failure-recovery A/B report

Date: 2026-09-02

## Compared versions

- Baseline: `E:\CodeFile\agenticSeek - 副本`
- Improved: `E:\CodeFile\agenticSeek`
- Python: each repository's own `.venv\Scripts\python.exe`

## Results

| Evaluation | Baseline | Improved | Delta |
|---|---:|---:|---:|
| Common pre-existing unit tests | 91/95 (95.79%) | 91/95 (95.79%) | 0.00 percentage points |
| Version-neutral Planner fault injection | 0/4 (0%) | 4/4 (100%) | +100 percentage points |
| Recovery/state/checkpoint regression suite | unsupported | 17/17 (100%) | new capability |

The same four common-suite errors occurred in both versions: one pre-existing
`Memory.tokenizer` initialization error and three SearxNG integration errors
while `http://127.0.0.1:8080` was offline. Therefore these four errors are not
attributed to the recovery changes.

## Version-neutral injected failures

| Scenario | Baseline observation | Improved observation |
|---|---|---|
| Transient failed node then success | Returned `temporary`; call count 1 | Retried only the node; returned `recovered`; call count 2 |
| Tool raises `TimeoutError` | Exception escaped and aborted caller | Converted to a terminal failed workflow result |
| Retry budget exhausted | Returned `bad-1` as final answer | Returned explicit failed state/result |
| Successful execution observability | Empty reasoning string | JSON metadata with `state=succeeded` |

## Recovery acceptance coverage

The 17 passing improved-version test methods contain 25 atomic recovery checks:

- 7 tool failure classifications;
- 4 model failure classifications;
- argument repair without mutating the original task;
- exponential backoff and retry;
- repeated trace detection;
- two checkpoint persistence/resume cases;
- five state-machine validation cases;
- five Planner execution/recovery cases.

The baseline does not contain `sources/recovery.py`, `sources/checkpoint.py`, or
`sources/workflow_state_machine.py`, and failed all four version-neutral runtime
acceptance cases. The improved version passed all 25 atomic checks.

## Behavioral dataset status

`dataset_v0.1.json` contains 30 cases: 22 offline-capable cases and 8 cases that
require web search. The repository currently has a validator and scorer but no
end-to-end benchmark runner. SearxNG was also offline during this comparison.
Consequently, no fabricated 30-case end-to-end score is reported here. The A/B
numbers above measure the recovery runtime deterministically, without LLM
sampling variance or paid API calls.

## Reproduction

Run the version-neutral suite from each repository with
`AGENTICSEEK_PROJECT_ROOT` pointing to that repository:

```powershell
.\.venv\Scripts\python.exe E:\CodeFile\agenticSeek\benchmarks\test_planner_recovery_compat.py
```

Run the improved recovery suite:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_recovery tests.test_checkpoint tests.test_workflow_state_machine tests.test_planner_workflow -v
```
