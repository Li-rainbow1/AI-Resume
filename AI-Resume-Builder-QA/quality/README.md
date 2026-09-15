> 当前确定性评分已切换为 evidence-v3：17 道按精确证据计分，3 道无答案题为 N/A。具体定义见 ../testdata/quality/README.md。下文旧版本的文档归属评分及无答案门禁描述仅供历史参考；本次不运行 DeepEval，不使用来源拼接证明生成质量。

# AI/RAG 质量评测

## 当前状态

- 脚本已实现：20 条 Golden Dataset（1 篇带图主文档 + 3 篇干扰文档）、三项确定性指标、真实 RAG 执行器、DeepEval 四项指标、Bad Case 分类、JSONL/CSV/JSON 报告和 Allure 摘要。
- 单元验证已完成：数据集结构和确定性指标已有 pytest 覆盖。
- 小规模真实模型基线尚未执行：当前隔离 Compose 的 Chat、Embedding、Vision 均指向 Mock AI；执行器会在上传前安全跳过。
- 正式质量门禁尚未建立：需要固定真实模型、固定 Judge、完成多轮基线后再确定阈值。

Mock AI 仅服务接口契约、异常和性能测试，不生成 AI/RAG 质量结论。

## 实际接口契约

查询接口为 `POST /api/ai/rag/query`，请求字段为 `query`、`topK`，响应顶层字段为 `answer`、`sources`。每个 source 包含 `sourceId`、`content`、`metadata`，评测使用以下 metadata 字段：

| 评测含义 | 实际字段 |
| --- | --- |
| 文档 ID | `documentId` |
| 原文件名 | `originalFilename` |
| Chunk 类型 | `sourceType`、`ingestSource` |
| 页码 | `pageNumber` |
| 段落位置 | `paragraphIndex` |
| 图片位置 | `imageSourceLocator` 或 `relativePath` |
| 相似度 | `similarity` |

查询响应当前没有独立 `imageIndex` 字段。第一版数据集使用 `imageLocator` 表达预期位置，并匹配现有 `imageSourceLocator` 或 `relativePath`；如果后续必须按图片序号评测，需要先补充产品契约，本仓库不会直接修改业务代码。

## Golden Dataset

版本控制文件为 `testdata/quality/golden_dataset.jsonl`，当前版本为 20 条合成固定样例：正文 6 条、图片 OCR 4 条、表格或流程图 4 条、正文图片混合 3 条、无答案 3 条。保留原 15 条并补充版本区分、边界条件、表格比较、流程顺序和三来源汇总。`top_k` 统一取 5，与生产默认 `DEFAULT_RAG_TOP_K=5` 对齐。规模用于首轮小样本基线，不代表生产问题分布。

素材与逐题依据见 [`testdata/quality/README.md`](../testdata/quality/README.md)。语料为 1 篇带图主文档 `quality-corpus.md` 加 3 篇纯文本干扰文档（`noise-alpha.md`、`noise-beta.md`、`legacy-archive.md`），四篇共 21 个正文 Chunk；运行时资产由 `quality/assets.py` 读取它们与已落盘的三个 PNG 副本，图片像素保持一致，仅更新本轮标识，文档文件名带 `QA_RUN_ID` 和 UUID。主文档不包含图片答案或标准答案，内容只含虚构编号、流程和数值。干扰文档用于让检索侧指标具备区分度，详见该 README 的「干扰语料」一节。

每条数据必须包含 `case_id`、`question`、`reference_answer`、`expected_document`、`expected_source_location`、`expected_facts`、`forbidden_facts`、`question_type`、`top_k`。加载器会检查字段、类型、唯一性和总条数。

## 确定性指标（三项，全部在检索侧）

