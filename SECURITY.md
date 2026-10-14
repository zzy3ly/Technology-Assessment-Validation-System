# Security Policy

## Reporting a vulnerability

请不要在公开 Issue 中发布 API Key、聊天记录、浏览器 Cookie、工作区文件或可复现漏洞的敏感细节。请通过 GitHub Security Advisories 或仓库维护者的私下联系方式报告安全问题，并提供最小复现步骤。

## 本地运行注意事项

- `.env` 只保存本机，不要提交到 Git。
- Web 服务默认只监听 `127.0.0.1`；不要未经认证暴露到公网。
- Agent 可以读写配置的工作目录并执行程序。生产环境应增加身份认证、沙箱、资源限制和审计。
- 使用真实模型进行测试可能产生 API 费用，并会将任务内容发送给模型服务商。
