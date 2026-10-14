# Web 工作台联调记录

日期：2026-10-14。环境：Windows、项目 `.venv`、React 19 / react-scripts 5、DeepSeek `deepseek-chat`。

这份记录证明本次 Web 接入的链路和修复有效，不代表对任意用户任务的成功率评测。

## 自动化检查

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| Web API、Python 子进程、shell 退出码、工具证据映射 | 22 / 22 通过 | `.web-runtime/web-tests.log`，`tests/test_web*.py` |
| 页面行为 | 6 / 6 通过 | `frontend/agentic-seek-front/src/App.test.js` |
| 项目 Python 全量回归 | 174 项中 170 通过、4 项报错 | `.web-runtime/regression-tests.log` |
| 真实浏览器端到端 | 桌面加载、Skill 选择、断点入口、真实 DeepSeek 提交、刷新保持、390px 无横向溢出；无 JS 错误 | `.web-runtime/ui-smoke-live.json`、`.web-runtime/ui-smoke.json`、`.web-runtime/screenshots` |

全量回归的四项已存在问题：`test_memory.test_save_and_load_memory` 在恢复时访问尚未初始化的 tokenizer；`test_searx_search` 的 valid_query / no_results / request_exception 三项受到未运行的 SearxNG 及旧异常断言的影响。本次没有把它们改成跳过或伪造通过。

停止后的续跑另有 `test_explicit_web_resume_of_stopped_workflow_reuses_completed_node`：保存节点 1 的成功结果，从停止状态恢复，只执行节点 2，并记录 resume_requested 事件。该项包含在全量回归中。

## 真实模型联调（修复后的最终运行）

| 用例 | 实际结果 |
| --- | --- |
| 浏览器提交简单问答 | DeepSeek 返回“网页提交与结果展示正常”；网页显示完成；刷新后结果仍在 |
| 两步 Planner 工作流 | coder 实际执行平方和计算，再由 casual 汇总；2 / 2 节点完成；结果 55；无节点重试或重规划；记录 2,736 tokens、42.26 秒 |
| 测试驱动代码修复 Skill | 仅在隔离的 `web-skill-smoke` 样例运行；6 / 6 节点完成；0 条工作流错误；修复 add 的减法错误；现有 3 项 unittest 全部通过；记录 36,144 tokens、86.48 秒 |

最终报告：`.web-runtime/live-workflow.json`、`.web-runtime/live-skill.json`。它们包含任务 ID、完整计划、状态转换、工具返回、实际 token 用量、最终答案和 Skill validator 的命令/退出码/输出。

Skill 样例修复前：2 项失败、1 项通过；修复后：3 项通过。生产代码从 `return a - b` 改为 `return a + b`；测试文件保持原样。独立 Skill 校验器再次执行 `python -m unittest discover -v`，退出码为 0，报告包含 `Ran 3 tests ... OK`。这是小样例的功能联调，不用于简历中的泛化成功率。

token 数来自 Provider 的运行记录；未核算货币费用。报告中的 cost=0 是现有未计价字段，不能理解为 API 免费。

## 联调发现并修复的问题

1. **原工具证据串线**：remove_blocks 每轮从 block:0 开始，但工具结果是累积列表；raw_answer_blocks 又读取 last_answer 而不是参数。第二轮回答可能引用第一轮失败输出，导致实际修好了，评审却认为没执行。改为当前执行批次的真实全局索引，并按工具类型及内容匹配。
2. **Windows 命令不一致**：原 bash 工具实际经系统 shell 运行，模型却发 Linux/PowerShell 混用命令，部分 Python 命令被静默跳过。Web 侧明确使用 PowerShell、项目虚拟环境、真实退出码和超时，并告诉 Agent 正确语法。
3. **Python 进程全局状态**：原 Python exec 会重定向整个进程的 stdout，也可能改变工作目录、干扰异步事件循环。Web 侧移到独立子进程；保留 CLI 原解释器。
4. **旧接口假成功**：不再以 think() 返回 True 判断任务成功，而使用工作流真实终态；失败、停止和中断不能显示为成功。轮询也不再清空答案。

第一轮 Skill 联调曾出现“独立测试通过但文字报告说没执行”的矛盾；未将该轮当作完整验收通过。上述证据映射修复后，在重置的隔离样例中重跑，最终报告与真实测试结果一致。

## 尚未通过的外部依赖

Docker Desktop 本机启动报错，SearxNG 不可用。没有声称联网搜索链路已通过，也没有重置 Docker。Web 页面展示真实依赖警告；其余离线工具任务与云模型问答可运行。

原自动分类路由模型和 C/Java/Go 编译环境未逐项进行真实联调。默认 Web 模式是现有 Planner，并非调用原分类器。
