"""Offline API lifecycle tests: no API quota, Chrome or search service needed."""
import asyncio
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from sources.checkpoint import CheckpointStore
from sources.web_api import create_app
from sources.web_runtime import public_workflow
from sources.workflow_state_machine import WorkflowContext, WorkflowEvent, WorkflowState

ROOT = Path(__file__).resolve().parents[1]


class FakeRuntime:
    def __init__(self):
        self.release = threading.Event()
        self.stopped = False
        self.request = None

    async def run(self, request):
        self.request = request
        while not self.release.is_set() and not self.stopped:
            await asyncio.sleep(0.005)
        if request['query'] == 'crash':
            raise RuntimeError('injected tool failure')
        return {'answer': 'real result in test fixture', 'status': 'failed' if request['query'] == 'failed' else 'succeeded'}

    def snapshot(self):
        return {'phase': 'fixture running', 'blocks': [], 'token_usage': {'total_tokens': 12}}

    def request_stop(self):
        self.stopped = True

    def close(self):
        pass


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.runtime = FakeRuntime()
        self.app = create_app(ROOT, lambda: self.runtime, Path(self.temp.name))
        self.client = TestClient(self.app)

    def tearDown(self):
        self.runtime.release.set()
        self.app.state.manager.executor.shutdown(wait=True)
        self.client.close()
        self.temp.cleanup()

    def submit(self, query='hello', **kwargs):
        response = self.client.post('/api/tasks', json={'query': query, **kwargs})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()['id']

    def finish(self, task_id):
        self.runtime.release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            row = self.client.get('/api/tasks/' + task_id).json()
            if row['status'] in ('succeeded', 'failed', 'stopped') and not self.app.state.manager.active_id:
                return row
            time.sleep(.01)
        self.fail('Task did not finish')

    def test_api_starts_without_models_and_lists_real_skills(self):
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertIn('test-driven-code-repair', [s['name'] for s in self.client.get('/api/skills').json()])

    def test_submit_nonblocking_and_reject_concurrent_tasks(self):
        started = time.monotonic()
        task_id = self.submit()
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.client.post('/api/tasks', json={'query': 'second'}).status_code, 409)
        self.assertTrue(self.finish(task_id)['success'])

    def test_polling_is_idempotent_and_does_not_clear_answer(self):
        task_id = self.submit()
        result = self.finish(task_id)
        for _ in range(3):
            self.assertEqual(self.client.get('/api/tasks/' + task_id).json()['answer'], result['answer'])
            self.assertEqual(self.client.get('/latest_answer').json()['uid'], task_id)

    def test_failed_workflow_is_not_success(self):
        row = self.finish(self.submit('failed'))
        self.assertEqual(row['status'], 'failed')
        self.assertIs(row['success'], False)
        self.assertEqual(self.client.get('/latest_answer').json()['success'], 'false')

    def test_exception_does_not_kill_server_or_block_next_task(self):
        row = self.finish(self.submit('crash'))
        self.assertEqual(row['status'], 'failed')
        self.assertIn('injected tool failure', row['error'])
        self.assertEqual(self.client.get('/api/health').status_code, 200)
        self.assertEqual(self.finish(self.submit())['status'], 'succeeded')

    def test_stop_is_cooperative_never_reported_success(self):
        task_id = self.submit()
        row = self.client.post('/api/tasks/' + task_id + '/stop').json()
        self.assertTrue(row['stop_requested'])
        self.assertIn(row['status'], ('stopping', 'stopped'))
        final = self.finish(task_id)
        self.assertEqual(final['status'], 'stopped')
        self.assertFalse(final['success'])

    def test_unknown_skill_and_blank_task_rejected_before_payment(self):
        for payload in ({'query': '  '}, {'query': 'test', 'skill': 'missing'}, {'query': '$missing test'}):
            self.assertEqual(self.client.post('/api/tasks', json=payload).status_code, 422)

    def test_skill_selection_forces_planner(self):
        row = self.finish(self.submit('repair', mode='casual', skill='test-driven-code-repair'))
        self.assertEqual(row['mode'], 'planner')
        self.assertEqual(self.runtime.request['skill'], 'test-driven-code-repair')

    def test_resume_only_eligible_checkpoint_and_preserves_id(self):
        store = self.app.state.manager.checkpoints
        store.save('resume-me', {'goal': 'continue', 'current_state': 'running', 'completed_steps': ['1'], 'plan': []})
        response = self.client.post('/api/checkpoints/resume-me/resume')
        self.assertEqual(response.status_code, 202)
        self.finish(response.json()['id'])
        self.assertEqual(self.runtime.request['checkpoint_id'], 'resume-me')
        for state in ('failed', 'succeeded'):
            store.save('terminal', {'goal': 'done', 'current_state': state})
            self.assertEqual(self.client.post('/api/checkpoints/terminal/resume').status_code, 409)
        self.assertEqual(self.client.get('/api/checkpoints/bad.id').status_code, 422)

    def test_restart_marks_unfinished_records_interrupted(self):
        store = self.app.state.manager.store
        store.save('interrupted-id', {'id': 'interrupted-id', 'status': 'running', 'created_at': '2020', 'query': 'x'})
        other = create_app(ROOT, lambda: self.runtime, Path(self.temp.name))
        self.assertEqual(other.state.manager.get('interrupted-id')['status'], 'interrupted')
        other.state.manager.close()

    def test_checkpoint_api_hides_model_messages(self):
        self.app.state.manager.checkpoints.save('safe', {'goal': 'x', 'current_state': 'running', 'model_messages': {'private': 'prompt'}})
        self.assertNotIn('model_messages', self.client.get('/api/checkpoints/safe').json())

    def test_foreign_websites_cannot_submit_code_execution(self):
        response = self.client.post('/api/tasks', json={'query': 'run'}, headers={'Origin': 'https://untrusted.example'})
        self.assertEqual(response.status_code, 403)

    def test_screenshot_does_not_leak_previous_task_image(self):
        task_id = self.submit()
        self.finish(task_id)
        self.assertEqual(self.client.get('/api/tasks/' + task_id + '/screenshot').status_code, 404)

    def test_explicit_resume_event_keeps_successful_node_result(self):
        context = WorkflowContext('demo')
        context.transition(WorkflowEvent.START_PLANNING)
        context.results['1'] = 'expensive successful output'
        context.transition(WorkflowEvent.STOP_REQUESTED)
        context.transition(WorkflowEvent.RESUME_REQUESTED)
        self.assertEqual(context.state, WorkflowState.READY)
        self.assertEqual(context.results['1'], 'expensive successful output')


if __name__ == '__main__':
    unittest.main()
