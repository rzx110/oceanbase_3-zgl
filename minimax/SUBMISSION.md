# MiniMax 赛题提交索引

- 题目：规有所依——面向软件持续演进的 SDD 上下文管理系统。
- 仓库 HTTPS URL：`https://github.com/rzx110/oceanbase_3-zgl.git`
- 分支：`2026-competition-ecosystem`
- 项目目录：`minimax/`
- 基于官方生态分支冻结基线 `d647275c308e09b7b623f322a26f5c4e85734fb0`。
- 正式提交使用上述分支最终 HEAD 的完整 40 位 SHA，以平台固定提交记录为准。

| 必做项 | 核对材料 |
| --- | --- |
| 可运行 | README 环境/初始化步骤、uv.lock、scripts/verify_demo.py |
| MiniMax 实际参与 | evidence/minimax-request.json、minimax-response.txt、minimax-development.json、minimax-development.jpg |
| 上下文正确且可更新 | demo/task.json 的必要来源与条目；evidence/context-before-model.json；tests/test_context_workflow.py 及实际日志 |
| seekdb 实际参与 | evidence/database-import.json、context-before-model.json 中的真实 SQL、绑定参数和返回行 |
| PowerContext 状态复用 | evidence/reproduction/powercontext-write.json、context-resumed.json、process-restart.json |
| 实际代码修改有效 | demo/before、demo/after、evidence/reproduction/code.diff、before/after 相同测试及实际输出 |

MiniMax 的实际开发使用其官方网页助手；没有声称已调用 HTTP API。
开发记录和代码变更可直接核对，重放不要求模型重新生成相同补丁。
数据库和模型凭据不随 commit 交付；运行者通过官方 D0 初始化自己的实例。
