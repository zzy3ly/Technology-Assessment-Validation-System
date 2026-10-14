# 联网搜索恢复验证（2026-10-14）

## 修复

- Docker Desktop 启动被损坏的 AF_UNIX 临时通信文件阻断；用户通过管理员 PowerShell 改名保留两个临时目录后，Docker Engine 29.2.1 启动成功。没有恢复出厂设置或删除数据卷。
- Docker settings-store.json 已备份，EnableDockerAI 改为 false；不影响项目 DeepSeek API。
- 容器直连 Bing 出现 TLS EOF；通过 host.docker.internal:7897 代理测试返回 HTTP 200。SearxNG 设置该代理，搜索超时从 3 秒调整为 10 秒，启用 Bing。
- 实测 `search: Python official website` 会返回 Google/Yahoo 等搜索站点，而去掉前缀后返回 Python 官网。工具现在去除模型格式前缀，并由 requests 正确编码表单，支持 &、+ 等字符。
- 修复显式 base_url 参数被忽略，以及正常结果标题含 Error 被误判失败的问题。

## 实测数据

| 检查 | 结果 |
| --- | --- |
| redis、searxng 容器 | 均运行 |
| 搜索：search: Python official website | 10 条，首条 Python 官网 |
| 搜索：FastAPI official documentation | 10 条，首条 FastAPI 官网 |
| 搜索：Python 异步编程 官方文档 | 10 条；只验证有结果，不代表结果都精确相关 |
| 新增搜索回归测试 | 3/3 通过 |
| 原有 test_web*.py | 22/22 通过 |

真实模型完整链路：任务 `71967d71c2764a1eb0ba7ea4bae40e01`，模式 web，查询 `Find the official Python website and briefly explain what Python is. Cite the source URL.`。09:00:25.632 UTC 开始、09:01:03.769 UTC 完成，约 38.14 秒；终态 succeeded，success=true，browser_used=true，error=null，总 token 10,184。实际导航到 https://www.python.org，最终回答包含该来源。任务快照可从 Web 历史或 GET /api/tasks/71967d71c2764a1eb0ba7ea4bae40e01 查看。

首次完整联调（修复 search: 前缀前）发生搜索跑偏，不能计作有效通过样本。本记录是功能恢复冒烟测试，不是任务成功率评测，也不证明所有搜索引擎可用。部分上游仍有 CAPTCHA、403、429。当前配置依赖本机代理客户端在 7897 端口运行；端口更换需要同步更新配置。
