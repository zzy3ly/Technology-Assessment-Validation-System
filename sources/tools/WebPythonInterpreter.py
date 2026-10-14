"""Process isolation for Web Python tools (not an OS security sandbox)."""
import os
import subprocess
import sys

from sources.tools.PyInterpreter import PyInterpreter


class WebPythonInterpreter(PyInterpreter):
    def __init__(self):
        super().__init__()
        self.execution_failed = False
        self.timeout_seconds = 90

    def execute(self, codes, safety=False):
        self.execution_failed = False
        if safety:
            self.execution_failed = True
            return "Code execution failed: interactive confirmation is unavailable in the Web worker."
        try:
            result = subprocess.run(
                [sys.executable, '-u', '-c', '\n\n'.join(codes)],
                cwd=self.work_dir, capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=self.timeout_seconds, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
            )
            self.execution_failed = result.returncode != 0
            output = (result.stdout + result.stderr)[-32000:]
            return f"[exit_code={result.returncode}]\n{output}"
        except subprocess.TimeoutExpired:
            self.execution_failed = True
            return f"Code execution failed: timeout after {self.timeout_seconds} seconds."
        except OSError as exc:
            self.execution_failed = True
            return f"Code execution failed: {type(exc).__name__}: {exc}"

    def execution_failure_check(self, output):
        return self.execution_failed
