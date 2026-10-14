import json
import unittest
from unittest.mock import MagicMock

from benchmarks.score_reflection_ab import objective_score
from sources.agents.evaluation_agent import EvaluationAgent
from sources.agents.reflection_agent import ReflectionAgent


class FakeProvider:
    def __init__(self, response): self.response = response
    def respond(self, *_): return self.response.pop(0) if isinstance(self.response, list) else self.response
    def get_model_name(self): return "fake"


class ReflectionEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def reflection(self, response):
        agent = ReflectionAgent.__new__(ReflectionAgent)
        agent.llm = FakeProvider(response); agent.verbose = False
        agent.system_prompt = "reflect"; agent.tools = {"json": MagicMock()}
        return agent

    def evaluator(self, response):
        agent = EvaluationAgent.__new__(EvaluationAgent)
        agent.llm = FakeProvider(response); agent.verbose = False
        agent.system_prompt = "evaluate"; agent.tools = {"json": MagicMock()}
        return agent

    async def test_invalid_reflection_keeps_original(self):
        agent = self.reflection("not json")
        answer, reasoning = await agent.process({"query":"q", "answer":"original"}, None)
        self.assertEqual(answer, "original")
        self.assertFalse(json.loads(reasoning)["valid"])

    async def test_structured_revision_is_used(self):
        raw = json.dumps({"verdict":"revise", "confidence":0.9,
                          "issues":[{"code":"tool_failure_ignored","severity":"high","description":"fake success"}],
                          "revised_answer":"测试失败：AssertionError。"}, ensure_ascii=False)
        answer, reasoning = await self.reflection(raw).process({"query":"run", "answer":"全部通过"}, None)
        self.assertIn("AssertionError", answer)
        self.assertTrue(json.loads(reasoning)["changed"])

    async def test_evaluator_cannot_author_third_answer(self):
        raw = json.dumps({"winner":"after", "score_before":2, "score_after":9,
                          "reason":"better", "final_text":"fabricated third answer",
                          "checks":[{"before":"fail", "after":"pass"}]})
        reverse = json.dumps({"winner":"before", "score_before":9, "score_after":2,
                              "reason":"better", "checks":[{"before":"pass", "after":"fail"}]})
        answer, reasoning = await self.evaluator([raw, reverse]).process({"before":"A", "after":"B"}, None)
        self.assertEqual(answer, "B")
        self.assertEqual(json.loads(reasoning)["final_text"], "B")

    async def test_tie_keeps_original(self):
        raw = json.dumps({"winner":"after", "score_before":8, "score_after":8, "reason":"tie"})
        answer, _ = await self.evaluator(raw).process({"before":"original", "after":"revision"}, None)
        self.assertEqual(answer, "original")

    def test_objective_scorer_rewards_honest_repair(self):
        case = {"required_any":[["失败","AssertionError"]], "forbidden":["全部通过"], "preserve":[]}
        self.assertGreater(objective_score(case, "测试失败：AssertionError")["score"],
                           objective_score(case, "测试全部通过")["score"])


if __name__ == "__main__": unittest.main()
