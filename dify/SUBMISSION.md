# Dify 作品提交清单

- 题目：Just for Fun——用 Dify 做一个好玩的智能应用。
- 作品：会记仇的奇物当铺。
- 仓库：<https://github.com/rzx110/oceanbase_3-zgl>。
- 分支：`2026-competition-ecosystem`；所有本题材料位于 `dify/`。
- 提交使用包含本目录的完整 40 位 commit SHA，可由 `git rev-parse HEAD` 获得。
- 在线入口：<https://udify.app/workflow/jvk3Zdb4WkHl6qLC>，依赖作者本机及临时隧道在线；独立复现按 README 配置自己的后端。
- 主应用：`0af47a97-849f-4918-80e9-563bf1e1b256`。
- 交付文件重新导入验证应用：`9e7b235a-c80d-4ffe-9ee6-f2fb3418f613`。

| 要求 | 交付物/验证路径 |
| --- | --- |
| Dify 核心编排 | `workflow/oddity-pawnshop.yml`，10 节点完整图，实际云端导出 |
| 至少一个比赛组件实际参与 | `src/oddity_pawnshop/memory.py`，冻结 PowerContext Runtime + Scope + CaptureSource + records |
| 输入、互动、反馈完整 | 原生 Dify Web App、规则、角色台词、分数和可回查账本 |
| 所有 DSL、后端及依赖 | `workflow/`、`src/`、`pyproject.toml`、`uv.lock` |
| 启动、配置及重置 | `README.md`，新玩家代号或新数据库路径 |
| 可重放样例 | `samples/scenario.json` |
| 技术说明及实际记录 | `TECHNICAL.md`、`evidence/README.md`、`evidence/dify-ui/` |
| 提交前重新导入并运行 | 第二个应用的 `reimport-*` 证据及真实 Source 回查 |

公开 DSL 含占位符；个人密钥、后端 token、私有导入副本、作者 SQLite 数据库、虚拟环境均不提交。模型配置由复现者在 Dify 工作空间提供。准备脚本不会向控制台打印令牌。