| 指标 | 输入 | 公式与方向 | 单条失败原因 | 汇总 |
| --- | --- | --- | --- | --- |
| Recall@K | 预期位置、TopK sources | 命中的预期位置数 / 预期位置数，越高越好；无答案题不适用 | TopK 未完整命中预期位置 | 仅对适用题汇总，并记录评测条数 |
| Precision@K | 预期文档、TopK sources | 命中预期文档的 source 数 / `top_k`，越高越好；无答案题不适用 | TopK 内没有任何一条属于预期文档 | 仅对适用题汇总，并记录评测条数 |
| MRR | 预期位置、TopK sources | 首个命中预期位置的来源排名倒数（rank 1 → 1.0、rank 3 → 0.33）；无答案题不适用 | 不参与 Bad Case 分类（见下） | 逐条分数的算术平均，即 MRR |

HTTP 200 只表示请求成功。**三项确定性指标只消费 `sources` 与数据集标注，不读取模型回答**——回答质量完全由 DeepEval 的四项指标承担。不适用的指标写为 `null`，汇总不会把它们按 0 计入，并在 `summary.json` 的 `aggregate_evaluated_count` 记录实际评测条数。

### Precision@K 的三处口径说明

- **分母固定为 `top_k`**，不是实际返回条数。检索返回不足 K 条时如实扣分，不掩盖召回不足——语料 Chunk 总数小于 `top_k` 的题会因此天然拿不到满分。
- **判据只比文档归属、不看位置**：调用 `source_matches` 时不传 `location`，所以「来源位置错误」不会拉低 Precision@K。它与 `Recall@K` 的分工是「找对文档」与「找对位置」。
- **门禁是 `> 0`，不是 `== 1`**：等价于「TopK 内至少命中一条预期文档来源」，也就是 Hit Rate@k 的 0/1 判定。不要求满分，是因为上一条分母规则会让「库内 Chunk 不够」的题天然达不到 1.00，那不属于检索质量问题。
- 单文档语料下所有 Chunk 共享同一 `originalFilename`，该指标退化为恒值（召回非空时恒为 `返回条数 / top_k`）。现在语料含 3 篇干扰文档、共 21 个正文 Chunk，而 `top_k=4`——干扰来源进入 TopK 会直接拉低该指标，区分度由此成立。图片题的干扰来源不参与竞争，所以**该指标要按题型看**。

### MRR 的两处口径说明

- **逐条值是 RR，不是 MRR。** `case-results.jsonl` / `case-summary.csv` 里每条记录存的是该题的排名倒数（1/rank），因为 MRR 的定义是「对所有查询求平均」，只有跨题才有意义。`summary.json` 的 `aggregate.mrr` 才是 MRR（对 17 道有答案题的 RR 取算术平均，无答案题记 `None` 不计入）。`aggregate_evaluated_count.mrr` 会显示实际参与计算的条数。
- **MRR 不产生 Bad Case 分类。** `classify_bad_case` 返回的 reason 会直接进入 `failure_reasons` 并让该题判失败，而「正确来源排在第 2、3 位」不等于检索失败，因此 MRR 只在报告里呈现，不新增分类。它的用途是**趋势指标**：优化排序（例如加 Rerank）后 MRR 会抬升，而同期的 Recall@K 可能完全不动——这正是 Recall@K 单独无法刻画的部分。
- 相关性判据与 Recall@K 完全同源（命中任一 `expected_source_location` 即相关，复用 `source_matches`），因此 MRR **不需要任何新增标注**。
- 门禁规则与 Precision@K 一致（`> 0`）。注意它被 `recall_at_k == 1` 隐含：能完整命中位置就一定存在命中来源。所以加 MRR 不会收紧原有失败门槛。

### 已删除的五项指标

早期版本的七项指标里已有四项被整体移除（函数、门禁条件、Bad Case 分类一并清理）：

- **禁止事实命中率**：词面匹配的辅助检查，单独统计只增加一份噪声；
- **无答案拒答率**：靠 `REFUSAL_MARKERS` 字面表判定，表宽了漏放编造、表窄了误判合理拒答；
- **重复运行稳定性**：该指标比对的是 documentId 集合，单文档语料下恒定得分，没有区分度；同时它要求每题重复查询，是唯一让评测成本翻倍的指标，删除后 `QA_QUALITY_REPEAT_COUNT` 与重复查询循环一并移除；
- **关键事实覆盖率**：词面对比回答与 `expected_facts`，只能粗筛、处理不了同义与否定，且与 DeepEval 的生成侧指标语义重叠。删除后确定性层不再读取模型回答。

