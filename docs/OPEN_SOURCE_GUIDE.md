# AgenticSeek 开源仓库说明

## 目录职责

| 目录 | 内容 |
| --- | --- |
| `sources/` | 多智能体、状态机、故障恢复、Skill、工具和 Web 运行时 |
| `prompts/` | 各角色 Agent 的提示词 |
| `frontend/agentic-seek-front/` | React Web 前端 |
| `tests/` | 单元测试和集成测试 |
| `benchmarks/` | 可复现实验数据集、评测脚本和报告 |
| `scripts/` | 安装、启动和联调脚本 |
| `searxng/` | 本地 SearXNG 配置 |
| `docs/` | 架构、贡献和开源说明 |

## 不提交到 Git 的内容

`.env`、虚拟环境、`workspace/`、`.logs/`、`.web-runtime/`、浏览器截图、前端构建产物、个人笔记和测试运行生成的 JSON/JSONL 已加入 `.gitignore`。不要把 API Key、个人工作目录、聊天记录或本机代理地址提交到仓库。

## 新环境初始化

```powershell
Copy-Item .env.example .env
Copy-Item config.example.ini config.ini
# 编辑 .env，至少设置 WORK_DIR；使用云模型时再填写对应 API Key
docker compose --profile core up -d redis searxng
.\start_web.ps1 -SkipBuild -Background
```

打开 `http://127.0.0.1:7777`。如果只运行 CLI，可按原 README 的 CLI 启动说明操作。

## 提交前检查

```powershell
git status --short --ignored
git diff --check
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_web*.py" -q
```

提交前确认 `git status` 中没有 `.env`、日志、工作区、个人文件和本机密钥。
