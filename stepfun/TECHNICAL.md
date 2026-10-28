# 端研 Edge Notes｜一页技术说明

**架构与环境。** 浏览器 Worker 计算文档和查询 embedding → Node 仅转发向量 → seekdb-js 1.4.0 → 本机 seekdb 1.4.0，数据库内全文+向量 RRF → 展示原文、来源和块号。实测：Windows 11、Edge 156、20 逻辑核、16 GB、NVIDIA Ampere（WebGPU 返回的信息）；Transformers.js 3.8.1，WASM 四线程。32 篇原创笔记、18 个固定问题，每配置独立集合，首次请求另记，预热后三轮，全部原始记录见 `evidence/browser-benchmark.json`。

**模型与量化。** Apache-2.0 的 Xenova/all-MiniLM-L6-v2，固定 revision `751bff37182d3f1213fa05d7196b954e230abad9`；384 维、mean pooling、L2。采用上游公开 FP16 / 动态 INT8 ONNX 文件；下载脚本验证固定摘要，检查确有 FLOAT16 / INT8 权重与整数 MatMul。按“一个权重+共用配置/tokenizer”口径，FP32 91.33 MB、FP16 46.24 MB（1.975×）、INT8 23.92 MB（3.819×）。

| 实测配置 | 文档/秒，中位数 | 查询 E2E ms | seekdb ms | Recall@3 / nDCG@3 |
|---|---:|---:|---:|---:|
| WASM FP32，B1 | 21.4 | 37.9 | 8.2 | 1.000 / 0.959 |
| WASM INT8，B1 | 27.2 | 36.9 | 9.2 | 1.000 / 0.959 |
| WASM INT8，B8 | 28.0 | 36.3 | 8.8 | 1.000 / 0.959 |
| WebGPU FP32，B8 | 184.5 | 47.1 | 7.4 | 1.000 / 0.959 |
| WebGPU FP16，B8 | 213.4 | 44.7 | 7.5 | 1.000 / 0.959 |

**结果与计时。** 同批大小下 GPU FP16 相对 GPU FP32 吞吐提高 15.6%；WASM INT8 B1 相对 FP32 B1 提高 27.2%，INT8 B8 相对 B1 提高 3.0%。此小集未观察到量化质量下降；短查询 GPU 仍比 WASM 慢，不据此宣称全面加速。E2E 含 Worker、HTTP、DB、DOM 更新及布局，不含显示刷新。冷模型加载使用唯一同源 URL、no-store、关闭模型缓存，ORT runtime 可热：FP32 1900 ms、INT8 471 ms、FP16 822 ms；实际权重传输分别 448/391/252 ms，热会话复用约 2.7/1.0/0.8 ms。这是本机 HTTP 传输，不是互联网带宽对比。

**数据库与状态。** 每库独立 collection：原文、384 维 cosine HNSW、space 全文索引、来源/块号/模型配置 metadata；embeddingFunction=null。调用 SDK hybridSearch，RRF 常数 60、窗口 20，融合在 seekdb 内执行。完整 payload 先校验；稳定 ID upsert、替换遗留尾块、批末 refresh_index。相同 INT8 B8 配置下，仅改变刷新策略，逐文档写入/刷新中位数 19,908 ms，整批一次 786 ms。共享连接的完整 SDK 操作串行化；忙碌时锁定切换；切库及实验返回后清除旧结果。

**后端与验证。** 显式优先 WebGPU FP16，无 shader-f16 使用 GPU FP32，无 adapter 或初始化失败自动 WASM INT8，界面显示实际配置。真实两后端推理与质量实验通过；应用禁用 GPU 策略也完成真实入库/检索（明确标记为策略测试）。Edge 重启前曾实际观察到无 adapter 时自动 WASM 完整流程，未用这次观察充当 GPU 性能数据。真实数据库测试、增量/幂等/隔离通过，数据库与应用进程重启后 32 篇正文及向量摘要一致、浏览器可重新回查。仅证明本机与原创小型英文集，不外推通用语料质量。无云推理、服务端预计算或替代数据库。

**复现。** Windows：`npm ci` → `npm run db:setup` → `npm run setup` → `npm run build`；分别运行 `npm run db:start`、`npm start`，访问 http://localhost:8791 。页面实验按钮重做全部实测；`npm test` 验证 DB 合同；详见 README。