第二批删除的 `image_knowledge_hit` 理由与上述四项不同——它不是噪声指标，而是被 `Recall@K` 完全覆盖的冗余门禁：

- **图片知识命中率（`image_knowledge_hit`）**：要求 TopK 内存在 `ingestSource == 'image_vision'` 且命中 `expected_source_location` 的来源。数据集里图表题的位置标注只有 `imageLocator` 一种，而只有图片解析分片带 `imageSourceLocator`/`relativePath`、正文分片没有这个字段（`document_chunking_service.py` 的正文 metadata 不含图片定位字段），于是 `Recall@K` 在 `image_ocr`/`table_or_flow` 题上判定的就是同一件事；在 `mixed` 题上它反而更松（任一图片位置命中即得 1.0，而 `Recall@K` 是位置命中数占比），因此 `image_knowledge_hit == 0` 必然伴随 `recall_at_k < 1`，删掉不会漏判任何失败。代价是报告少一列便于定位图片链路故障的指标、Bad Case 少一个「图片Chunk未命中」标签——图片链路故障改由「来源位置错误」体现。

**因此当前的确定性层只在检索侧**，两个已知后果需要明说：

1. **无答案题没有确定性门禁。** 三项指标对 `question_type=no_answer` 全部返回 `null`，`deterministic_passed` 恒为真——合理拒答和编造答案在这一层不可区分，只能靠 DeepEval 的 `Faithfulness` 拦截，所以无答案题只在 `QA_RUN_DEEPEVAL=1` 的那一轮才有约束。这个缺口是刻意接受的，`test_no_answer_cases_have_no_deterministic_gate` 把它钉住，补门禁时需同步更新本文件。
2. **确定性基线（`test_real_rag_deterministic_baseline`）是检索回归门禁，不是回答正确性证据。** 它只保证「来源找对没有」，适合作为 Chunking、Embedding、Rerank 等检索改动的回归门禁；报告里不能把它写成回答质量结论。

`golden_dataset.jsonl` 的 `expected_facts`、`forbidden_facts` 标注保留：`expected_facts` 仍被 Bad Case 分类「OCR内容缺失」用于检查图片类检索内容是否完整，`forbidden_facts` 仅作人工复核依据，两者当前都不参与打分。

## DeepEval

接入 `Faithfulness`、`Answer Relevancy`、`Contextual Recall`、`Contextual Relevancy`。四项按「缺 / 杂 / 编 / 偏」四个正交维度选取：

- `Contextual Recall`：该找的关键信息有没有**找全**；
- `Contextual Relevancy`：检索上下文里**噪声**多不多；
- `Faithfulness`：回答有没有**超出**检索上下文；
- `Answer Relevancy`：回答有没有**答非所问**。

DeepEval 检索侧另有 `Contextual Precision`（相关 Chunk 是否排在前面），本仓库**未启用**：排序维度已由确定性侧的 MRR 覆盖，且它依赖 `expected_output`，扩展性不如 `Contextual Relevancy`。若将来引入 Rerank，应把它加回来，届时为五项。

字段映射如下：

- `input`：问题；
- `actual_output`：RAG 实际回答；
- `expected_output`：参考答案；
- `retrieval_context`：sources 中的 `content`。

固定 Judge 只从当前进程的 `DEEPEVAL_JUDGE_MODEL`、`DEEPEVAL_JUDGE_BASE_URL`、`DEEPEVAL_JUDGE_API_KEY` 读取。可选的 `DEEPEVAL_JUDGE_THRESHOLD` 控制阈值，`DEEPEVAL_JUDGE_REPEAT_COUNT` 控制同项重复评分；重复评分极差超过 0.2 会归类为“Judge评分波动”。这些变量不写入 `.env.test.example`、日志或报告。

