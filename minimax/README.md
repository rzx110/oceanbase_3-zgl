# 规有所依 / SDD Context Desk

为一次实际软件修改提供可追溯的规格、代码和测试上下文，并保存可跨进程复用的任务进度。
验证业务是本仓库原创的小型任务看板：给 `list_tasks` 增加精确状态筛选，保留默认列表行为。
演示任务和初始数据均为虚构，不涉及真实用户资料。

## 实际技术分工

| 技术 | 在本次任务中的实际作用 | 记录 |
| --- | --- | --- |
| MiniMax Code 官方网页端，界面选择 M3.1-Flash default | 接收真实检索上下文，生成 `taskboard/service.py`；提交的 after 文件与其公开回复逐字一致 | [请求](evidence/minimax-request.json)、[公开回复](evidence/minimax-response.txt)、[开发记录](evidence/minimax-development.json)、[截图](evidence/minimax-development.jpg) |
| seekdb D0，MySQL/TLS，PyMySQL 1.2.3 | 保存规格、代码和测试；SQL 按项目、类型和任务词项检索，结果直接构造上下文 | [真实导入与 SQL](evidence/database-import.json)、[模型调用前上下文](evidence/context-before-model.json) |
| PowerContext 1.2.0，官方 Git SHA `9db1753434162c0aa7c9f1189a1722e617880a52` | 以独立任务 Scope 和不可变 Source 保存进度/测试结果，关闭 Runtime 后在新进程读取并决定下一步 | [写入及重启复现](evidence/reproduction/)、[上下文测试](evidence/context-tests.json) |

实际 MiniMax 开发采用网页交互，不声称使用了 API Key 或取得 API response ID。
仓库另提供 HTTP API 接入代码 `src/sdd_context/agent.py`；这是可选的再次生成入口，重放已提交样例无需模型密钥。
没有导出模型私有推理内容、账号凭据或数据库密码。

## 环境与初始化

需要 Python 3.12、Git、uv。依赖完整版本见 `uv.lock`；本次验证 Python 为 3.12.3。
无需 Docker、Node.js、本地 seekdb 安装或 Qoder。PowerContext 安装自上述固定官方提交。

```bash
git clone --branch 2026-competition-ecosystem https://github.com/rzx110/oceanbase_3-zgl.git
cd oceanbase_3-zgl/minimax
uv sync --frozen --python 3.12
uv run --frozen python scripts/create_seekdb.py
```

