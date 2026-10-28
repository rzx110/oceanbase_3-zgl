# 证据索引

本目录只包含虚构角色演示和真实运行结果，不含后端令牌、模型密钥、私有 DSL 或 SQLite 数据库。

| 文件 | 证明范围 |
| --- | --- |
| `local-tests.json` | 7 个本地测试的命令、退出码、输出、源码哈希；不是 Dify 主机执行 |
| `backend-http.json` | 独立空库、真实 HTTP 子进程、重启、幂等和精确旧 Source 等 8 项检查；不是 Dify 执行 |
| `dify-ui/round-01-output.txt` 至 `round-05-output.txt` | 主 Dify 应用实际复制的五轮反馈及 Source 凭据 |
| `dify-ui/backend-restart.json` | 第二轮后的真实后端进程更换，同一数据库及状态保留 |
| `dify-ui/round-03-after-restart.jpg` | 重启后 Dify 继续道歉的实际界面 |
| `dify-ui/round-05-result.json`、`round-05-details.txt`、`round-05-trace.jpg` | 秘密解锁、SUCCESS、9 节点和实际 1062 tokens |
| `dify-ui/round-06-*` | 只读回忆仍为第五轮、SUCCESS、5 节点及 0 tokens |
| `dify-ui/public-web-*` | 已发布原生 Web App 的独立玩家实际执行 |
| `dify-ui/reimport-*` | 交付 DSL 绑定自己环境变量后，重新导入独立应用并运行新玩家 |
| `dify-ui/powercontext-audit.jsonl` | 只筛选主样例玩家的实际后端记忆读写/回查审计 |
| `dify-live-verification.json` | 对 UI 返回凭据逐个通过真实 Runtime 后端精确回查，验证分数、轮次、重启和隔离 |

截图为浏览器真实采集，文本来自可见 UI 的复制结果或可访问性信息。模型台词不是固定答案，重放时可以变化；不能把证据 JSON 当作启动所需数据，也不能将本地自测等同于官方评分。
