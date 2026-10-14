import json
import asyncio
import os
from pathlib import Path
from typing import List, Tuple, Type, Dict
from sources.utility import pretty_print, animate_thinking
from sources.agents.agent import Agent
from sources.agents.code_agent import CoderAgent
from sources.agents.file_agent import FileAgent
from sources.agents.browser_agent import BrowserAgent
from sources.agents.casual_agent import CasualAgent
from sources.agents.reflection_agent import ReflectionAgent
from sources.agents.evaluation_agent import EvaluationAgent
from sources.text_to_speech import Speech
from sources.tools.tools import Tools
from sources.logger import Logger
from sources.memory import Memory
from sources.checkpoint import CheckpointStore
from sources.recovery import (
    FailureClassifier,
    FailureKind,
    FailureRecord,
    RecoveryAction,
    RepeatedTraceDetector,
    RetryPolicy,
    repair_arguments,
)
from sources.tools.webSearch import webSearch
from sources.workflow_state_machine import (
    InvalidTransition,
    WorkflowContext,
    WorkflowEvent,
    WorkflowState,
)
from sources.skills import LoadedSkill, SkillRuntime

class PlannerAgent(Agent):
    def __init__(self, name, prompt_path, provider, verbose=False, browser=None):
        """
        The planner agent is a special agent that divides and conquers the task.
        """
        super().__init__(name, prompt_path, provider, verbose, None)
        self.tools = {
            "json": Tools()
        }
        self.tools['json'].tag = "json"
        self.browser = browser
        self.agents = {
            "coder": CoderAgent(name, "prompts/base/coder_agent.txt", provider, verbose=False),
            "file": FileAgent(name, "prompts/base/file_agent.txt", provider, verbose=False),
            "web": BrowserAgent(name, "prompts/base/browser_agent.txt", provider, verbose=False, browser=browser),
            "casual": CasualAgent(name, "prompts/base/casual_agent.txt", provider, verbose=False),
            "reflection": ReflectionAgent(name, "prompts/base/reflection_agent.txt", provider, verbose=False),
            "evaluation": EvaluationAgent(name, "prompts/base/evaluation_agent.txt", provider, verbose=False)
        }
        self.role = "planification"
        self.type = "planner_agent"
        self.memory = Memory(self.load_prompt(prompt_path),
                                recover_last_session=False, # session recovery in handled by the interaction class
                                memory_compression=False,
                                model_provider=provider.get_model_name())
        self.logger = Logger("planner_agent.log")
        self.max_plan_attempts = 3
        self.max_step_retries = 1
        self.max_replans = 2
        self.max_total_steps = 24
        self.node_retry_policy = RetryPolicy(
            max_attempts=self.max_step_retries + 1,
            base_delay_seconds=0.5,
            max_delay_seconds=4.0,
        )
        checkpoint_root = Path(os.getenv("WORK_DIR", "workspace")) / ".agenticseek_checkpoints"
        self.checkpoint_store = CheckpointStore(checkpoint_root)
        self.last_workflow_context = None
        self.active_skill = None
        self.skill_registry = None
    
    def get_task_names(self, text: str) -> List[str]:
        """
        Extracts task names from the given text.
        This method processes a multi-line string, where each line may represent a task name.
        containing '##' or starting with a digit. The valid task names are collected and returned.
        Args:
            text (str): A string containing potential task titles (eg: Task 1: I will...).
        Returns:
            List[str]: A list of extracted task names that meet the specified criteria.
        """
        tasks_names = []
        lines = text.strip().split('\n')
        for line in lines:
            if line is None:
                continue
            line = line.strip()
            if len(line) == 0:
                continue
            if '##' in line or line[0].isdigit():
                tasks_names.append(line)
                continue
        self.logger.info(f"Found {len(tasks_names)} tasks names.")
        return tasks_names

    def parse_agent_tasks(self, text: str) -> List[Tuple[str, str]]:
        """
        Parses agent tasks from the given LLM text.
        This method extracts task information from a JSON. It identifies task names and their details.
        Args:
            text (str): The input text containing task information in a JSON-like format.
        Returns:
            List[Tuple[str, str]]: A list of tuples containing task names and their details.
        """
        tasks = []
        tasks_names = self.get_task_names(text)

        blocks, _ = self.tools["json"].load_exec_block(text)
        if blocks == None:
            return []
        for block in blocks:
            try:
                line_json = json.loads(block)
            except json.JSONDecodeError as e:
                self.logger.warning(f"Failed to parse JSON block: {e}")
                pretty_print(f"JSON parsing error: {e}", color="warning")
                return []
            if 'plan' in line_json:
                for task in line_json['plan']:
                    try:
                        agent_name = str(task['agent']).lower()
                        agent = {
                            'agent': agent_name,
                            'id': str(task['id']),
                            'task': str(task['task']),
                            'need': [str(item) for item in (task.get('need') or [])]
                        }
                    except (KeyError, TypeError, ValueError):
                        self.logger.warning("Missing field in json plan.")
                        return []
                    if agent_name not in [ag_name.lower() for ag_name in self.agents.keys()]:
                        self.logger.warning(f"Agent {agent_name} does not exist.")
                        pretty_print(f"Agent {agent_name} does not exist.", color="warning")
                        return []
                    self.logger.info(f"Created agent {agent_name} with task: {agent['task']}")
                    self.logger.info(f"Agent {agent_name} was given info:\n {agent['need']}")
                    tasks.append(agent)
        if len(tasks_names) != len(tasks):
            names = [task['task'] for task in tasks]
            return list(map(list, zip(names, tasks)))
        return list(map(list, zip(tasks_names, tasks)))
    
    def make_prompt(self, task: str, agent_infos_dict: dict) -> str:
        """
        Generates a prompt for the agent based on the task and previous agents work information.
        Args:
            task (str): The task to be performed.
            agent_infos_dict (dict): A dictionary containing information from other agents.
        Returns:
            str: The formatted prompt for the agent.
        """
        infos = ""
        if agent_infos_dict is None or len(agent_infos_dict) == 0:
            infos = "No needed informations."
        else:
            for agent_id, info in agent_infos_dict.items():
                infos += f"\t- According to agent {agent_id}:\n{info}\n\n"
        prompt = f"""
        You are given informations from your AI friends work:
        {infos}
        Your task is:
        {task}
        """
        self.logger.info(f"Prompt for agent:\n{prompt}")
        return prompt
    
    def show_plan(self, agents_tasks: List[dict], answer: str) -> None:
        """
        Displays the plan made by the agent.
        Args:
            agents_tasks (dict): The tasks assigned to each agent.
            answer (str): The answer from the LLM.
        """
        if agents_tasks == []:
            pretty_print(answer, color="warning")
            pretty_print("Failed to make a plan. This can happen with (too) small LLM. Clarify your request and insist on it making a plan within ```json.", color="failure")
            return
        pretty_print("\n▂▘ P L A N ▝▂", color="status")
        for task_name, task in agents_tasks:
            pretty_print(f"{task['agent']} -> {task['task']}", color="info")
        pretty_print("▔▗ E N D ▖▔", color="status")

    async def make_plan(self, prompt: str, max_attempts: int | None = None) -> List[list]:
        """
        Asks the LLM to make a plan.
        Args:
            prompt (str): The prompt to be sent to the LLM.
        Returns:
            str: The plan made by the LLM.
        """
        attempt_limit = max_attempts or self.max_plan_attempts
        self.last_plan_attempts = 0
        retry_prompt = prompt
        for attempt in range(1, attempt_limit + 1):
            self.last_plan_attempts = attempt
            animate_thinking("Thinking...", color="status")
            self.memory.push('user', retry_prompt)
            answer, reasoning = await self.llm_request()
            if "NO_UPDATE" in answer:
                return []
            agents_tasks = self.parse_agent_tasks(answer)
            active_skill = getattr(self, "active_skill", None)
            if agents_tasks and active_skill is not None:
                validation = SkillRuntime.validate_plan(active_skill, agents_tasks)
                if not validation.passed:
                    agents_tasks = []
                    retry_prompt = (
                        "The plan violates the selected skill: "
                        + "; ".join(validation.errors)
                        + ". Return a corrected complete JSON plan."
                    )
                    pretty_print(
                        f"Skill rejected plan (attempt {attempt}/{attempt_limit}).",
                        color="warning",
                    )
                    continue
            if agents_tasks:
                self.show_plan(agents_tasks, answer)
                self.logger.info(f"Plan made in {attempt} attempt(s):\n{answer}")
                return agents_tasks
            self.show_plan(agents_tasks, answer)
            retry_prompt = (
                "The previous response was not a valid plan. Return a JSON plan inside "
                "a ```json block. Every step must contain agent, id, task, and need."
            )
            pretty_print(
                f"Failed to make plan (attempt {attempt}/{attempt_limit}).",
                color="warning"
            )
        self.logger.error(f"Plan creation exhausted {attempt_limit} attempts.")
        return []
    
    async def update_plan(self, goal: str, agents_tasks: List[dict], agents_work_result: dict, id: str, success: bool) -> dict:
        """
        Updates the plan with the results of the agents work.
        Args:
            goal (str): The goal to be achieved.
            agents_tasks (list): The tasks assigned to each agent.
            agents_work_result (dict): The results of the agents work.
        Returns:
            dict: The updated plan.
        """
        self.status_message = "Updating plan..."
        last_agent_work = agents_work_result[id]
        tool_success_str = "success" if success else "failure"
        pretty_print(f"Agent {id} work {tool_success_str}.", color="success" if success else "failure")
        task_index = next(
            (
                index for index, item in enumerate(agents_tasks)
                if str(item[1].get('id')) == str(id)
            ),
            None,
        )
        if task_index is None:
            self.logger.warning(f"Cannot replan: task id {id} is not in the current plan.")
            return agents_tasks
        if task_index == len(agents_tasks) - 1:
            next_task = "No task follow, this was the last step. If it failed add a task to recover."
        else:
            next_task = f"Next task is: {agents_tasks[task_index + 1][0]}."
        #if success:
        #    return agents_tasks # we only update the plan if last task failed, for now
        update_prompt = f"""
        Your goal is : {goal}
        You previously made a plan, agents are currently working on it.
        The last agent working on task: {id}, did the following work:
        {last_agent_work}
        Agent {id} work was a {tool_success_str} according to system interpreter.
        {next_task}
        Is the work done for task {id} leading to success or failure ? Did an agent fail with a task?
        If agent work was good: answer "NO_UPDATE"
        If agent work is leading to failure: update the plan.
        If a task failed add a task to try again or recover from failure. You might have near identical task twice.
        plan should be within ```json like before.
        You need to rewrite the whole plan, but only change the tasks after task {id}.
        Make the plan the same length as the original one or with only one additional step.
        Do not change past tasks. Change next tasks.
        """
        pretty_print("Updating plan...", color="status")
        plan = await self.make_plan(update_prompt, max_attempts=self.max_plan_attempts)
        if plan == []:
            pretty_print("No plan update required.", color="info")
            return agents_tasks
        self.logger.info(f"Plan updated:\n{plan}")
        return plan
    
    def _review_payload(self, task, required_infos):
        context = self.last_workflow_context
        needs = [str(value) for value in task.get("need", [])]
        infos = {str(k): v for k, v in (required_infos or {}).items()}
        def clean(text):
            for suffix in ("\nAgent succeeded with task.", "\nAgent failed with task (Error detected)."):
                if text.endswith(suffix):
                    return text[:-len(suffix)]
            return text
        if task["agent"].lower() == "evaluation":
            candidates = [key for key in needs if key in context.review_contexts and key in infos]
            if len(candidates) != 1:
                raise ValueError("Evaluation requires exactly one completed reflection dependency")
            key = candidates[0]
            return {**context.review_contexts[key], "after": clean(infos[key])}
        plan = {str(item[1]["id"]): item[1] for item in context.plan}
        drafts = [key for key in needs if key in infos and plan.get(key, {}).get("agent", "").lower() not in ("file", "web", "reflection", "evaluation")]
        if len(drafts) != 1:
            raise ValueError("Reflection requires exactly one draft dependency, plus optional evidence nodes")
        draft_id = drafts[0]
        evidence_ids = list(dict.fromkeys(needs + [str(k) for k in plan[draft_id].get("need", [])]))
        evidence = [clean(context.results[key]) for key in evidence_ids if key in context.results
                    and key not in context.failures and plan.get(key, {}).get("agent", "").lower() in ("file", "web")]
        payload = {"query": context.goal, "before": clean(infos[draft_id]), "evidence": evidence,
                   "requirements": [], "tool_status": [f"node {key}: failed" for key in evidence_ids if key in context.failures]}
        context.review_contexts[str(task["id"])] = payload
        return {**payload, "answer": payload["before"]}

    async def start_agent_process(self, task: dict, required_infos: dict | None) -> str:
        """
        Starts the agent process for a given task.
        Args:
            task (dict): The task to be performed.
            required_infos (dict | None): The required information for the task.
        Returns:
            str: The result of the agent process.
        """
        self.status_message = f"Starting task {task['task']}..."
        agent_prompt = self.make_prompt(task['task'], required_infos)
        is_review = task["agent"].lower() in ("reflection", "evaluation")
        if is_review:
            agent_prompt = self._review_payload(task, required_infos)
        pretty_print(f"Agent {task['agent']} started working...", color="status")
        self.logger.info(f"Agent {task['agent']} started working on {task['task']}.")
        answer, reasoning = await self.agents[task['agent'].lower()].process(agent_prompt, None)
        self.last_answer = answer
        self.last_reasoning = reasoning
        self.blocks_result = self.agents[task['agent'].lower()].blocks_result
        agent_answer = answer if is_review else self.agents[task['agent'].lower()].raw_answer_blocks(answer)
        if agent_answer is None:
            agent_answer = answer or ""
        success = self.agents[task['agent'].lower()].get_success
        self.agents[task['agent'].lower()].show_answer()
        pretty_print(f"Agent {task['agent']} completed task.", color="status")
        self.logger.info(f"Agent {task['agent']} finished working on {task['task']}. Success: {success}")
        if not is_review:
            agent_answer += "\nAgent succeeded with task." if success else "\nAgent failed with task (Error detected)."
        return agent_answer, success
    
    def get_work_result_agent(self, task_needs, agents_work_result):
        task_needs = task_needs or []
        res = {k: agents_work_result[k] for k in task_needs if k in agents_work_result}
        self.logger.info(f"Next agent needs: {task_needs}.\n Match previous agent result: {res}")
        return res

    async def process(
        self,
        goal: str,
        speech_module: Speech,
        selected_skill: LoadedSkill | None = None,
    ) -> Tuple[str, str]:
        """
        Process the goal by dividing it into tasks and assigning them to agents.
        Args:
            goal (str): The goal to be achieved (user prompt).
            speech_module (Speech): The speech module for text-to-speech.
        Returns:
            Tuple[str, str]: The result of the agent process and empty reasoning string.
        """
        self.stop = False
        self.active_skill = selected_skill
        context = WorkflowContext(
            goal=goal,
            max_plan_attempts=self.max_plan_attempts,
            max_step_retries=self.max_step_retries,
            max_replans=self.max_replans,
            max_total_steps=getattr(self, "max_total_steps", 24),
        )
        self.last_workflow_context = context
        try:
            planning_goal = goal
            if selected_skill is not None:
                dependency_check = SkillRuntime.validate_dependencies(selected_skill, self.agents)
                if not dependency_check.passed:
                    raise ValueError("; ".join(dependency_check.errors))
                context.selected_skill = selected_skill.checkpoint_metadata()
                planning_goal = SkillRuntime.render_for_planner(selected_skill, goal)
            self.status_message = "Planning"
            context.transition(WorkflowEvent.START_PLANNING)
            self._save_checkpoint(context)
            plan = await self.make_plan(planning_goal, max_attempts=context.max_plan_attempts)
            context.plan_attempts = getattr(self, "last_plan_attempts", 0)
            if not plan:
                context.transition(WorkflowEvent.PLAN_FAILED, "Could not create a valid plan.")
                self._save_checkpoint(context)
                return "Failed to create a valid plan after bounded retries.", self._workflow_reasoning(context)
            context.install_plan(plan)
            context.transition(WorkflowEvent.PLAN_CREATED, f"steps={len(plan)}")
            self._save_checkpoint(context)
            pretty_print(
                f"Workflow checkpoint id: {context.workflow_id} "
                f"(resume with /resume {context.workflow_id})",
                color="status",
            )
            return await self._execute_workflow(context, speech_module)
        except Exception as exc:
            self.logger.error(f"Workflow initialization failed: {exc}")
            if not context.is_terminal:
                context.transition(WorkflowEvent.FATAL_ERROR, f"{type(exc).__name__}: {exc}")
            self._save_checkpoint(context)
            return self._terminal_response(context, "")

    async def resume_from_checkpoint(self, task_id: str, speech_module: Speech = None,
                                     resume_stopped: bool = False) -> Tuple[str, str]:
        """Resume at the first incomplete node without replaying successful steps."""
        payload = self.checkpoint_store.load(task_id)
        context = WorkflowContext.from_snapshot(payload)
        self.last_workflow_context = context
        self.stop = False
        self.active_skill = None
        if resume_stopped and context.state == WorkflowState.STOPPED:
            context.transition(WorkflowEvent.RESUME_REQUESTED, "User explicitly resumed a stopped workflow.")
            for agent in self.agents.values():
                agent.stop = False
        if context.selected_skill:
            registry = getattr(self, "skill_registry", None)
            if registry is None:
                context.transition(WorkflowEvent.FATAL_ERROR, "Skill registry is unavailable during resume.")
                return self._terminal_response(context, "")
            skill = registry.load(context.selected_skill["name"])
            if skill.content_hash != context.selected_skill.get("content_hash"):
                context.transition(WorkflowEvent.FATAL_ERROR, "Selected skill changed since checkpoint creation.")
                return self._terminal_response(context, "")
            self.active_skill = skill
        if context.is_terminal:
            last_key = next(reversed(context.results), None) if context.results else None
            last_answer = context.results.get(last_key, "") if last_key else ""
            return self._terminal_response(context, last_answer)
        self._restore_model_messages(context)
        if not context.plan:
            context.state = WorkflowState.PLANNING
            plan = await self.make_plan(context.goal, max_attempts=context.max_plan_attempts)
            context.plan_attempts += getattr(self, "last_plan_attempts", 0)
            if not plan:
                context.transition(WorkflowEvent.PLAN_FAILED, "Could not recreate interrupted plan.")
                self._save_checkpoint(context)
                return self._terminal_response(context, "")
            context.install_plan(plan)
            context.transition(WorkflowEvent.PLAN_CREATED, f"steps={len(plan)} resumed=true")
        self.logger.info(
            f"Resuming workflow {task_id} at cursor={context.cursor} state={context.state.value}."
        )
        self._save_checkpoint(context)
        return await self._execute_workflow(context, speech_module)

    async def _execute_workflow(
        self,
        context: WorkflowContext,
        speech_module: Speech,
    ) -> Tuple[str, str]:
        detector = RepeatedTraceDetector(max_repetitions=2)
        detector.restore(context.trace_counts)
        last_key = next(reversed(context.results), None) if context.results else None
        last_answer = context.results.get(last_key, "") if last_key else ""
        try:
            while not context.is_terminal:
                if self.stop:
                    context.transition(WorkflowEvent.STOP_REQUESTED, "User requested stop.")
                    self._save_checkpoint(context)
                    break
                if context.step_budget_exhausted():
                    context.transition(WorkflowEvent.FATAL_ERROR, "Maximum workflow step budget exhausted.")
                    self._save_checkpoint(context)
                    break
                if context.current_task is None:
                    if self.active_skill is not None:
                        work_dir = os.getenv("WORK_DIR", "workspace")
                        validation = SkillRuntime.validate_outcome(self.active_skill, work_dir)
                        context.skill_validation = validation.to_dict()
                        if not validation.passed:
                            context.transition(
                                WorkflowEvent.FATAL_ERROR,
                                "Skill validation failed: " + "; ".join(validation.errors),
                            )
                            self._save_checkpoint(context)
                            break
                    context.transition(WorkflowEvent.ALL_COMPLETED)
                    self._save_checkpoint(context)
                    break

                task = context.current_task
                task_name = context.current_task_name or task["task"]
                signature = detector.signature(task["agent"], task["task"])
                repetitions = detector.record(signature)
                context.trace_counts = detector.snapshot()
                if repetitions > detector.max_repetitions:
                    failure = FailureRecord(
                        FailureKind.REPEATED_CALL,
                        f"Repeated identical node {repetitions} times: {task['id']}",
                        False,
                        RecoveryAction.REPLAN,
                        source="trajectory",
                        attempt=repetitions,
                    )
                    context.transition(
                        WorkflowEvent.STEP_STARTED,
                        f"task={task['id']} repeated_signature=true",
                    )
                    context.record_failure(failure.message)
                    context.transition(WorkflowEvent.STEP_FAILED, failure.kind.value)
                    context.append_error(failure.to_dict())
                    await self._replan_or_fail(context, failure)
                    self._save_checkpoint(context)
                    continue

                context.transition(WorkflowEvent.STEP_STARTED, f"task={task['id']} agent={task['agent']}")
                self.status_message = f"Running task {task['id']}"
                self._save_checkpoint(context)  # crash-safe: cursor still points to this node
                pretty_print(f"I will {task_name}.", color="info")
                pretty_print(f"Assigned agent {task['agent']} to {task_name}", color="info")
                if speech_module:
                    speech_module.speak(
                        f"I will {task_name}. I assigned the {task['agent']} agent to the task."
                    )

                try:
                    answer, success = await self.start_agent_process(task, context.dependencies())
                    last_answer = answer or ""
                    failure = self._verify_step(task, last_answer, success)
                except Exception as exc:
                    last_answer = f"{type(exc).__name__}: {exc}"
                    failure = FailureClassifier.classify(
                        exc,
                        source=f"agent:{task['agent']}",
                        attempt=context.step_retries.get(context.task_id(), 0) + 1,
                    )
                    self.logger.error(f"Task {task['id']} raised an exception: {exc}")

                if failure is None:
                    context.record_success(last_answer)
                    context.transition(WorkflowEvent.STEP_SUCCEEDED, f"task={task['id']}")
                    self._save_checkpoint(context)
                    continue

                context.record_failure(failure.message)
                context.append_error(failure.to_dict())
                context.transition(
                    WorkflowEvent.STEP_FAILED,
                    f"task={task['id']} kind={failure.kind.value}",
                )
                self._save_checkpoint(context)

                recovered = False
                if failure.action == RecoveryAction.FALLBACK:
                    recovered = self._activate_fallback(task, failure)
                if failure.action == RecoveryAction.REPAIR_ARGUMENTS:
                    context.plan[context.cursor][1] = repair_arguments(task, failure)
                    recovered = True

                if (failure.retryable or recovered) and context.can_retry_current_step():
                    retry_number = context.consume_retry()
                    delay = getattr(self, "node_retry_policy", RetryPolicy()).delay(retry_number)
                    pretty_print(
                        f"Retrying task {task['id']} in {delay:.2f}s "
                        f"({retry_number}/{context.max_step_retries}, {failure.kind.value}).",
                        color="warning",
                    )
                    self._save_checkpoint(context)
                    await asyncio.sleep(delay)
                    continue

                context.transition(WorkflowEvent.RETRY_EXHAUSTED, f"task={task['id']}")
                await self._replan_or_fail(context, failure)
                self._save_checkpoint(context)

        except InvalidTransition as exc:
            self.logger.error(f"Workflow transition error: {exc}")
            if not context.is_terminal:
                context.transition(WorkflowEvent.FATAL_ERROR, str(exc))
        except Exception as exc:
            self.logger.error(f"Workflow failed: {exc}")
            if not context.is_terminal:
                context.transition(WorkflowEvent.FATAL_ERROR, f"{type(exc).__name__}: {exc}")
        self._save_checkpoint(context)
        return self._terminal_response(context, last_answer)

    def _verify_step(
        self,
        task: dict,
        answer: str,
        agent_success: bool,
    ) -> FailureRecord | None:
        """Deterministic verifier; objective tool status wins over fluent prose."""
        attempt = self.last_workflow_context.step_retries.get(str(task["id"]), 0) + 1
        if not agent_success:
            failure = FailureClassifier.classify(
                answer,
                source=f"agent:{task['agent']}",
                attempt=attempt,
            )
            if failure.kind == FailureKind.UNKNOWN:
                failure.retryable = True
                failure.action = RecoveryAction.RETRY
            return failure
        if not answer or not answer.strip():
            return FailureClassifier.classify("Empty result", source=f"agent:{task['agent']}", attempt=attempt)
        classified = FailureClassifier.classify(answer, source=f"agent:{task['agent']}", attempt=attempt)
        if classified.kind in {
            FailureKind.EMPTY_RESULT,
            FailureKind.EARLY_EXIT,
            FailureKind.INTERRUPTED,
            FailureKind.TOOL_HALLUCINATION,
            FailureKind.CONFLICTING_RESULTS,
        }:
            return classified
        return None

    async def _replan_or_fail(self, context: WorkflowContext, failure: FailureRecord) -> None:
        if context.state == WorkflowState.RUNNING:
            context.transition(WorkflowEvent.STEP_FAILED, failure.kind.value)
        if context.state == WorkflowState.RETRYING:
            context.transition(WorkflowEvent.RETRY_EXHAUSTED, failure.kind.value)
        if context.state != WorkflowState.REPLANNING:
            raise InvalidTransition(
                f"recovery expected replanning state, got {context.state.value}"
            )
        if not context.can_replan():
            context.transition(WorkflowEvent.REPLAN_FAILED, "Replan budget exhausted.")
            return
        previous_plan = context.plan
        self.status_message = "Replanning"
        try:
            revised_plan = await self.update_plan(
                context.goal,
                previous_plan,
                context.results,
                context.task_id(),
                False,
            )
            if not revised_plan or revised_plan == previous_plan:
                raise ValueError("Planner did not provide a recovery step.")
            context.install_replanned_tail(revised_plan)
            context.transition(
                WorkflowEvent.REPLAN_SUCCEEDED,
                f"replans={context.replan_count} steps={len(revised_plan)}",
            )
        except Exception as exc:
            self.logger.error(f"Replanning failed: {exc}")
            context.transition(WorkflowEvent.REPLAN_FAILED, f"{type(exc).__name__}: {exc}")

    def _activate_fallback(self, task: dict, failure: FailureRecord) -> bool:
        """Switch the failed node to an available alternate tool, without replaying prior nodes."""
        if str(task.get("agent", "")).lower() != "web":
            return False
        api_key = os.getenv("SERPAPI_KEY")
        if not api_key:
            self.logger.warning("Web fallback unavailable: SERPAPI_KEY is not configured.")
            return False
        browser_agent = self.agents.get("web")
        if browser_agent is None:
            return False
        browser_agent.tools["web_search"] = webSearch(api_key=api_key)
        self.logger.warning(
            f"Activated SerpAPI fallback for web node after {failure.kind.value}."
        )
        return True

    def _collect_model_messages(self) -> dict:
        messages = {}
        for name, agent in getattr(self, "agents", {}).items():
            memory = getattr(agent, "memory", None)
            if memory is not None:
                messages[name] = memory.get()[-20:]
        return messages

    def _restore_model_messages(self, context: WorkflowContext) -> None:
        for name, messages in context.model_messages.items():
            agent = getattr(self, "agents", {}).get(name)
            memory = getattr(agent, "memory", None) if agent else None
            if memory is not None and messages:
                memory.memory = list(messages)

    def _save_checkpoint(self, context: WorkflowContext) -> None:
        store = getattr(self, "checkpoint_store", None)
        if store is None:
            return
        context.model_messages = self._collect_model_messages()
        provider = getattr(self, "llm", None)
        if provider is not None and hasattr(provider, "get_usage"):
            context.token_usage = provider.get_usage()
        store.save(context.workflow_id, context.snapshot())

    def _terminal_response(self, context: WorkflowContext, last_answer: str) -> Tuple[str, str]:
        if context.state == WorkflowState.SUCCEEDED:
            self.status_message = "Succeeded"
            return last_answer, self._workflow_reasoning(context)
        if context.state == WorkflowState.STOPPED:
            self.status_message = "Stopped"
            return "Workflow stopped by user.", self._workflow_reasoning(context)
        self.status_message = "Failed"
        message = context.last_error or "Workflow failed before completing all tasks."
        return f"Workflow failed: {message}", self._workflow_reasoning(context)

    def _workflow_reasoning(self, context: WorkflowContext) -> str:
        """Return machine-readable runtime metadata without exposing hidden LLM thoughts."""
        checkpoint_store = getattr(self, "checkpoint_store", None)
        checkpoint_path = (
            str(checkpoint_store.path_for(context.workflow_id))
            if checkpoint_store is not None
            else None
        )
        return json.dumps(
            {
                "task_id": context.workflow_id,
                "state": context.state.value,
                "checkpoint": checkpoint_path,
                "plan_attempts": context.plan_attempts,
                "replan_count": context.replan_count,
                "executed_steps": context.executed_steps,
                "completed_task_ids": [
                    task_id for task_id in context.results if task_id not in context.failures
                ],
                "failed_task_ids": list(context.failures.keys()),
                "last_error": context.last_error,
                "error_history": context.error_history,
                "token_usage": context.token_usage,
                "selected_skill": context.selected_skill,
                "skill_validation": context.skill_validation,
                "transitions": context.trace(),
            },
            ensure_ascii=False,
        )