`create_seekdb.py` 通过 [seekdb 官方 D0](https://d0.seekdb.ai/) 创建免费的独立测试实例，
将连接参数写入 Git 忽略的 `.local/d0.json`，不打印凭据。实例有效期最多七天；
评审应自行初始化新实例，不依赖作者原实例仍然有效。实测服务版本为
`5.7.25-OceanBase seekdb-v1.2.0.0`。仅存储本仓库的演示资料。
需要访问 D0 HTTPS 初始化服务及返回的 MySQL/TLS 地址，TLS 验证保持开启。

## 一条命令重放完整样例

```bash
uv run --frozen python scripts/verify_demo.py --output .local/review-1
uv run --frozen python -m pytest -q -p no:cacheprovider tests/test_context_workflow.py
```

第一条命令使用新的随机项目 ID 和空 PowerContext 数据库，依次导入资料、检索、核对模型记录与源码、
运行修改前后同一组测试、保存实际结果，再启动独立进程恢复任务。它不再次请求模型。
第二条命令实际连接 seekdb，验证上下文来源、项目隔离、跨进程恢复、规格更新及来源删除。
网络/数据库初始化错误不会被计作修改前的功能失败。

本次实际结果：

| 测试 | before | MiniMax after |
| --- | --- | --- |
| 状态筛选目标功能 | 15 failed，退出码 1 | 15 passed，退出码 0 |
| 原有列表回归 | 4 passed，退出码 0 | 4 passed，退出码 0 |
| 上下文模块集成 | 规格更新、来源删除、状态恢复和查询隔离共 4 项通过 | 同一模块 |

原始输出、命令、退出码和源码/测试 SHA-256 见 [before](evidence/before/)、[after](evidence/after/)、
[完整复现报告](evidence/reproduction/report.json)。两阶段目标测试和回归测试的文件 SHA 完全一致。
代码差异见 [code.diff](evidence/reproduction/code.diff)。
展示日志仅规范化本机目录前缀和文本换行；测试结果、退出码与源码指纹保持原值。

## CLI 调用入口

所有命令在 `minimax/` 运行。输出 JSON 中的来源包含相对路径、行范围、Python 符号或规格 ID、
原文、原始字节 SHA-256；检索不是固定返回样例答案。

```bash
uv run --frozen python -m sdd_context --output .local/import.json import
uv run --frozen python -m sdd_context --output .local/context.json context
uv run --frozen python -m sdd_context context --query "invalid status list_tasks"
uv run --frozen python -m sdd_context --output .local/resumed.json resume
```

- `--root`：含 `project.json` 的工程根目录，默认本目录。清单声明要导入的规格、代码和测试 glob。
- `--state-dir`：本次任务的 PowerContext 数据目录，默认 `.local/state`。
- `--task`：任务 JSON，默认 `demo/task.json`，含任务描述、查询词、项目及任务 ID。
- `import`：完整读入清单文件后，在一个数据库事务中替换该项目资料；删除的文件也会从索引移除。
- `context` / `resume`：真实查询 seekdb，读取已保存任务，返回来源、已完成事项、待办、验证结果和下一步。
- `save-state STATE.json`：将调用方给出的任务记录保存到 PowerContext。相同记录重复保存不增加 Source。

规格更新后重新运行 `import` 和 `context`。未重新导入的文件变更会明确报错；
重新导入后返回新正文和新摘要，并将旧验证标记为需重新检查。

## 验证任务、数据与预期行为

任务定义和必需来源清单见 [demo/task.json](demo/task.json)。上下文必须包含：

1. `demo/specs/task_status.md` 中的 REQ-STATUS-01、REQ-STATUS-02、REQ-COMPAT-01、REQ-COMPAT-02。
2. `demo/before/taskboard/service.py` 中的 `Task` 和 `list_tasks`。
3. `demo/tests/test_status_filter.py` 及 `demo/tests/test_regression.py` 中的功能和兼容测试。
4. 保存任务后，输出必须包含已完成、待办、实际测试结果和被后续执行采用的 `next_action`。

初始数据为 `demo/data/tasks.json`。可运行 after 业务 CLI：

```powershell
# PowerShell
$env:PYTHONPATH = "$PWD/demo/after"
uv run --frozen python -m taskboard --data demo/data/tasks.json --status todo
```

```bash
# Linux/macOS
PYTHONPATH=demo/after uv run --frozen python -m taskboard --data demo/data/tasks.json --status todo
```

预期仅返回 T-01、T-02，保持原输入顺序；不传 status 时仍返回全部四条。
空/未知/大小写或首尾空格不匹配的筛选值及其他类型应抛 ValueError，空输入也要校验。
输入序列和 Task 对象不变，重复项和对象身份保留。

重置方式：再次执行完整重放时换用 `.local/review-2` 等新的输出目录，程序自动分配新项目和任务状态。
不改写已保存的测试证据。数据库实例过期时用新文件初始化：

```bash
uv run --frozen python scripts/create_seekdb.py --output .local/d0-new.json
```

随后设置环境变量 `SDD_SEEKDB_CONFIG` 为该文件路径。无需作者机器上的任何文件。

## 再次让 MiniMax 修改代码

实际网页开发的上下文生成入口：

```bash
uv run --frozen python scripts/prepare_web_task.py
```

它真实导入和检索数据库，保存 baseline-tested 状态，重新打开 PowerContext，然后输出
`evidence/minimax-prompt.txt`。将此文本交给官方 MiniMax Code，保存其公开 JSON 回复，
运行 `scripts/apply_web_response.py --help` 查看受限补丁应用入口。已有 after 快照不会被覆盖。
这类再次开发应在工作副本中进行，以保留平台已冻结提交的原始证据。

可选 HTTP API：在官方平台创建密钥后运行 `scripts/configure_minimax.py`，以隐藏输入保存本机配置；
也可设置 `MINIMAX_API_KEY`、`MINIMAX_MODEL` 和 `MINIMAX_BASE_URL`。随后：

```bash
uv run --frozen python -m sdd_context agent --snapshot .local/api-generated --record .local/api-development.json
```

只有官方 MiniMax HTTPS 端点被接受；代码只允许修改声明的文件，不允许修改测试。
本次验收证据来自真实网页会话，HTTP 适配器不被冒充为已经在线调用的路径。

## 已知范围与来源

本模块面向小型工程：以文件为检索单元，seekdb 中进行词项匹配与类型分组排序，
不声称通用语义检索、无限上下文或生产级并发吞吐。中文查询可做字面匹配，本样例使用英文代码标识符。
PowerContext 状态为单任务追加快照，未实现多写入者冲突合并。D0 是有到期时间的测试服务，需网络。
评审可以直接重放提交快照；重新生成代码需要自己的 MiniMax 可用额度。

PowerContext 来源为 [oceanbase/powercontext](https://github.com/oceanbase/powercontext)，Apache-2.0。
seekdb 来源为 [oceanbase/seekdb](https://github.com/oceanbase/seekdb)，实际使用官方 D0 服务。
主要上下文模块、样例和测试由 Codex 编写，状态筛选的实际业务修改由 MiniMax 生成并经独立测试。
一页技术说明见 [TECHNICAL.md](TECHNICAL.md)，本项目源码许可见 [LICENSE](LICENSE)。
