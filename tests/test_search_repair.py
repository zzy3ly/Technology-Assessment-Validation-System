import unittest
import tempfile
from unittest.mock import patch, Mock
from sources.tools.searxSearch import searxSearch


class SearchRepairTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        env = patch.dict('os.environ', {'WORK_DIR': directory.name})
        env.start()
        self.addCleanup(env.stop)

    def test_explicit_url(self):
        self.assertEqual(searxSearch('http://127.0.0.1:8080').base_url,
                         'http://127.0.0.1:8080')

    @patch('sources.tools.searxSearch.requests.post')
    def test_query_and_result(self, post):
        response = Mock()
        response.text = '<article class="result"><a class="url_header" href="https://example.com"></a><h3>Error handling</h3><p class="content">Documentation</p></article>'
        post.return_value = response
        tool = searxSearch('http://127.0.0.1:8080')
        result = tool.execute(['search: C++ & Python'])
        self.assertEqual(post.call_args.kwargs['data']['q'], 'C++ & Python')
        self.assertFalse(tool.execution_failure_check(result))
        self.assertIn('Link:https://example.com', result)

    def test_empty_and_failure(self):
        tool = searxSearch('http://127.0.0.1:8080')
        self.assertTrue(tool.execution_failure_check(tool.execute(['search: '])))


if __name__ == '__main__':
    unittest.main()
