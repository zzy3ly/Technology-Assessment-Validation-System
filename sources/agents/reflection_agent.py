import json
import math

from sources.answer_verification import matches, verification

from sources.agents.agent import Agent
from sources.memory import Memory
from sources.tools.tools import Tools


class ReflectionAgent(Agent):
    def __init__(self, name, prompt_path, provider, verbose=False):
        super().__init__(name, prompt_path, provider, verbose, None)
        self.tools = {"json": Tools()}
        self.tools["json"].tag = "json"
        self.role = "reflection"
        self.type = "reflection_agent"
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

    async def process(self, prompt, speech_module):
        if isinstance(prompt, dict):
            query = prompt.get("query", "")
            answer = prompt.get("answer", "")
            reasoning = prompt.get("reasoning", "")
            requirements = prompt.get("requirements", [])
            evidence = prompt.get("evidence", [])
            tool_status = prompt.get("tool_status", [])
            forbidden = prompt.get("forbidden", [])
        else:
            # Called directly by PlannerAgent or others as a string
            query = "Task from Planner"
            answer = str(prompt)
            reasoning = ""
            requirements, evidence, tool_status, forbidden = [], [], [], []

        original_answer = str(answer)
        check = verification(str(query), evidence)
        if check["supported"] and matches(original_answer, check["expected"]):
            self.success = True
            self.last_answer = original_answer
            self.last_reasoning = json.dumps({"valid": True, "verdict": "keep", "changed": False,
                "issues": [], "verification": check, "decision": "verified_original", "model_called": False}, ensure_ascii=False)
            return self.last_answer, self.last_reasoning
        context = {
            "user_query": str(query),
            "current_answer": original_answer,
            "reasoning_notes": str(reasoning),
            "requirements": requirements,
            "verified_evidence": evidence,
            "tool_status": tool_status,
            "forbidden_content_or_actions": forbidden,
            "deterministic_check": check,
        }

        history = [
            {
                "role": "system",
                "content": self.system_prompt
            },
            {
                "role": "user",
                "content": json.dumps(context, ensure_ascii=False)
                + "\nOutput strict JSON only."
            }
        ]
        try:
            raw = self.llm.respond(history, self.verbose)
        except Exception as exc:
            self.success = True  # Optional review failed, original answer remains available.
            self.last_answer = original_answer
            self.last_reasoning = json.dumps({"valid": False, "changed": False, "verdict": "keep",
                "decision": "review_unavailable", "error_type": type(exc).__name__})
            return self.last_answer, self.last_reasoning
        data = self.parse_json_response(raw)
        issues = data.get("issues", [])
        revised_answer = data.get("revised_answer", "")
        verdict = str(data.get("verdict", "keep")).lower()
        confidence = data.get("confidence", 0)
        if not isinstance(issues, list):
            issues = []
        normalized_issues = []
        for issue in issues[:8]:
            if isinstance(issue, str):
                normalized_issues.append({"code": "other", "severity": "medium", "description": issue})
            elif isinstance(issue, dict):
                normalized_issues.append({
                    "code": str(issue.get("code", "other")),
                    "severity": str(issue.get("severity", "medium")),
                    "description": str(issue.get("description", "")),
                })
        if not isinstance(revised_answer, str):
            revised_answer = ""
        try:
            confidence = float(confidence)
            confidence = max(0.0, min(1.0, confidence)) if math.isfinite(confidence) else 0.0
        except (TypeError, ValueError):
            confidence = 0.0

        valid = bool(data) and "verdict" in data and "revised_answer" in data
        valid = valid and verdict in {"keep", "revise"}
        valid = valid and (verdict != "revise" or bool(revised_answer.strip()))
        selected = original_answer if verdict == "keep" else revised_answer.strip()
        if not valid or not selected.strip():
            verdict, selected, confidence = "keep", original_answer, 0.0

        decision = "model_proposal"
        if check["supported"] and not matches(selected, check["expected"]):
            verdict, selected = "keep", original_answer
            decision = "unverified_revision_rejected"
        elif verdict == "revise" and not normalized_issues:
            verdict, selected = "keep", original_answer
            decision = "revision_without_issue_rejected"

        metadata = {
            "valid": valid,
            "verdict": verdict,
            "confidence": confidence,
            "issues": normalized_issues,
            "changed": selected != original_answer,
            "verification": check,
            "decision": decision,
            "model_called": True,
        }
        self.success = bool(selected.strip())
        self.last_answer = selected
        self.last_reasoning = json.dumps(metadata, ensure_ascii=False)
        return selected, self.last_reasoning
