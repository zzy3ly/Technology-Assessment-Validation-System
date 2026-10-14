# AgenticSeek Web 工作台

面向研发团队的技术调研与方案验证助手。本机单用户版本；沿用已有 Planner、File、Coder、Web、Casual、Reflection、Evaluation 和 Skill，不是另造一套演示后端。

## 启动

在项目根目录的 PowerShell 中运行：

```powershell
.\start_web.ps1
```

访问 http://127.0.0.1:7777 。脚本构建 React，然后由 FastAPI 同源提供页面和 API；不必同时维护两个端口。

```powershell
# 已构建前端时快速启动
.\start_web.ps1 -SkipBuild

# 后台运行，日志保存在 .web-runtime/api.*.log
.\start_web.ps1 -SkipBuild -Background

# Docker Desktop 正常运行时，额外启动搜索依赖
.\start_web.ps1 -StartSearch

# 修改端口
.\start_web.ps1 -Port 7780
```

如 PowerShell 阻止执行本地脚本，可对这一次启动使用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\start_web.ps1`；不需要修改机器级执行策略。

依赖：现有 `.venv` 和 Python 依赖、Node/npm（构建时）、`.env` 中的模型 Key 与 `WORK_DIR`、`config.ini` 中的模型配置。密钥只留在后端，页面不提供修改密钥入口。

前端开发：后端启动后，在 `frontend/agentic-seek-front` 执行 `npm start`，开发代理默认指向 7777；其他端口可用 `REACT_APP_BACKEND_URL`。生产构建应使用默认同源地址。

## 使用

- **多 Agent 编排（默认）**：复用状态机，自动规划与分派；在右侧看节点、依赖、重试、工具输出、tokens。
- **直接问答 / 联网调研 / 文件处理 / 代码验证**：显式选择原有 Agent。单 Agent 模式不显示多节点工作流，也不产生 Planner checkpoint。
- **原自动路由**：保留原分类模型路由，首次使用可能加载/下载模型；这不是默认模式。Web 默认 Planner 的节点分派仍由原系统规划完成。
- **Skill**：选择“测试驱动代码修复”，或输入 `$test-driven-code-repair 任务内容`；强制走 Planner，实际加载 Skill、检查规划、调用测试校验器。
- **断点恢复**：中断及手动停止的工作流可继续，复用成功节点。最终失败或完成的任务不自动重放；先排查或调整任务再提交。
- **停止**：协作式停止，不是假装立刻取消；当前模型/工具调用结束后退出。已经产生的文件修改不会撤销。
- **任务记录**：关闭网页不影响后台执行；刷新后恢复选中的任务。记录保存在 `WORK_DIR/.agenticseek_web`，checkpoint 保存在 `WORK_DIR/.agenticseek_checkpoints`。页面列出最近 100 项。

每次提交是独立任务，不隐式继承上一次任务的对话。文件请填写真实本地路径；当前没有附件上传、登录或多人隔离功能。

代码修复 Skill 的独立校验器测试的是后端 `WORK_DIR`，不是从用户文字中猜出的项目目录。使用前请将 `.env` 的 `WORK_DIR` 设置为目标项目（包含测试文件），重启后端；网页输入框下会显示实际测试目录。普通问答和临时方案验证不需要修改它。

## 本次改动

1. React 工作台：任务历史、模式/Skill 选择、执行节点、工具证据、反思评估记录、恢复入口、浅深色主题和移动布局。
2. 新任务 API：立即返回 202 和稳定任务 ID；后台单任务执行，轮询不再清空答案，异常不再退出整个 API。
3. 真实状态：只有终态 succeeded 才标记成功；排队、执行、停止中、失败、服务重启中断分开记录。
4. 延迟初始化：打开网页无需先启动 Chrome 或加载路由模型；只有实际任务才加载 Agent，需要浏览器时才启动 Chrome。
5. Web Python 子进程执行，避免生成代码改变 HTTP 服务的 cwd/stdout/事件循环；Windows shell 明确使用 PowerShell、当前虚拟环境和真实退出码。不是安全沙箱。
6. 修复原 Agent 的工具证据索引：同一 Agent 多轮执行后，不再将当前答案的 block:0 错配到最早的工具输出；也不按错误的注册顺序匹配执行证据。
7. 手动停止的 checkpoint 经明确恢复事件回到 READY，不重跑已成功节点；CLI 默认终态语义保持不变。

## API

接口文档：http://127.0.0.1:7777/docs

| 接口 | 用途 |
| --- | --- |
| GET /api/health、/api/capabilities | 服务/模型配置与搜索可达性 |
| GET /api/skills | 真实注册的 Skill 元数据 |
| POST /api/tasks | 提交 query、mode、skill，立即返回任务 ID |
| GET /api/tasks、/api/tasks/{id} | 历史与执行快照 |
| POST /api/tasks/{id}/stop | 请求安全停止 |
| GET /api/checkpoints、/api/checkpoints/{id} | 断点及执行证据，不返回 model_messages |
| POST /api/checkpoints/{id}/resume | 从原断点恢复 |

旧 `/query` 和 `/latest_answer` 保留有限兼容；新前端使用 `/api/*`。停止操作改为 POST，旧 GET /stop 不再产生副作用。

## 测试与边界

离线测试（不会消费模型额度）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_web*.py" -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_planner_workflow.py -v
cd frontend/agentic-seek-front
npm test -- --watchAll=false --runInBand
npm run build
```

真实联调脚本 `scripts/web_live_smoke.py` 需要显式 `--allow-paid-model`；只用于验证链路，不是成功率评测。Skill 实验强制要求独立 WORK_DIR 名为 `web-skill-smoke`，不要拿真实业务仓库当故障注入样例。

浏览器脚本 `scripts/web_ui_smoke.cjs` 需要可用的 `playwright` Node 包及本机 Chrome；默认只做 UI 检查，添加 `--live` 才提交真实模型请求。结果与截图写入 `.web-runtime`。

2026-10-14 已恢复本机联网搜索：保留并改名隔离 Docker 损坏的临时通信目录，备份设置后关闭 Docker 自带 AI；没有重置或清理镜像、容器和数据卷。SearxNG 使用 `http://host.docker.internal:7897` 访问本机代理，并启用实测可用的 Bing。使用搜索时需保持代理客户端运行；若代理端口改变，请同步修改 `searxng/settings.yml` 的 `outgoing.proxies`，再执行 `docker restart searxng`。其他搜索引擎仍可能遇到验证码或限流，不保证全部可用。

已通过真实 Web Agent 联调（Python 官网搜索、打开网页、带来源回答），详情见 `test_outputs/search_recovery_20261014.md`。启动命令为 `.\start_web.ps1 -SkipBuild -StartSearch`；需先启动 Docker Desktop。若 Docker 再次出现同类 socket 错误，先退出 Docker 后检查临时目录，必要时用管理员 PowerShell 改名保留；不要恢复出厂设置。

默认只监听 127.0.0.1，并拒绝外部网页来源的执行请求。系统能读写文件、执行程序，**不要直接暴露公网**；生产部署还需要身份认证、用户隔离、工具沙箱、资源配额和审计。未单独实测原自动路由模型以及 C/Java/Go 等全部语言运行环境。
