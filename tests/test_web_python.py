import asyncio
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from sources.tools.WebPythonInterpreter import WebPythonInterpreter


class WebPythonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        with patch.dict(os.environ, {'WORK_DIR': self.temp.name}):
            self.tool = WebPythonInterpreter()
        self.tool.work_dir = self.temp.name

    def tearDown(self):
        self.temp.cleanup()

    def test_code_cannot_change_server_cwd_or_stdout(self):
        before = os.getcwd(), sys.stdout
        result = self.tool.execute(['import os; os.chdir(".."); print("isolated")'])
        self.assertIn('isolated', result)
        self.assertEqual(before, (os.getcwd(), sys.stdout))

    def test_asyncio_code_runs_inside_web_event_loop(self):
        async def run():
            return self.tool.execute(['import asyncio\nasync def main():\n print("async-ok")\nasyncio.run(main())'])
        self.assertIn('async-ok', asyncio.run(run()))

    def test_exit_code_not_words_decides_python_failure(self):
        result = self.tool.execute(['print("no errors, zero failed assertions")'])
        self.assertFalse(self.tool.execution_failure_check(result))
        result = self.tool.execute(['raise ValueError("bad input")'])
        self.assertTrue(self.tool.execution_failure_check(result))
        self.assertIn('[exit_code=1]', result)

    def test_timeout_is_bounded(self):
        self.tool.timeout_seconds = .1
        result = self.tool.execute(['import time; time.sleep(5)'])
        self.assertTrue(self.tool.execution_failure_check(result))
        self.assertIn('timeout', result)


if __name__ == '__main__':
    unittest.main()
