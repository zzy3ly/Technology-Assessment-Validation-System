import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, AsyncMock

from sources.answer_verification import BoundedPython, Unsupported, verification
from sources.agents.reflection_agent import ReflectionAgent
from sources.agents.evaluation_agent import EvaluationAgent
from sources.agents.planner_agent import PlannerAgent
from sources.workflow_state_machine import WorkflowContext

DATE_QUERY = '从start之后数5个工作日，不含start；周六日和指定holiday不算工作日。输出due（YYYY-MM-DD字符串）。只输出JSON。'
DATE_EVIDENCE = ['start=2027-01-01; holiday=2027-01-04']


def make_agent(cls, replies=None):
    agent = cls.__new__(cls)
    agent.system_prompt = 'review'
    agent.verbose = False
    agent.tools = {'json': MagicMock()}
    agent.llm = MagicMock()
    if replies is not None:
        agent.llm.respond.side_effect = replies
    return agent


class VerifierTests(unittest.TestCase):
    def test_non_benchmark_date_and_loop(self):
        self.assertEqual(verification(DATE_QUERY, DATE_EVIDENCE)['expected'], {'due': '2027-01-11'})
        query = '按Python语义推演，输出total（整数）和seen（整数数组）。只输出JSON。'
        code = 'total=0\nseen=[]\nfor n in range(6):\n    if n%2==0: continue\n    total+=n\n    seen.append(total)'
        self.assertEqual(verification(query, [code])['expected'], {'total': 9, 'seen': [1, 4, 9]})

    def test_unsafe_and_unbounded_programs_rejected(self):
        for code in ('import os', 'x=open("secret")', 'x=2**999999', 'x=[1]*999999',
                     'for i in range(999999999):\n    x=1', 'x=[]\nx.append(x)', 'while True: pass'):
            with self.subTest(code=code), self.assertRaises((Unsupported, ValueError)):
                BoundedPython().run(code)

    def test_ambiguous_calendar_abstains(self):
        self.assertFalse(verification(DATE_QUERY.replace('不含start', '包含start'), DATE_EVIDENCE)['supported'])
        self.assertFalse(verification(DATE_QUERY, DATE_EVIDENCE + ['extra holiday'])['supported'])

    def test_unknown_output_variable_abstains(self):
        self.assertFalse(verification('Python推演，输出x（整数）和missing（整数）。JSON', ['x=1'])['supported'])


class ReviewGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_original_skips_model(self):
        agent = make_agent(ReflectionAgent)
        original = '{"due":"2027-01-11"}'
        answer, info = await agent.process({'query': DATE_QUERY, 'evidence': DATE_EVIDENCE, 'answer': original}, None)
        self.assertEqual(answer, original)
        agent.llm.respond.assert_not_called()

    async def test_bad_revision_rejected_despite_confidence(self):
        reply = json.dumps({'verdict':'revise', 'confidence':1, 'issues':['date'], 'revised_answer':'{"due":"2027-01-07"}'})
        agent = make_agent(ReflectionAgent, [reply])
        answer, info = await agent.process({'query':DATE_QUERY, 'evidence':DATE_EVIDENCE, 'answer':'{"due":"2027-01-08"}'}, None)
        self.assertEqual(answer, '{"due":"2027-01-08"}')
        self.assertEqual(json.loads(info)['decision'], 'unverified_revision_rejected')

    async def test_verified_candidate_wins_without_judge(self):
        agent = make_agent(EvaluationAgent)
        answer, info = await agent.process({'query':DATE_QUERY, 'evidence':DATE_EVIDENCE,
                                           'before':'{"due":"2027-01-08"}', 'after':'{"due":"2027-01-11"}'}, None)
        self.assertEqual(json.loads(answer)['due'], '2027-01-11')
        agent.llm.respond.assert_not_called()

    async def test_invalid_or_contradictory_judge_keeps_original(self):
        replies = ['[]', '{"winner":"after","score_before":9,"score_after":2,"reason":"contradiction"}',
                   '{"winner":"after","score_before":0,"score_after":NaN,"reason":"bad"}',
                   '{"winner":"after","score_before":1,"score_after":9,"reason":"bad","checks":[{"before":"pass","after":"fail"}]}']
        for reply in replies:
            agent = make_agent(EvaluationAgent, [reply])
            answer, info = await agent.process({'before':'A', 'after':'B'}, None)
            self.assertEqual(answer, 'A')
            self.assertFalse(json.loads(info)['valid'])

    async def test_position_bias_keeps_original(self):
        vote = json.dumps({'winner':'after','score_before':2,'score_after':9,'reason':'preference',
                           'checks':[{'before':'unknown','after':'unknown'}]})
        agent = make_agent(EvaluationAgent, [vote, vote])
        answer, info = await agent.process({'before':'A','after':'B'}, None)
        self.assertEqual(answer, 'A')
        self.assertEqual(len(json.loads(info)['judge_rounds']), 2)

    async def test_provider_error_preserves_answer(self):
        for cls, payload in ((ReflectionAgent, {'answer':'A'}), (EvaluationAgent, {'before':'A','after':'B'})):
            agent = make_agent(cls, [TimeoutError('timeout')])
            answer, info = await agent.process(payload, None)
            self.assertEqual(answer, 'A')


class PlannerReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_planner_executes_real_review_agents(self):
        planner = PlannerAgent.__new__(PlannerAgent)
        planner.logger = MagicMock()
        planner.checkpoint_store = None
        planner.llm = None
        planner.max_plan_attempts = 3
        planner.max_step_retries = 1
        planner.max_replans = 1
        planner.max_total_steps = 12
        plan = [
            ['facts', {'id':'1','agent':'file','task':'read','need':[]}],
            ['draft', {'id':'2','agent':'casual','task':'answer','need':['1']}],
            ['review', {'id':'3','agent':'reflection','task':'review','need':['2']}],
            ['select', {'id':'4','agent':'evaluation','task':'select','need':['2','3']}],
        ]
        planner.make_plan = AsyncMock(return_value=plan)
        planner.update_plan = AsyncMock(return_value=plan)
        def child(answer):
            obj = MagicMock()
            obj.process = AsyncMock(return_value=(answer, ''))
            obj.raw_answer_blocks.side_effect = lambda text: text
            obj.get_success = True
            return obj
        revised = '{"due":"2027-01-11"}'
        reflection = make_agent(ReflectionAgent, [json.dumps({'verdict':'revise','confidence':0.9,
                    'issues':['verified date mismatch'],'revised_answer':revised})])
        evaluation = make_agent(EvaluationAgent)
        for obj in (reflection, evaluation):
            obj.blocks_result = []
            obj.show_answer = MagicMock()
        planner.agents = {'file':child(DATE_EVIDENCE[0]), 'casual':child('{"due":"2027-01-08"}'),
                          'reflection':reflection, 'evaluation':evaluation}
        answer, info = await planner.process(DATE_QUERY, None)
        self.assertEqual(answer, revised)
        self.assertEqual(json.loads(info)['state'], 'succeeded')
        self.assertEqual(planner.last_workflow_context.review_contexts['3']['before'], '{"due":"2027-01-08"}')
        evaluation.llm.respond.assert_not_called()

    async def test_structured_handoff_survives_checkpoint(self):
        planner = PlannerAgent.__new__(PlannerAgent)
        planner.logger = MagicMock()
        context = WorkflowContext(DATE_QUERY)
        context.install_plan([
            ['evidence', {'id':'1','agent':'file','task':'read','need':[]}],
            ['draft', {'id':'2','agent':'casual','task':'draft','need':['1']}],
            ['reflect', {'id':'3','agent':'reflection','task':'review','need':['2']}],
            ['judge', {'id':'4','agent':'evaluation','task':'judge','need':['2','3']}],
        ])
        context.results = {'1': DATE_EVIDENCE[0] + '\nAgent succeeded with task.',
                           '2': '{"due":"2027-01-08"}\nAgent succeeded with task.'}
        planner.last_workflow_context = context
        reflect = MagicMock()
        reflect.process = AsyncMock(return_value=('{"due":"2027-01-11"}', '{}'))
        reflect.get_success = True
        planner.agents = {'reflection': reflect}
        answer, ok = await planner.start_agent_process(context.plan[2][1], {'2': context.results['2']})
        payload = reflect.process.await_args.args[0]
        self.assertIsInstance(payload, dict)
        self.assertEqual(payload['query'], DATE_QUERY)
        self.assertEqual(payload['evidence'], DATE_EVIDENCE)
        self.assertEqual(json.loads(answer)['due'], '2027-01-11')
        context.results['3'] = answer
        planner.last_workflow_context = WorkflowContext.from_snapshot(context.snapshot())
        judge = planner._review_payload(context.plan[3][1], {'2':context.results['2'], '3':answer})
        self.assertEqual(json.loads(judge['before'])['due'], '2027-01-08')
        self.assertEqual(judge['after'], answer)


class LiveExperimentAudit(unittest.TestCase):
    def test_frozen_results_and_independent_oracles(self):
        root = Path(__file__).resolve().parents[1] / 'test_outputs/reflection_optimization_v2'
        from benchmarks.run_reflection_objective import score
        from benchmarks.run_reflection_optimization import metrics
        for split in ('regression', 'holdout'):
            if not (root / split / 'summary.json').exists():
                self.skipTest('Run both experiments first')
            cases = json.loads((root / split / 'dataset.json').read_text(encoding='utf-8'))['cases']
            rows = []
            for case in cases:
                row = json.loads((root / split / case['id'] / 'result.json').read_text(encoding='utf-8'))
                for flag, text in (('baseline_correct','baseline_answer'), ('old_correct','old_final_answer'),
                                   ('new_reflected_correct','new_reflected_answer'), ('new_correct','new_final_answer')):
                    self.assertEqual(row[flag], score(case, row[text])['correct'])
                check = verification(case['query'], case['evidence'])
                if check['supported']:
                    self.assertEqual(check['expected'], case['expected'])
                rows.append(row)
            saved = json.loads((root / split / 'summary.json').read_text(encoding='utf-8'))
            for key, value in metrics(rows).items():
                self.assertEqual(saved[key], value)


if __name__ == '__main__':
    unittest.main()
