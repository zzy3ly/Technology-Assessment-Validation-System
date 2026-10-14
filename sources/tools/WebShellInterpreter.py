"""Platform-aware shell for the Web worker, using real exit codes and timeouts."""
import os
import shutil
import subprocess
import sys

from sources.tools.BashInterpreter import BashInterpreter
from sources.tools.safety import is_any_unsafe


class WebShellInterpreter(BashInterpreter):
    def __init__(self):
        super().__init__()
        self.execution_failed = False

    def execute(self, commands, safety=False, timeout=90):
        self.execution_failed = False
        if self.safe_mode and is_any_unsafe(commands):
            self.execution_failed = True
            return 'Command failed: unsafe command rejected by safe mode.'
        if safety:
            self.execution_failed = True
            return 'Command failed: interactive confirmation is unavailable in the Web worker.'
        code = '\n'.join(commands)
        if os.name == 'nt':
            shell = shutil.which('pwsh') or shutil.which('powershell.exe')
            if not shell:
                self.execution_failed = True
                return 'Command failed: PowerShell is not installed.'
            code = "$ErrorActionPreference='Stop'\n" + code + '\nif ($LASTEXITCODE) { exit $LASTEXITCODE }'
            args = [shell, '-NoProfile', '-NonInteractive', '-Command', code]
        else:
            args = ['/bin/bash', '-eo', 'pipefail', '-c', code]
        env = {**os.environ, 'PYTHONIOENCODING': 'utf-8',
               'PATH': str(os.path.dirname(sys.executable)) + os.pathsep + os.environ.get('PATH', '')}
        try:
            result = subprocess.run(args, cwd=self.work_dir, capture_output=True,
                                    text=True, encoding='utf-8', errors='replace', timeout=timeout, env=env)
            self.execution_failed = result.returncode != 0
            return f'[exit_code={result.returncode}]\n' + (result.stdout + result.stderr)[-32000:]
        except subprocess.TimeoutExpired:
            self.execution_failed = True
            return f'Command failed: timeout after {timeout} seconds.'
        except OSError as exc:
            self.execution_failed = True
            return f'Command failed: {type(exc).__name__}: {exc}'

    def execution_failure_check(self, output):
        return self.execution_failed
