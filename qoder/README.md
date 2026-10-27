# 研迹 / Research Desk

本地优先的研究进度记忆台，基于 [PowerContext](https://github.com/oceanbase/powercontext) 真实 Runtime 构建。

## 功能特性

- **项目与范围管理**：每个项目映射为一个 PowerContext Scope，支持重启后自动恢复
- **资料摄入**：通过 `capture` API 本地摄入笔记，自动提取为 Memory 条目
- **中文全文检索**：基于 SQLite FTS5 的中文分词搜索，支持"显存""注意力""支付"等关键词
- **精确引用回溯**：搜索结果携带 `MemoryCitation`，可点击回查原文和来源
- **继续工作清单**：自动按类别整理（已完成/阻塞/决策/下一步/参考），支持任务状态更新
- **幂等导入**：重复导入演示数据不会创建重复记录，准确报告新增/跳过数量

## 技术架构

```
┌─────────────────────────────────────────────────────┐
│  Frontend (static/index.html)                       │
│  - 中文 UI，移动端响应式                              │
│  - 暖白 + 青绿 + 橙色配色                             │
└────────────────────┬────────────────────────────────┘
                     │ HTTP API
┌────────────────────▼────────────────────────────────┐
│  Backend (FastAPI)                                   │
│  src/research_desk/server.py                         │
│  - /api/projects, /api/notes, /api/search            │
│  - /api/citation/get, /api/tasks/update-status       │
│  - /api/continue-work/{id}, /api/demo/import         │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Runtime (PowerContext BuiltinRuntime)               │
│  src/research_desk/runtime.py                        │
│  - Scope 管理，Source capture，Memory flush          │
│  - 确定性候选管线 (pipeline.py)                        │
│  - JSON 编码元数据持久化                               │
└────────────────────┬────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────┐
│  Storage (SQLite + aiosqlite)                        │
│  data/research_desk.db                               │
│  - Scopes, Sources, Memory entries, FTS index        │
└─────────────────────────────────────────────────────┘
```

## 环境要求

- Python 3.12+
- uv (Python 包管理器)
- Git (用于拉取 PowerContext 依赖)

## 安装与运行

### 1. 克隆仓库并进入目录

```bash
git clone https://github.com/rzx110/oceanbase_3-zgl.git -b 2026-competition-ecosystem
cd oceanbase_3-zgl/qoder
```

### 2. 安装依赖

```bash
# 使用 uv 安装依赖（冻结到 PowerContext git commit）
uv sync --frozen
```

### 3. 运行验证脚本

**Windows PowerShell：**

```powershell
$env:PYTHONPATH = "src"; uv run python verify.py
```

**通用（Bash / macOS / Linux）：**

```bash
PYTHONPATH=src uv run python verify.py
```

预期输出：

```
✓ Core flow verification PASSED
✓ Restart recovery verification PASSED
✓ Idempotent import verification PASSED
ALL VERIFICATIONS PASSED
```

### 4. 启动 HTTP 服务器

**Windows PowerShell：**

```powershell
$env:PYTHONPATH = "src"; uv run python -m research_desk.server
```

**通用（Bash / macOS / Linux）：**

```bash
PYTHONPATH=src uv run python -m research_desk.server
```

### 5. 访问界面

打开浏览器访问 `http://127.0.0.1:8000`

首次进入时无数据，点击"导入演示数据"加载两个虚构项目：
- **论文复现：Transformer 注意力机制**（6 条笔记）
- **每周业务复盘**（5 条笔记）

## API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/projects` | 列出所有项目 |
| POST | `/api/projects` | 创建项目 |
| POST | `/api/notes` | 添加笔记 |
| POST | `/api/search` | 搜索（返回带引用的结果） |
| POST | `/api/citation/get` | 获取引用原文 |
| POST | `/api/tasks/update-status` | 更新任务状态 |
| GET | `/api/continue-work/{id}` | 获取继续工作清单 |
| POST | `/api/demo/import` | 导入演示数据 |
| GET | `/api/demo/data` | 查看演示数据定义 |

## 演示数据说明

演示数据为**虚构内容**，仅用于展示功能：

- **论文复现项目**：模拟复现 Transformer 论文的研究过程，包含参考文献、进度记录、阻塞问题、决策和待办事项
- **每周复盘项目**：模拟团队周会记录，包含完成事项、阻塞问题、决策和参考资料

所有数据均为示例性质，不代表真实研究项目或业务指标。

## 核心实现细节

### PowerContext API 使用

1. **Source Capture**：使用 `runtime.sources.for_scope(scope_id).capture(CaptureSource(...))` 本地摄入
2. **Memory Flush**：调用 `runtime.memory.for_scope(scope_id).flush()` 触发候选管线
3. **Search**：使用 `SearchMemoryRequest(query, limit, mode="fts")` 全文检索
4. **Citation**：从 `MemoryHit` 构造 `MemoryCitation(memory_ref, entry_id, entry_version_id)`
5. **Source Record**：通过 `runtime.records.for_scope(scope_id).get_source(type, id)` 读取元数据

### 候选管线 (pipeline.py)

确定性管线，无需 LLM：
- 接收 `ContentSource` 对象（本地 capture）或 `SourceObservation`（远程 ingestion）
- 从 JSON 编码的 content 中提取文本
- 生成 `MemoryEntryInput(kind="research-note", text=content, sources=(source,))`

### 元数据持久化

由于 `SourceRecord.content` 仅存储文本，元数据通过 JSON 编码嵌入：
```python
content_payload = {"text": "...", "metadata": {"title": "...", "category": "..."}}
content_json = json.dumps(content_payload, ensure_ascii=False)
```

### 重启恢复

- 每个项目创建时设置 `idempotency_key=f"project:{project_id}"`
- 同时在 `external_references` 中存储 `ScopeExternalReference(kind="project_id", value=project_id)`
- 启动时调用 `reload_projects()` 从数据库重新发现所有 scope

### 幂等导入

- 导入前检查 source 是否已存在：`records.get_source("content", source_id)`
- 已存在则跳过并计数，不存在则 capture
- 返回 `{projects: N, notes: M, skipped: K}` 准确报告

## 安全与边界

- **CORS**：已移除 `allow_origins=["*"]`，静态页面与 API 同源
- **URL 验证**：来源 URL 仅允许 `http://` 和 `https://` 协议
- **输入验证**：空/空白项目名、空记录、非法类别、越界 limit 均返回 400 错误
- **XSS 防护**：前端使用 `esc()` 函数转义用户文本，避免 HTML/JavaScript 注入
- **跨项目隔离**：引用检索强制校验 `project_id`，跨项目引用返回 404

## 已知限制

- 中文分词依赖 SQLite FTS5 内置分词器，对专业术语可能需要自定义词典
- Memory 列表未实现分页（当前 `list()` 返回全部条目）
- 任务状态更新通过重新 capture 实现，会产生新的 source 版本

## 项目结构

```
qoder/
├── src/research_desk/
│   ├── __init__.py
│   ├── runtime.py          # PowerContext runtime wrapper（版本化 Source、重启恢复）
│   ├── pipeline.py         # 确定性候选管线（多版本合并）
│   ├── server.py           # FastAPI 后端
│   └── demo_data.py        # 虚构演示数据
├── static/
│   └── index.html          # 单页应用前端（XSS 安全）
├── evidence/
│   └── screenshots/        # 浏览器实测截图
├── data/                   # SQLite 数据库目录（自动创建，.gitignore 排除）
├── verify.py               # 端到端验证脚本
├── pyproject.toml          # 项目配置（PowerContext git commit 冻结）
├── uv.lock                 # 锁定依赖版本
├── .gitignore
└── README.md               # 本文档
```

## 依赖版本

- **PowerContext**: git commit `9db1753434162c0aa7c9f1189a1722e617880a52`
- **FastAPI**: >=0.115,<1
- **uvicorn**: >=0.34,<1
- **aiosqlite**: >=0.22,<1

## 许可证

本项目遵循 PowerContext 的 Apache 2.0 许可证。

## 验收清单

以下功能已通过实际运行验证（见 `verify.py`）：

- [x] 重启后项目和数据自动恢复
- [x] 重复导入报告实际新增/已有数量
- [x] 搜索结果携带精确 MemoryCitation
- [x] 引用可点击回查全文和来源
- [x] 继续工作清单支持任务状态更新
- [x] 中文词语检索（"显存""注意力""支付"）
- [x] 负例搜索（不存在的关键词返回 0 结果）
- [x] 边界情况处理（空名称、非法类别、未知项目等）
- [x] 同源 CORS，无 `allow_origins=["*"]`
- [x] URL 仅允许 http(s)，用户文本安全展示

**注意**：演示数据为虚构内容，不代表真实研究项目或业务指标。
