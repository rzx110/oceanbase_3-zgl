# 研迹 / Research Desk 提交材料

- 项目网站：https://research-desk-mbqdjnphp29.qoder.zone
- 仓库 HTTPS URL：https://github.com/rzx110/oceanbase_3-zgl.git
- 分支：`2026-competition-ecosystem`
- 项目目录：`qoder/`
- 已验证核心实现 commit：`97cbfbeb2e4b85a71512ce49cfa6c018831b870f`
- 正式提测使用此分支最终 HEAD 的完整 40 位 SHA；该值记录在比赛提测记录中，避免本文件自引用提交哈希。

## Qoder 账号及开发证明

- Qoder 账号：`nick2566971799`
- Qoder 用户标识（UID 提交值）：`01a17cc8-3479-75ef-bc6a-658a78ab19df`
- 标识来源：官方 `qoderclicn status -o json` 返回的头像资源路径 `/users/{id}/`；此来源是用户资源标识提取，Sites API 未单独返回 UID 字段。未将阿里云账号 ID 混作 Qoder 用户标识。
- Qoder CLI CN 版本：1.1.79。
- 实际开发会话：`4d600a49-75dc-4311-a3f7-811be5815bca`。
- [实际开发记录](evidence/qoder-development.md)、[用户提供的真实 Qoder 窗口截图](evidence/screenshots/qoder-cli-development.png)。
- 主要源码和展示页由真实 Qoder 会话完成；Codex 负责需求核对、独立验证和提交材料整理。

## 实际使用 PowerContext

每个项目对应一个 Scope。通过 Runtime Source Capture 保存资料，由确定性 pipeline 写入 scoped Memory；关键词检索返回可回查的 MemoryCitation。任务状态形成新采集修订，历史引用继续指向原版本。数据保存在本地 SQLite，重启后恢复项目、最新状态和逻辑记录数。运行时无需模型 API Key。

PowerContext 依赖固定为官方 commit `9db1753434162c0aa7c9f1189a1722e617880a52`，安装来源已通过 distribution 的 `direct_url.json` 断言核对。

## 复现

```bash
git clone --branch 2026-competition-ecosystem https://github.com/rzx110/oceanbase_3-zgl.git
cd oceanbase_3-zgl/qoder
uv sync --frozen
uv run --frozen python verify.py
uv run --frozen python -m research_desk.server
```

打开 `http://127.0.0.1:8000`，导入虚构演示数据，选择项目，搜索“显存”，点击“查看原文”；在继续工作中将待办标记为完成，确认计数不增加。关闭并重启后再次检索。

- [七个验证场景的实际输出](evidence/verification.txt)
- [九项独立检查](evidence/independent-checks.json)
- [网页操作与实际 HTTP 服务重启检查](evidence/browser-checks.json)
- [匿名无 Cookie 的网站和三张图片访问检查](evidence/anonymous-access.json)
- [Qoder Sites 发布记录](evidence/publication.json)

网站为项目展示和评审入口，完整 Python 应用按上述步骤在本地运行。展示资料均为虚构演示数据，没有使用人数或提效比例的虚构声明。源码与 PowerContext 依赖的许可见 [LICENSE](LICENSE)。
