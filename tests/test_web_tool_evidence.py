import os
import tempfile
import unittest
from unittest.mock import patch

from sources.agents.agent import Agent
from sources.schemas import executorResult
from sources.tools.WebShellInterpreter import WebShellInterpreter


class EvidenceMappingTests(unittest.TestCase):
    def agent(self):
        agent = Agent.__new__(Agent)
        agent.blocks_result = [
            executorResult('print("old")', 'old failed output', False, 'python'),
            executorResult('Get-ChildItem', 'listing', True, 'bash'),
            executorResult('print(55)', '55', True, 'python'),
        ]
        agent._last_execution_indices = [1, 2]
        agent.last_answer = 'stale answer'
        return agent

    def test_current_response_uses_actual_tool_not_stale_zero_index(self):
        agent = self.agent()
        text = agent.remove_blocks('Result\n```python\nprint(55)\n```')
        self.assertIn('block:2', text)
        answer = agent.raw_answer_blocks(text)
        self.assertIn('Feedback: 55', answer)
        self.assertNotIn('old failed output', answer)

    def test_mapping_follows_fence_content_not_tool_registration_order(self):
        agent = self.agent()
        text = agent.remove_blocks('```python\nprint(55)\n```\n```bash\nGet-ChildItem\n```')
        self.assertEqual(text, 'block:2\nblock:1')

    def test_unknown_fence_cannot_borrow_previous_success(self):
        text = self.agent().remove_blocks('```python\nprint("not executed")\n```')
        self.assertIn('not executed', text)
        self.assertNotIn('block:2', text)


class WebShellTests(unittest.TestCase):
    def test_shell_uses_exit_codes_and_handles_paths_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix='web tool ') as folder:
            with patch.dict(os.environ, {'WORK_DIR': folder}):
                tool = WebShellInterpreter()
            tool.work_dir = folder
            output = tool.execute(['python -c "print(55)"'])
            self.assertIn('55', output)
            self.assertIn('[exit_code=0]', output)
            self.assertFalse(tool.execution_failure_check(output))
            output = tool.execute(['python -c "import sys; sys.exit(7)"'])
            self.assertTrue(tool.execution_failure_check(output))
            self.assertIn('[exit_code=7]', output)


if __name__ == '__main__':
    unittest.main()
