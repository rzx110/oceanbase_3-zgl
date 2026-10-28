# 端研 / Edge Notes

阶跃星辰赛题：浏览器本地 embedding、量化与 seekdb 数据库内混合检索。无需模型 API Key。文档、查询的向量均由浏览器 Web Worker 计算；Node 服务只接收向量并调用 seekdb-js，不加载推理模型。

## Windows 11 快速启动（本机验证环境）

要求 Node.js 20.9+、当前版 Edge/Chrome、约 3 GB 可用内存、约 4 GB 可用磁盘。初次安装需要联网下载依赖、公开模型和官方数据库包；准备完毕后推理和检索均在本机执行。

在本目录打开 PowerShell：

```powershell
npm ci
npm run db:setup
npm run setup
npm run build
```

在第一个终端启动数据库并保持运行：

```powershell
npm run db:start
```

在第二个终端启动应用：

```powershell
npm start
```

访问 **http://localhost:8791**（使用 localhost 的安全上下文，不能换成普通 HTTP 局域网 IP）。选择“加载模型”→“导入演示集”→自然语言检索。也可粘贴文本或导入 `.txt` / `.md` 文件。默认优先 WebGPU FP16；无 adapter、无 FP16 或初始化失败的处理会显示在界面中。WASM INT8 可手动选择用于对照。

Windows 数据库采用 **官方 seekdb 1.4.0 本地 server + seekdb-js 1.4.0**。`db:setup` 获取官方 seekdb-go Windows 模块中附带的 server 二进制，两层 SHA-256 固定并验证；不需要安装系统服务、不依赖 Docker。`db:start` 在应用目录中运行它，数据保存在 `.local/seekdb-server/`。首次初始化为此专用实例生成随机数据库密码，保存在忽略提交的 `.local/db-config.json`。HTTP 服务仅监听 127.0.0.1，不开放跨域 API；数据库使用本机 2885 端口。请保留私有配置与数据库目录，避免手动删除后失去访问配置。

Linux / Apple Silicon macOS 可执行同样的依赖、模型和构建命令；不设置 `SEEKDB_HOST` 时 `npm start` 使用 seekdb-js 的嵌入式模式。跨平台路径已提供，本次真实运行证据来自 Windows 11，未声称在其他系统完成实测。已部署的本地 seekdb server 也可通过环境变量 `SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_DATABASE`、`SEEKDB_USER`、`SEEKDB_PASSWORD` 配置。

## 模型、量化与资源获取

