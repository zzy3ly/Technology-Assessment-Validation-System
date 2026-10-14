import json
import math

from sources.answer_verification import matches, verification

from sources.agents.agent import Agent
from sources.memory import Memory
from sources.tools.tools import Tools
from sources.utility import pretty_print


class EvaluationAgent(Agent):
    def __init__(self, name, prompt_path, provider, verbose=False):
        super().__init__(name, prompt_path, provider, verbose, None)
        self.tools = {"json": Tools()}
        self.tools["json"].tag = "json"
        self.role = "evaluation"
        self.type = "evaluation_agent"
        self.system_prompt = self.load_prompt(prompt_path)
        self.memory = Memory(
            self.system_prompt,
            recover_last_session=False,
            memory_compression=False,
            model_provider=provider.get_model_name()
        )

    def parse_json_response(self, text: str) -> dict:
        if not text:
            return {}
        try:
            value = json.loads(text.strip())
            return value if isinstance(value, dict) else {}
        except Exception:
            pass
        try:
            parsed = self.tools["json"].load_exec_block(text)
            blocks = parsed[0] if isinstance(parsed, tuple) and len(parsed) == 2 else None
        except Exception:
            blocks = None
        if blocks:
            try:
                value = json.loads(blocks[0].strip())
                return value if isinstance(value, dict) else {}
            except Exception:
                return {}
        return {}

    def _judge(self, query, requirements, evidence, tool_status, before, after):
        payload = {"query": query, "requirements": requirements, "verified_evidence": evidence,
                   "tool_status": tool_status, "candidate_before": before, "candidate_after": after}
        raw = self.llm.respond([{"role": "system", "content": self.system_prompt},
                                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}], self.verbose)
        data = self.parse_json_response(raw)
        valid = data.get("winner") in ("before", "after") and isinstance(data.get("reason"), str)
        scores = [data.get("score_before"), data.get("score_after")]
        valid = valid and all(type(s) in (int, float) and math.isfinite(s) and 0 <= s <= 10 for s in scores)
        if valid:
            expected_winner = "after" if scores[1] > scores[0] else "before"
            valid = data["winner"] == expected_winner
        checks = data.get("checks", [])
        valid = valid and isinstance(checks, list) and bool(checks)
        for check in checks if isinstance(checks, list) else []:
            if not isinstance(check, dict) or not all(check.get(k) in ("pass", "fail", "unknown") for k in ("before", "after")):
                valid = False
            elif check.get("before") == "pass" and check.get("after") != "pass" and data.get("winner") == "after":
                valid = False
            elif check.get("after") == "pass" and check.get("before") != "pass" and data.get("winner") == "before":
                valid = False
        return {**data, "valid": bool(valid)}

    async def process(self, prompt, speech_module):
        if isinstance(prompt, dict):
            before = str(prompt.get("before", ""))
            after = str(prompt.get("after", ""))
            query = str(prompt.get("query", ""))
            requirements = prompt.get("requirements", [])
            evidence = prompt.get("evidence", [])
            tool_status = prompt.get("tool_status", [])
        else:
            before, after, query = str(prompt), "", ""
            requirements, evidence = [], []
            tool_status = []
        check = verification(query, evidence)
        winner, decision, valid = "before", "conservative_default", bool(before)
        rounds = []
        if before == after:
            decision = "identical_candidates"
        elif check["supported"]:
            winner = "after" if matches(after, check["expected"]) and not matches(before, check["expected"]) else "before"
            decision = "deterministic_verification"
        elif before and after:
            try:
                forward = self._judge(query, requirements, evidence, tool_status, before, after)
                rounds.append(forward)
                valid = forward["valid"]
                if valid and forward["winner"] == "after" and forward["score_after"] - forward["score_before"] >= 1:
                    reverse = self._judge(query, requirements, evidence, tool_status, after, before)
                    rounds.append(reverse)
                    valid = reverse["valid"]
                    if valid and reverse["winner"] == "before" and reverse["score_before"] - reverse["score_after"] >= 1:
                        winner, decision = "after", "consistent_bidirectional_judge"
                    else:
                        decision = "inconsistent_judge_keep_original"
                else:
                    decision = "no_supported_improvement"
            except Exception as exc:
                valid = False
                decision = "judge_unavailable_keep_original"
                rounds.append({"error_type": type(exc).__name__})
        final_text = after if winner == "after" else before

        result = {
            "valid": valid,
            "winner": winner,
            "score_before": rounds[0].get("score_before") if rounds else None,
            "score_after": rounds[0].get("score_after") if rounds else None,
            "reason": decision,
            "judge_rounds": rounds,
            "verification": check,
            "final_text": final_text,
        }
        self.success = bool(final_text.strip())
        self.last_answer = final_text
        self.last_reasoning = json.dumps(result, ensure_ascii=False)
        return final_text, self.last_reasoning