Judge 必须与待测 Chat 模型**异构**：同源同模型等于自评，分数会系统性偏乐观。写入由 `scripts/refresh_real_model_env.py --judge-model <模型名> --write` 完成，它会连同 `PERF_OPENAI_*` 一起刷新 `QA_RUN_DEEPEVAL=1`，并顺带用一次最小对话请求探测 Judge 端点（可达、密钥有效、模型名存在、正文非空）。实测组合：待测 `deepseek-flash` + Judge `qwen3.8-max`（同走 DashScope 兼容模式，复用业务 embedding 服务的端点与密钥，因为同一个账号密钥既能调 embedding 也能调对话模型）。

未登记进 DeepEval 内置模型表的模型（Qwen 系即是）会走「普通对话 + 本地 JSON 解析」这条最兼容的路径——不向端点传 `response_format`，拿回正文后由 `trim_and_load_json` + pydantic 本地校验。因此**不要求端点支持 `response_format`**，代价是报告里的成本字段为 `None`（未知模型无价目表），不影响分数与判定。

实现依据：[DeepEval 指标说明](https://deepeval.com/docs/metrics-introduction)、[Contextual Relevancy](https://deepeval.com/docs/metrics-contextual-relevancy)、[自定义 OpenAI-compatible 模型](https://deepeval.com/integrations/models/openai)。本项目锁定并按实际安装的 `deepeval==4.1.4` 接口适配。

## 执行

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,eval]"
.\.venv\Scripts\python.exe -m pytest tests\quality\test_deterministic_metrics.py
.\.venv\Scripts\python.exe scripts\inspect_quality_corpus_chunks.py
.\.venv\Scripts\python.exe -m pytest tests\quality\test_real_rag_quality.py -m quality_eval --alluredir=reports\allure-results-quality
```

`scripts/inspect_quality_corpus_chunks.py` 离线调用业务仓库的分片代码（`LogicalDocumentSplitterService` + `DocumentChunkingService`），打印每篇语料的 Chunk 数与数据集 `top_k` 分布。它只读代码和语料、不连接服务，用来确认干扰文档确实构成 TopK 竞争、以及 `top_k` 相对语料规模是否合理。**修改语料或分片参数后必须重跑**——`top_k` 停用旧值就是因为语料规模变了而它没有跟着变。

真实模型端点与 Judge 凭据不手工维护：`scripts/refresh_real_model_env.py` 读业务仓库 `.env` 的根密钥解密业务 MySQL 的 `system_service_configs`，把 chat / embedding / vision 写给 `PERF_OPENAI_*`，并按需写入 `DEEPEVAL_JUDGE_*`。省略 `--judge-model` 时完全不碰 Judge 配置，`--write` 才落盘并自动备份（默认只预览、密钥只回显长度与尾 4 位）。写入 `PERF_*` 后要重建 `backend` 与 `image-worker`；`DEEPEVAL_*` 只在 pytest 进程内读取，不需要重建容器。

真实基线还要求显式启用 `QA_RUN_RAG_QUALITY`、`QA_ALLOW_QUALITY_WRITES`、`QA_QUALITY_REAL_MODELS_CONFIRMED`、`QA_RUN_RAG_INTEGRATION`、`QA_ALLOW_RAG_WRITES`。DeepEval 另需启用 `QA_RUN_DEEPEVAL` 并在当前进程提供三项 Judge 环境变量。非 localhost 目标还需显式启用 `QA_ALLOW_REMOTE_QUALITY`。

逐条报告写入 `reports/quality/<QA_RUN_ID>/deterministic/` 或 `deepeval/`，包含 `case-results.jsonl`、`case-summary.csv`、`summary.json`。报告只保存脱敏合成语料、非敏感配置摘要和异常类型。

上传前会登记动态文件名，清理 Fixture 只删除文件名完整匹配当前 `QA_RUN_ID` 且已登记的文档。测试失败和 pytest 中断退出仍会执行清理；本地生成资产由 pytest 临时目录回收，不会删除用户文件。