- 模型：[Xenova/all-MiniLM-L6-v2](https://huggingface.co/Xenova/all-MiniLM-L6-v2/tree/751bff37182d3f1213fa05d7196b954e230abad9)，Apache-2.0，revision `751bff37182d3f1213fa05d7196b954e230abad9`；原始模型为 sentence-transformers/all-MiniLM-L6-v2。
- `npm run setup` 从固定 revision 获取 FP32、FP16、INT8 的公开 ONNX 发布文件及共用 tokenizer/config，按固定 SHA-256 校验，缓存于 `.local/models/`。采用发布者已经转换和量化的文件，不声称自己训练或生成了这些发布权重。
- FP16 文件有 101 个 FLOAT16 initializer；INT8 文件由 `onnx.quantize` 生成，包含 `DynamicQuantizeLinear` / `MatMulInteger` 和 INT8 权重。部分浮点算子、scale 与整数索引保留，INT8 不表示所有张量都是八位。实物检查见 `evidence/quantization-inspection.json`。
- 复现采用发布量化文件的步骤是：固定版本下载 → 校验 → 以 Transformers.js 的 `dtype:'fp32'/'fp16'/'q8'` 显式选择对应文件 → 同语料重建独立集合 → 对比质量和耗时。可选 `uv run --with onnx==1.17.0 python scripts/inspect-models.py` 重跑格式检查；这不是应用依赖，也不执行 embedding。
- 统一体积口径：**一个权重文件 + 五个共用 config/tokenizer 文件**。FP32 91,331,916 B；FP16 46,242,135 B（1.975×）；INT8 23,916,680 B（3.819×）。原始逐文件大小、摘要在 `evidence/model-files.json`。三个实验模型都保留在本机的总占用不能当作单模型体积。
- 固定 mean pooling、L2 normalization、384 维、最大 256 token。原文按最多 800 字符且优先空白边界切块，存储原始块与来源。模型为英文模型；不宣称中文召回质量。

运行时仅从同源 `/models/` 和 `/ort/` 加载，`allowRemoteModels=false`。模型文件下载不调用推理服务。WebGPU 不支持 shader-f16 时采用 GPU FP32；GPU 初始化失败自动降级 WASM INT8。COOP / COEP 头及同源 runtime 资源支持 WASM 多线程，最多四线程。旧会话在切换配置前释放，后续同配置请求复用同一会话。

## 存储与检索

每个知识库对应一个 seekdb-js collection：

| 数据 | 配置 |
|---|---|
| id | 来源名 + 块序号的 SHA-256（演示集使用固定原创文档 ID） |
| document | 完整文本块 |
| embedding | 浏览器生成的 384 维归一化向量，cosine HNSW |
| metadata | source、chunk、backend、dtype、model、revision |
| 全文索引 | `FulltextIndexConfig('space')`，英文分词 |
| 自动 embedding | **null**，不安装/调用 SDK 默认向量化功能 |

集合名称、展示名、模型空间与创建时间也存于 seekdb 的 `stepfun_catalog` 表。API 的集合 ID 在数据库注册表中验证。`collection.hybridSearch` 以自然语言文本、真实查询向量调用 seekdb 的 `DBMS_HYBRID_SEARCH`，数据库执行全文分支、向量分支和 RRF，`rankConstant=60`、候选窗口 20。客户端只展示返回顺序和原文，不做两路结果拼接、浏览器向量检索或生成假引用。

完整 payload 先校验维度、数值、归一化、来源与 ID。按批 `upsert`，删除同来源被替换后遗留的尾部块，最后调用 `refresh_index()`，完成后再展示成功。连续重复导入幂等。会话仅把当前集合 ID 存到 localStorage，持久化内容仍全部在 seekdb。导入/查询时锁定切换，服务器为共享连接中的 SDK 完整操作排队，防止 hybridSearch 的 SQL session 变量相互覆盖。

## 复现实验与检查

1. `npm test`：真实本机 seekdb 验证整批输入校验、替换尾块、重复 upsert、索引可见、并发集合隔离、重连和 limit。**这些数据库测试用明确标注的合成向量，不能当作浏览器推理证据。**
2. 网页“运行量化与性能对比”：同设备、32 篇原创资料和 18 个固定自然语言问题；每个配置首次导入/查询单独记录，预热后重新计算文档和查询向量，测三轮，保留每条查询与每个批次的原始数据。质量使用 `src/demo.js` 公开 relevance judgments 计算 Recall@3、二值 nDCG@3，没有预存 embedding 或检索结果。
3. 配置依次为 WASM FP32 B1、WASM INT8 B1、WASM INT8 B8、WebGPU FP32 B8、WebGPU FP16 B8。对比 INT8 B1 与 INT8 B8 时仅改变批大小；对比 WebGPU FP32 与 FP16 时保持批大小。不可用配置记录跳过原因，不能伪报成功。
4. 索引策略单独对照：WASM INT8 B8，同样语料每轮重新向量化，逐文档写入/刷新与整批写入/一次刷新，三轮原始数据分别保存。数据库刷新影响原文和索引，不使用虚拟延迟。
5. 网页“验证增量、幂等与集合隔离”，以及“验证禁用 GPU 后自动降级”：真实浏览器推理、真实 DB 写入和回查。禁用 GPU 验证是应用中的显式测试策略，会标注 `notPhysicalHardwareTest:true`；它不能单独证明物理无 GPU 环境的行为。
6. 下载页面提供的原始 JSON；结果来自本次应用，不依赖产品目录之外的数据。实验记录 localStorage 只缓存诊断 JSON，不参与检索。提交材料中的 `evidence/browser-benchmark.json`、截图和一页 `TECHNICAL.md` 说明实际完成的验证及边界。

计时定义：模型加载包含同源模型读取、ONNX session 创建；逐资源 HTTP 耗时也保留，区分浏览器缓存命中。向量化计时位于 Worker 内；写入/刷新与数据库检索在 Node 端单独计时；E2E 从发起查询至推理、HTTP、数据库返回、结果 DOM 更新及布局完成，**不包含显示器刷新**。浏览器窗口最小化会暂停 rAF，因此不用等待 rAF 当作检索耗时；记录每条查询的页面可见性。性能数字只代表记录中的设备与小型原创测试集，不表示通用语料上的保证。

## 文件布局

- `src/embedding-worker.js`：唯一 embedding 执行位置。
- `src/database.mjs`：真实 seekdb-js 集合、写入、刷新和混合检索。
- `src/main.js`、`src/style.css`：应用、状态和可重复实验 UI。
- `src/demo.js`：完整原创语料、问题与 relevance judgments。
- `scripts/`：固定模型获取、本地数据库获取与启动、自测。
- `evidence/`：逐文件摘要、原始实测、功能验证与截图。

数据库、模型大文件、依赖、构建输出、凭据均不提交 Git。评测方从提交 commit 安装依赖、运行准备脚本即可重建。
