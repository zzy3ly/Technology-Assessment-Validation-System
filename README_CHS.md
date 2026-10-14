# Technology Assessment & Validation System（TAVS）

**Technology Assessment & Validation System（TAVS）** 是一个面向研发团队的多智能体技术调研与方案验证助手。它将自然语言问题转化为可追踪的工作流，帮助用户完成技术调研、方案对比、原型执行和证据化验证。

![TAVS 工作台](./docs/screenshots/tavs-workspace.png)

## 系统能力

- 通过 SearXNG 和浏览器 Agent 进行联网调研；
- 任务路由、规划和专业子 Agent 协同；
- 文件分析以及 Python/PowerShell 执行；
- 对中间结果和最终答案进行反思与评估；
- 按需加载带约束的 Skill；
- React Web 控制台，展示任务、执行轨迹、工具证据和恢复入口。

原始 AgenticSeek 是一个通用本地智能助手，支持网页浏览、代码、文件处理、语音和多智能体任务执行。TAVS 保留了其中可复用的 Agent 与工具基础，并将系统重点聚焦到技术评估和方案验证。

## 主要优化

本项目的核心工作是将依赖 Prompt 的工作流改造成可恢复的执行系统：

1. **显式状态机编排**：在代码中管理任务依赖、状态转换、重试次数、重新规划和终止条件。
2. **统一故障恢复**：分类处理超时、限流、空结果、参数错误、文件不存在、代码失败和模型输出异常。
3. **断点持久化**：保存已完成节点、待执行节点、工具输出、错误历史和 token 使用量，中断后只继续未完成部分。
4. **反思与评估闭环**：根据结构化标准检查初始答案，必要时修订并验证，而不是只相信模型分数。
5. **Skill 机制**：Skill 支持注册、按需加载、规划约束、运行校验和 checkpoint 元数据记录。
6. **证据优先的 Web 界面**：用户可以查看执行轨迹、工具输出、校验记录和恢复状态。

![TAVS 执行轨迹](./docs/screenshots/tavs-execution-trace.png)

## 目录结构

```text
sources/       Agent、状态机、故障恢复、工具和 Web 运行时
prompts/       Planner 与各专业 Agent 的提示词
skills/        示例 Skill 及其策略资源
frontend/      React Web 控制台
tests/         单元、工作流、恢复、Web 和 Skill 测试
benchmarks/    测试集、实验脚本和评测报告
searxng/       搜索服务配置
scripts/       安装和联调脚本
docs/          架构、贡献、安全说明和截图
```

## 快速启动

```powershell
Copy-Item .env.example .env
Copy-Item config.example.ini config.ini
# 在 .env 中设置 WORK_DIR 和所使用模型的 API Key
docker compose --profile core up -d redis searxng
.\start_web.ps1 -SkipBuild -Background
```

打开 `http://127.0.0.1:7777`。服务默认只监听本机地址。更多 API 和故障排查说明见 [WEB_GUIDE.md](./WEB_GUIDE.md)。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_web*.py" -q
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*recovery*.py" -q
```

真实模型测试可能消耗 API 额度。模型密钥只能放在本地 `.env` 中，禁止提交到 Git。

## 二次开发与许可证

本项目基于 [AgenticSeek](https://github.com/Fosowl/agenticSeek) 二次开发，遵循 GNU General Public License v3.0。本版本新增可靠工作流、故障恢复、反思评估、Skill 和 Web 执行界面。详见 [LICENSE](./LICENSE)、[开源仓库说明](./docs/OPEN_SOURCE_GUIDE.md) 和 [SECURITY.md](./SECURITY.md)。
