"""Fictional demo data for Research Desk.

Two research projects with realistic notes covering different categories.
All content is fictional and for demonstration purposes only.
"""

DEMO_PROJECTS = [
    {
        "project_id": "paper-repro",
        "title": "论文复现：Transformer 注意力机制",
        "summary": "复现 Vaswani et al. 2017 的 Attention Is All You Need 核心实验",
        "notes": [
            {
                "note_id": "ref-001",
                "title": "原始论文关键参数",
                "body": "模型维度 d_model=512，注意力头数 h=8，FFN 维度 d_ff=2048。使用 6 层 encoder 和 6 层 decoder。学习率 warmup 4000 步，peak lr=0.001。",
                "category": "reference",
                "url": "https://arxiv.org/abs/1706.03762",
                "created_at": "2026-10-20T09:00:00",
            },
            {
                "note_id": "progress-001",
                "title": "数据预处理已完成",
                "body": "WMT 2014 英德翻译数据集已完成 BPE 分词，词表大小 37000。训练集 4.5M 句对，验证集 newstest2013，测试集 newstest2014。",
                "category": "progress",
                "created_at": "2026-10-21T14:30:00",
            },
            {
                "note_id": "blocker-001",
                "title": "GPU 显存不足",
                "body": "单卡 RTX 3090 (24GB) 在 batch_size=32 时 OOM。需要实现 gradient accumulation 或降低 batch_size 到 16 并使用更大的 accumulation steps。",
                "category": "blocker",
                "created_at": "2026-10-22T10:15:00",
            },
            {
                "note_id": "decision-001",
                "title": "选择 PyTorch 实现",
                "body": "决定使用 PyTorch 而非 TensorFlow 进行复现，因为团队更熟悉 PyTorch 且社区资源更丰富。使用 torch.nn.Transformer 作为基线对比。",
                "category": "decision",
                "created_at": "2026-10-20T11:00:00",
            },
            {
                "note_id": "todo-001",
                "title": "实现 Multi-Head Attention",
                "body": "需要手写 MHA 模块以理解内部机制，不能完全依赖 torch.nn.Transformer。重点验证 QKV 投影和缩放点积注意力的数值稳定性。",
                "category": "todo",
                "created_at": "2026-10-23T08:45:00",
            },
            {
                "note_id": "excerpt-001",
                "title": "位置编码公式",
                "body": "PE(pos, 2i) = sin(pos / 10000^(2i/d_model))，PE(pos, 2i+1) = cos(pos / 10000^(2i/d_model))。论文 Section 3.5 解释了选择正弦函数的原因：允许模型学习关注相对位置。",
                "category": "reference",
                "url": "https://arxiv.org/abs/1706.03762",
                "created_at": "2026-10-20T15:20:00",
            },
        ],
    },
    {
        "project_id": "weekly-review",
        "title": "每周业务复盘",
        "summary": "记录每周团队工作进展、阻塞问题和下周计划",
        "notes": [
            {
                "note_id": "w42-progress",
                "title": "第 42 周完成事项",
                "body": "1. 用户反馈系统 v2 上线，日均处理 500+ 反馈。2. 数据库查询优化，P99 延迟从 800ms 降至 200ms。3. 新人培训材料更新完毕。",
                "category": "progress",
                "created_at": "2026-10-18T17:00:00",
            },
            {
                "note_id": "w42-blocker",
                "title": "第三方支付接口不稳定",
                "body": "支付宝回调偶发超时，影响约 2% 的订单。已联系支付宝技术支持，等待排查结果。临时方案：增加重试机制和告警。",
                "category": "blocker",
                "created_at": "2026-10-18T16:30:00",
            },
            {
                "note_id": "w42-decision",
                "title": "Q4 技术栈选型决定",
                "body": "经过评估，决定新项目采用 Go + PostgreSQL 替代原有的 Python + MySQL 方案。主要原因：Go 的并发模型更适合高并发场景，PostgreSQL 的 JSON 支持更灵活。",
                "category": "decision",
                "created_at": "2026-10-17T14:00:00",
            },
            {
                "note_id": "w43-todo",
                "title": "第 43 周计划",
                "body": "1. 完成支付模块重构。2. 启动用户画像系统需求分析。3. 准备技术分享：分布式事务方案对比。4. 代码审查：订单模块重构 PR。",
                "category": "todo",
                "created_at": "2026-10-21T09:00:00",
            },
            {
                "note_id": "w42-ref",
                "title": "性能优化参考资料",
                "body": "MySQL 索引优化最佳实践：覆盖索引、联合索引最左前缀原则、避免索引失效的写法。PostgreSQL 方面：EXPLAIN ANALYZE 解读、并行查询配置。",
                "category": "reference",
                "url": "https://dev.mysql.com/doc/refman/8.0/en/optimization.html",
                "created_at": "2026-10-19T11:20:00",
            },
        ],
    },
]

__all__ = ["DEMO_PROJECTS"]
