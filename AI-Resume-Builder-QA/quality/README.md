> 当前确定性评分已切换为 evidence-v4：计分单位统一为「答案单元」，数据集按 `schema_version` 分发加载，判分器可插拔（正则 / 语义）。旧 `evidence-v3` 数据集（`golden_dataset.jsonl`）已不在本机，但对应加载路径与用例仍在。具体定义见 [数据集 schema 与加载分发](#数据集-schema-与加载分发evidence-v4)。下文关于「三项指标」「单文档 `expected_document`」的描述已在本次改造中更新；标注为历史参考的段落不再代表当前实现。

# AI/RAG 质量评测

## 当前状态

- 脚本已实现：schema 分发加载器、**schema 分发的语料编排**、三项确定性指标、真实 RAG 执行器、DeepEval 四项指标、Bad Case 分类、JSONL/CSV/JSON 报告（含题型/主题/模态分组）和 Allure 摘要。
- 单元验证已完成：数据集结构（两代 schema）、语料编排契约、确定性指标、判分器（正则 / 语义）、判分通道配方、DeepEval 适配层（假 Judge 驱动，不联网）、冻结清单（形状 / 门禁 / 生成器往返）都有 pytest 覆盖，全套离线可跑，不调用任何模型。
- 判分通道已统一：配置与请求配方只有 `quality/judge.py` 一份，三条链路（确定性层语义判分器、DeepEval 四项指标、面试链路）共用，见[判分链路已统一](#判分链路已统一一套-judge一份配方)。
- 冻结清单已统一：生成方（`scripts/freeze_quality_dataset.py`）与校验方（两条 runner）共用 `quality/freeze.py` 一套实现，见[冻结清单](#冻结清单正式评测的门禁)。
- 两个数据集：`testdata/quality/interview-notes-v1/`（50 正式 + 10 调试，多文档 + 语义判分，**加载、编排、判分均已接线**）；`golden_dataset.jsonl`（旧集，**本机已无此文件，且从未入库**，加载路径与编排路径保留、可跑性由同构测试保证）。
- 小规模真实模型基线尚未执行：当前隔离 Compose 的 Chat、Embedding、Vision 均指向 Mock AI；执行器会在上传前安全跳过。
- 正式质量门禁尚未建立：需要固定真实模型、固定 Judge、完成多轮基线后再确定阈值。`interview-notes-v1` 目前**还没有冻结清单**，正式跑之前必须先执行一次冻结。

Mock AI 仅服务接口契约、异常和性能测试，不生成 AI/RAG 质量结论。

> **执行器（`runner.py`）的语料编排已按 `schema_version` 分发**，两代 schema 都有实现：`evidence-v3` 走 `quality/assets.py`，`interview-notes-v1` 走 `quality/corpus.py` 的 `build_notes_corpus`（同一批上传 5 份正文 + 64 张附件，再逐篇轮询图片解析）。未注册的 `schema_version` 会在**上传前**直接报错，不会错传语料。详见[语料编排分发](#语料编排分发schema--上传语料)。

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

每条数据必须包含 `case_id`、`question`、`reference_answer`、`expected_source_location`、`expected_facts`、`forbidden_facts`、`question_type`、`top_k`，以及旧结构特有的 `expected_document`。加载器会检查字段、类型、唯一性和总条数。**该数据集文件已不在本机**（从未入库，无可恢复副本）；加载路径与契约由 `tests/quality/test_dataset_loaders.py::test_legacy_dataset_still_loads_and_scores` 用现场构造的同构数据集继续保证可跑。

新数据集不再使用这一结构。加载入口是 `quality/loaders.py::load_case_set`，按题目声明的 `schema_version` 分发，见下节。

## 数据集 schema 与加载分发（evidence-v4）

评分只依赖两件事：题目声明的**答案单元**与运行期实际返回的**来源**。数据集之间的差别只有一处——答案单元怎么落到来源上。因此 schema 差异全部下沉到加载器，指标、匹配器、报告都不认识数据集字段名。

| 关注点 | 位置 | 说明 |
| --- | --- | --- |
| 统一题目模型 | `quality/models.py` | `EvalCase` / `AnswerUnit` / `SourceSelector` / `CaseSet`。`GoldenCase` 已删除 |
| 加载分发 | `quality/loaders.py` | `load_case_set()` 读首题 `schema_version` 选加载器；新增数据集 = 新增加载函数 + 一行 `@register_loader` |
| 来源适配 | `quality/sources.py` | 把 `originalFilename` / `ingestSource` / `imageSourceLocator` / `relativePath` 收敛成 `SourceView`，判「是不是图片来源」只在这一处 |
| 判分器 | `quality/matchers.py` | `EvidenceMatcher` 协议；`PatternMatcher`（正则、离线）与 `SemanticMatcher`（语义、LLM） |
| 指标 | `quality/metrics.py` | `evaluate_case(case, answer, sources, matcher)`，三项指标，与 schema 无关 |

已注册的 schema：

| `schema_version` | 出处 | 多文档 | 判分方式 | 答案单元来源 | DeepEval 指标 |
| --- | --- | --- | --- | --- | --- |
| `evidence-v3`（无该字段时回落） | `testdata/quality/golden_dataset.jsonl`（本机已无） | 否 | `match_patterns` 正则 | 每个正则组 = 一个必须命中的子项 | 检索侧两项 |
| `interview-notes-v1` | `testdata/quality/interview-notes-v1/` | 是 | 语义（`claim` + `acceptance`） | `acceptable_evidence_ids` 的或关系 | 检索侧两项 |

三条硬约束：

1. **语义单元必须有判分通道。** 单元没有正则时不允许退回关键词匹配——那正是旧集已证明会漏判的做法。缺配置时 `matcher_for_cases` 直接报出缺失的环境变量名。
2. **语料哈希在加载期校验。** 路径不得越界、文件必须存在、SHA-256 必须与标注一致；任一条不满足就拒绝加载，不带着错标注去评分。
3. **无答案题不得声明答案单元或预期文档。** 反之有答案题必须两者都有，且单元引用的证据所属文档必须在预期文档内（否则等于把别的文档偷偷算成正确答案）。

## 语料编排分发（schema → 上传语料）

加载器决定「这道题该命中什么」，编排决定「这一轮往被测系统里传什么」。两者都按 `schema_version` 分发，但职责不同、代码分开，避免「换数据集」这件事同时动到评分口径。

| 关注点 | 位置 | 说明 |
| --- | --- | --- |
| 编排分发 | `quality/corpus.py` | `corpus_for(case_set, temp_root, run_id)` 按 `case_set.schema_version` 选工厂；新增数据集 = 新增工厂函数 + 一行 `register_corpus_factory` |
| 共享语料模型 | `quality/models.py` | `QualityCorpus`：`primary_assets` / `noise_assets` / `document_file_names` / `noise_file_names` / `expected_unreferenced_attachments` |
| 两代实现 | `quality/assets.py`、`quality/corpus.py` | `evidence-v3` → `build_legacy_corpus`（原文 + 干扰文档 + 3 张图）；`interview-notes-v1` → `build_notes_corpus`（5 篇笔记 + 64 张附件） |

`interview-notes-v1` 的编排有三条硬约束，都由 `tests/quality/test_corpus_orchestration.py` 钉住：

1. **正文与附件必须同一批上传，且附件的 `relativePath` 等于正文里的 Markdown 相对路径**（`附件/<原名>`，不是数据集相对路径 `corpus/附件/<原名>`）。后端靠上传 manifest 的 `relativePath` 把附件挂到正文；路径写错不报错，只会让图片静默挂不上、图片题永久无解。
2. **正文与数据集声明的 `sha256` 逐字节一致**（落盘时仅文件名加 run 前缀，并逐文件核对）。哈希是证据行号可信的前提。唯一一次有意的改写（`6-计算机网络.md` 第 74 行的 Obsidian `![[...]]` → 常规 Markdown 链接，且文件名里的空格按 URL 编码写成 `%20`）发生在**冻结生成时**，已记入 `corpus_manifest.json` 的 `transformations`（含 before/after 哈希）；行数不变，所以证据行号仍成立。运行期不许再改正文。
3. **每个附件都要有归属**：要么被正文引用，要么被 `expected_unreferenced_attachments` 点名为已知的未引用项。逐篇轮询完的 `referenced_total` 之和与附件总数之差必须**恰好等于**声明的例外数，不等就中止本轮。

`interview-notes-v1` 声明的例外是**空集**：原先 `附件/Pasted image 20260818090016.png` 只被 Obsidian 写法 `![[...]]` 引用，而后端 `markdown_attachment_service` 只认 `![](path)` / `![][label]` / `![label]` 三种写法，解析不到。核对过全部 8 个图片证据单元、确认没有任何一题依赖它之后，改为在冻结生成时把这一处链接归一化（而不是留一张挂不上正文的无归属附件），于是 64 张附件全部有正文归属。声明位保留：将来真出现挂不上的附件必须点名，点名项若从素材里消失则报错。

⚠️ **这条链接踩过两次坑，写法别再改回去。** 图片文件名本身含空格（`Pasted image ….png`），而后端 `_IMAGE_PATTERN` 用 `[^\s)\r\n]+` 捕获路径——`](` 之后**遇空格即断**，所以归一化成 `![](附件/Pasted image 20260818090016.png)`（不编码）会被当成坏语法静默丢弃：正文里看着有图，后端一条引用都抽不到。初版就是这么写的，检索评测在「未引用附件数 1 ≠ 声明 0」门禁处中止（`/api/ai/rag/query` 调用 0 次）。必须让路径里**没有裸空格**：写成 `%20`（后端 `normalize_markdown_attachment_path` 会 `unquote` 解回，与上传 `relativePath` 逐字节相等），或用尖括号 `![](<附件/Pasted image ….png>)`。改完用业务仓库的 `extract_markdown_image_paths` 离线复核一遍：5 篇正文合计应 matched=64 / missing=0 / 未归属=0。

> **语料只在本地保留，不入库。** `testdata/quality/` 由 `.gitignore` 排除——资料是第三方笔记正文与插图，按数据集自己的说明（`资料问题与接入说明.md`「入库前处理」）只在本地制作，不提交、不推送、不发布。因此依赖真实语料的 3 条契约用例带 skip 守卫：语料不在时**跳过**，而不是把「这台机器没有本地语料」误报成实现坏了。其余用例现场构造数据集与语料，任何机器都能跑。

### 两层评分：确定性层 vs DeepEval（别混）

这里有**两套互相独立**的评分，都保留、都要跑，不能互相替代：

| | 确定性层（`metrics.py`） | DeepEval 层（`deepeval_adapter.py`） |
| --- | --- | --- |
| 判什么 | 检索回来的来源**有没有覆盖住每个答案单元** | 回答的**缺 / 杂 / 编 / 偏** |
| 计分单位 | 数据集标的 `answer_units` / 片段（`acceptable_evidence_ids` 的或关系） | DeepEval 内部的句子级判断 |
| 读不读模型回答 | **完全不读** | 读（`actual_output`） |
| 输出 | 4 个比例值（Recall@K 等） | 4 个 0–1 分数 + 理由 |
| 判分方式 | 单元自带正则（旧集）或**语义判分器**（新集） | LLM Judge，固定 4 个内置指标 |

关键点：DeepEval 的 `Contextual Recall` **不能替代** `Recall@4`。它的分子分母是「参考答案的句子有没有被上下文支持」，而 `Recall@4` 的分母是数据集标注的 `answer_units`——单位不同、依据不同，两者数值不可互换也不可合并。`interview-notes-v1` 的 README 自己就把这两组并列写了两节，所以这里不是二选一。

**为什么确定性层需要「语义判分」**：旧集的单元自带 `match_patterns` 正则，命中与否是纯字符串判断，完全离线。新集 **0 / 103 个单元带正则**，只在 `acceptance` 里写明「语义等价即可；必须保留主体、条件、动作及否定关系，孤立关键词不算覆盖」，并且明确要求「暂不编造固定正则来模拟语义判分」。既然没有正则，就必须有别的机制回答「这条片段算不算覆盖了这个事实」——那个机制就是语义判分器，它服务于确定性层，**和 DeepEval 无关**。DeepEval 不会读 `acceptance` 字段，也无法按 `answer_units` 计分。

### 确定性层的语义判分器（不是 DeepEval）

`SemanticMatcher` 一次请求判完「一条片段 × 该片段覆盖范围内的全部单元」，只回 `supported` 布尔值与一句理由，**不打分、不做句子级拆分**，所以比跑一个 DeepEval 指标便宜得多。判分结果按 `(unit_id, 片段内容)` 缓存，重评同一批片段不再调用模型。漏报单元或结构不合法会重试（默认 3 次，`*_MAX_ATTEMPTS` 可调），最终仍不合法则抛错——**不能把「没判到」当成「不支持」**。

判分通道读环境变量，优先 `QUALITY_JUDGE_*`，缺失时回落 `DEEPEVAL_JUDGE_*`（`MODEL` / `BASE_URL` / `API_KEY`，可选 `TIMEOUT_SECONDS`、`MAX_ATTEMPTS`）。配置与请求配方见 `quality/judge.py`（[判分链路已统一](#判分链路已统一一套-judge一份配方)），报告里只记录去掉凭证的地址、模型名与生效前缀。

⚠️ **默认建议不要设 `QUALITY_JUDGE_*`**，让它沿用 DeepEval 同一个 Judge（本项目记为待测 `deepseek-flash` + Judge `qwen3.8-max`，见「DeepEval」节）。理由：① 两个判分器口径一致，出错时只需复核一家；② 保持与待测 Chat 模型**异构**。若把 `QUALITY_JUDGE_*` 指到 `deepseek-flash`，就等于让待测模型自评检索命中，与「Judge 必须异构」的既有结论冲突。只在明确要用更便宜的模型降成本、且接受口径不一致时才这么做。

想换 embedding 相似度判分：新增一个 `EvidenceMatcher` 子类（实现 `match_many`）并在 `matcher_for_cases` 里选一次即可，指标与报告都不用改。**不建议**用它替代当前实现：数据集明确要求保留否定关系，向量相似度对「把否定说成肯定」不敏感。

## 确定性指标（三项，全部在检索侧）

| 指标 | 输入 | 公式与方向 | 单条失败原因 | 汇总 |
| --- | --- | --- | --- | --- |
| Recall@K | 答案单元、TopK sources | 被覆盖的答案单元数 / 单元总数，越高越好；一个单元可由多条片段合起来支持；无答案题不适用 | 单元未在 TopK 内被完整覆盖 | 仅对适用题汇总，并记录评测条数 |
| Precision@Returned | 同上 | 相关非重复片段数 / **实际返回**片段数；无返回记 `null` | 不参与 Bad Case 分类（见下） | 逐条算术平均，记录评测条数 |
| MRR | 答案单元、TopK sources | 首个相关来源排名倒数（rank 1 → 1.0、rank 3 → 0.33）；无答案题不适用 | 不参与 Bad Case 分类（见下） | 逐条分数的算术平均，即 MRR |

HTTP 200 只表示请求成功。**三项确定性指标只消费 `sources` 与数据集标注，不读取模型回答**。注意本集对应的接口**不生成回答**（返回的 `answer` 是检索片段拼成的上下文摘要），所以这一层不存在「回答质量」这回事：检索侧确定性三项 + DeepEval 的检索侧两项就是全部口径，生成侧两项归面试链路。不适用的指标写为 `null`，汇总不会把它们按 0 计入，并在 `summary.json` 的 `aggregate_evaluated_count` 记录实际评测条数。

### 相关性判据的口径说明

- **判据是「片段是否支持了所问事实」，不是「片段属于哪篇文档」。**（evidence-v4 修正）旧文档写的是「只比文档归属、不看位置」，但旧实现一直是位置感知的——单元必须同时命中文件归属与位置/图片定位才算相关。现在把口径统一到实现与新数据集说明：支持了单元才算相关。口径变化只影响图片/位置错的片段（它们不再被计成「相关」），并且旧数据集文件已不在本机，不存在需要重算的历史报告。
- 同一判据供 `Precision@Returned` 与 `MRR` 共用：前者统计相关片段占**实际返回**条数的比例，后者取首个相关片段的排名倒数。

### MRR 的两处口径说明

- **逐条值是 RR，不是 MRR。** `case-results.jsonl` / `case-summary.csv` 里每条记录存的是该题的排名倒数（1/rank），因为 MRR 的定义是「对所有查询求平均」，只有跨题才有意义。`summary.json` 的 `aggregate.mrr` 才是 MRR（对 17 道有答案题的 RR 取算术平均，无答案题记 `None` 不计入）。`aggregate_evaluated_count.mrr` 会显示实际参与计算的条数。
- **MRR 不产生 Bad Case 分类。** `classify_bad_case` 返回的 reason 会直接进入 `failure_reasons` 并让该题判失败，而「正确来源排在第 2、3 位」不等于检索失败，因此 MRR 只在报告里呈现，不新增分类。它的用途是**趋势指标**：优化排序（例如加 Rerank）后 MRR 会抬升，而同期的 Recall@K 可能完全不动——这正是 Recall@K 单独无法刻画的部分。
- 相关性判据与 Recall@K 完全同源（覆盖了任一答案单元的片段即相关，单元自身的文档归属与图片定位都由 `selector_matches` 判定），因此 MRR **不需要任何新增标注**。
- 门禁只取 `MRR > 0`，且它被 `recall_at_k == 1` 隐含：能完整命中单元就一定存在命中来源。所以加 MRR 不会收紧原有失败门槛。

### 已删除的指标

早期版本的七项指标里已有四项被整体移除（函数、门禁条件、Bad Case 分类一并清理）：

- **禁止事实命中率**：词面匹配的辅助检查，单独统计只增加一份噪声；
- **无答案拒答率**：靠 `REFUSAL_MARKERS` 字面表判定，表宽了漏放编造、表窄了误判合理拒答；
- **重复运行稳定性**：该指标比对的是 documentId 集合，单文档语料下恒定得分，没有区分度；同时它要求每题重复查询，是唯一让评测成本翻倍的指标，删除后 `QA_QUALITY_REPEAT_COUNT` 与重复查询循环一并移除；
- **关键事实覆盖率**：词面对比回答与 `expected_facts`，只能粗筛、处理不了同义与否定，且与 DeepEval 的生成侧指标语义重叠。删除后确定性层不再读取模型回答。

第二批删除的 `image_knowledge_hit` 理由与上述四项不同——它不是噪声指标，而是被 `Recall@K` 完全覆盖的冗余门禁：

- **图片知识命中率（`image_knowledge_hit`）**：要求 TopK 内存在 `ingestSource == 'image_vision'` 且命中 `expected_source_location` 的来源。数据集里图表题的位置标注只有 `imageLocator` 一种，而只有图片解析分片带 `imageSourceLocator`/`relativePath`、正文分片没有这个字段（`document_chunking_service.py` 的正文 metadata 不含图片定位字段），于是 `Recall@K` 在 `image_ocr`/`table_or_flow` 题上判定的就是同一件事；在 `mixed` 题上它反而更松（任一图片位置命中即得 1.0，而 `Recall@K` 是位置命中数占比），因此 `image_knowledge_hit == 0` 必然伴随 `recall_at_k < 1`，删掉不会漏判任何失败。代价是报告少一列便于定位图片链路故障的指标、Bad Case 少一个「图片Chunk未命中」标签——图片链路故障改由「来源位置错误」体现。

第三批删除的 `precision_at_k` 与上面五条都不同——它不是噪声、也不是被别的指标覆盖，而是**同一个问题有两套分母、其中一套没人会用**：

- **它的分母固定为 `top_k`，回答的是「TopK 名额占了多少」，不是「返回得准不准」。** 上游检索会按相似度阈值裁掉弱片段、且**不补足条数**，因此实际返回经常少于 `top_k`：`notes-v1-20260917b` 这轮 45 道有答案题里只有 15 道返回满 4 条，汇总值 `0.35` 主要是被「少返回」拉下来的。同批 `precision_at_returned = 0.65` 才是「返回内容准不准」的读数；两个分母并存，只会让 `0.35` 被误读成检索质量问题。
- **它在门禁里本来就冗余**：判定式里的 `precision_at_k > 0` 与 `mrr > 0` 等价（都只是「至少有一条相关片段」），删掉不改变任何一题的结论（本轮仍是 29 过 / 16 挂）。
- **单文档语料下它还会退化成恒值**：召回非空时恒等于 `返回条数 / top_k`，区分度完全依赖语料里存在干扰文档，与本集「不设人工干扰文档」的设计相抵。

删除后确定性层收紧为 `Recall@K` / `Precision@Returned` / `MRR` 三项，报告里对应的列自动消失（列由指标字典动态生成，不需要改报告代码）。

**因此当前的确定性层只在检索侧**，两个已知后果需要明说：

1. **无答案题没有确定性门禁。** 三项指标对 `question_type=no_answer` 全部返回 `null`，`deterministic_passed` 恒为真——合理拒答与「检索拿回一堆不相关片段」在这一层不可区分。本集链路上没有生成环节，所以不存在「编造答案」可拦，`Faithfulness` 也已不在本集声明里（那是面试链路的口径）；无答案题实际只剩检索侧一条间接约束。这个缺口是刻意接受的，`test_no_answer_cases_have_no_deterministic_gate` 把它钉住，补门禁时需同步更新本文件。
2. **确定性基线（`test_real_rag_deterministic_baseline`）是检索回归门禁，不是回答正确性证据。** 它只保证「来源找对没有」，适合作为 Chunking、Embedding、Rerank 等检索改动的回归门禁；报告里不能把它写成回答质量结论。

`golden_dataset.jsonl` 的 `expected_facts`、`forbidden_facts` 标注保留：`expected_facts` 仅作人工复核依据（「OCR内容缺失」分类已随词面匹配一并移除），`forbidden_facts` 落到 `EvalCase.forbidden_claims`，两者当前都不参与打分。

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

**指标集由题目声明。** `EvalCase.judge_metrics` 决定本题适用哪几项：目前两个数据集都只声明**检索侧两项**——旧集本来就是检索集；`interview-notes-v1` 对应的 `/api/ai/rag/query` 不生成回答（`answer` 是检索片段拼装），生成侧两项在这条链路上测不出信号，它们归面试链路。这里修掉了一处静默缺口——`deepeval_adapter.py` 的 `METRIC_NAMES` 一直列着四项，但 `metric_factories` 只实现了 `Contextual Recall` 与 `Contextual Relevancy`，`Faithfulness` 与 `Answer Relevancy` 从未被测量且没有任何报错。现在四项全部实现，未实现的指标名会直接抛错；`Faithfulness` 沿用面试链路验证过的 `penalize_ambiguous_claims=True`。缺项也不再算通过：`judge_passed` 要求实际测到的指标集合等于题目声明的集合。

Judge 配置只从当前进程读取，唯一入口是 `quality/judge.py::judge_config_from_environment`：前缀 `QUALITY_JUDGE_*` → `DEEPEVAL_JUDGE_*`，取第一组配齐的（`MODEL` / `BASE_URL` / `API_KEY`）。阈值与重复次数是 `<前缀>_THRESHOLD` / `<前缀>_REPEAT_COUNT`；重复评分极差超过 0.2 会归类为“Judge评分波动”。这些变量不写入 `.env.test.example`、日志或报告（报告只留脱敏摘要 + `env_prefix`）。

⚠️ 波动检查只在 `DEEPEVAL_JUDGE_REPEAT_COUNT > 1` 时才有意义：默认值 1 下每项只测一次，`score_spread` 恒为 `0.0`，这条归类永不触发。

判分链路按约定只打本机服务（非 localhost 目标要显式开 `QA_ALLOW_REMOTE_QUALITY`），因此 `deepeval_adapter` 在模块导入时关掉 deepeval 自带的 PostHog 遥测（`DEEPEVAL_TELEMETRY_OPT_OUT=1`）并强制 `DEEPEVAL_DISABLE_DOTENV=1`（防止 deepeval 自带的 `.env` 覆盖本进程的 Judge 配置）。这两项都必须在任何 `import deepeval` 之前落到环境里——`deepeval.config.settings` 首次 `get_settings()` 就把环境读进缓存，之后再改环境变量不生效。

Judge 必须与待测 Chat 模型**异构**：同源同模型等于自评，分数会系统性偏乐观。写入由 `scripts/refresh_real_model_env.py --judge-model <模型名> --write` 完成，它会连同 `PERF_OPENAI_*` 一起刷新 `QA_RUN_DEEPEVAL=1`，并顺带用一次最小对话请求探测 Judge 端点（可达、密钥有效、模型名存在、正文非空）。实测组合：待测 `deepseek-flash` + Judge `qwen3.8-max`（同走 DashScope 兼容模式，复用业务 embedding 服务的端点与密钥，因为同一个账号密钥既能调 embedding 也能调对话模型）。

### 判分链路已统一（一套 Judge，一份配方）

通道配置与请求配方只有一份，在 `quality/judge.py`：

| 位置 | 职责 |
| --- | --- |
| `judge.JudgeConfig` / `judge_config_from_environment` | 唯一的环境读取；记录生效前缀（`env_prefix`），`summary()` 去掉凭证后可直接进报告 |
| `judge.judge_completion` | **唯一的请求出口**。固定 `temperature=0`、`response_format=json_object`、`extra_body=JUDGE_EXTRA_BODY`、SDK `max_retries=0`、`timeout` 取自配置；**传输层重试也只有这一份**（断连/超时/限流/5xx，见下第 4 条） |
| `judge.JUDGE_EXTRA_BODY` | 思考参数，**只有一套**：智谱原生端点的 `{"thinking": {"type": "enabled"}}`（恒开思考）。取值原样进 `summary()["thinking"]` |
| `judge.OpenAICompatibleJudge` | 确定性层的语义判分通道（把正文解析成 dict） |
| `deepeval_judge.DeepEvalJudgeLLM` | 把同一配方接到 DeepEval 的模型接口（把正文校验成 pydantic 对象） |
| `interview_judge.InterviewJudge` | **就是** `DeepEvalJudgeLLM` 的别名，面试链路不再有独立实现 |

于是三条链路（确定性层语义判分器、DeepEval 四项指标、面试八股链路）发出的报文逐字节一致，同一道题的两组分数才真的可比。

统一带来的行为变化，需要知道：

1. **端点必须支持 `response_format={"type": "json_object"}` 与 `extra_body=JUDGE_EXTRA_BODY` 所指定的思考参数。** 原先只有 DeepEval 层走「普通对话 + `trimAndLoadJson` 抽子串」的兼容路径；现在废弃该路径，改由服务端保证 JSON。另两条链路本来就一直这么发，端点已实测通过。判分器固定为智谱原生端点（`open.bigmodel.cn/api/paas/v4`）上的 `glm-5.3-flash`，思考参数写法 `{"thinking": {"type": "enabled"}}`——这套字段名与 dashscope `compatible-mode` 的 `enable_thinking` **不通用**，换端点必须同时改 `JUDGE_EXTRA_BODY`，没有回落分支。
2. **`QUALITY_JUDGE_*` 现在也会驱动 DeepEval 层**（原先该前缀只影响确定性层）。优先级仍是 `QUALITY_JUDGE_*` → `DEEPEVAL_JUDGE_*`，生效前缀记在报告的 `judge.env_prefix` 里。这正是「不要设 `QUALITY_JUDGE_*`，让三条链路共用同一个 Judge」那条建议的强制化。
3. **阈值与重复次数跟着生效前缀走**：`<前缀>_THRESHOLD` / `<前缀>_REPEAT_COUNT`，该前缀没配则回落 `DEEPEVAL_JUDGE_*`。
4. **重试语义（2026-09-17 起分两层，别再按旧的「网络错误直接抛」理解）**：
   - **传输层**：`judge_completion` 自己对断连/超时/限流/5xx 重试，次数用 `max_attempts`（默认 3），退避固定 2s×次数。重试的是**同一条报文**，模型输入没变，所以分数含义不变；`sdk_max_retries` 仍是 0。策略记在报告 `judge.transport_retry_attempts` / `transport_retry_backoff_seconds` 里。
   - **结构层**：`DeepEvalJudgeLLM` / `SemanticMatcher` 只在「返回了但结构不合法」时重试到 `max_attempts`；鉴权、400 这类不重试（同一份报文再发还是错）。
   - **为什么加**：端点偶发断连时，确定性层的证据判分原先没有任何保护，一个 `APIConnectionError` 把已跑 19 分钟的那轮 50 题整体打断（`APIConnectionError` 不是 `ValueError`）。用满重试仍失败时，`runner` 会按与 DeepEval 层相同的口径记一条「Judge 执行失败：<类型>」并继续跑下一题，不再中断整轮。
5. **「判分器没给分」的失败口径两条链路一致**：`deepeval_adapter` 与 `interview_runner` 都抛 `RuntimeError` 并带出 `metric.error`，不再出现「一处有原因、一处只有裸 `ValueError`」。
6. **冻结清单的 `judge` / `interview_judge` 块要按新形状重生成。** `runner.py` 与 `interview_runner.py` 拿运行时摘要与清单做**整体相等**比对；统一后摘要新增了 `temperature` / `response_format` / `thinking` / `sdk_max_retries` / `max_attempts` / `transport_retry_attempts` / `transport_retry_backoff_seconds` / `request_timeout_seconds` / `env_prefix` 九项，按旧形状写好的清单会被判「Judge 配置与冻结版本不一致」。当前仓库内还没有任何 `freeze-manifest.json`（新数据集尚未冻结），因此这是**待触发**项：第一次冻结时照新形状生成即可。
   - ✅ 原先记的「`env_prefix` 与 `deepeval_version` 也在比对范围内、改前缀名或升补丁版都会误报」已修（2026-09-16）：比对改为 `quality/freeze.py::comparable_judge`——`env_prefix` 不比，`deepeval_version` 只比 `major.minor`，其余字段（含以后新增的）仍然逐个比对。见「冻结清单」节的四条约定。

另有一条**指标口径**上的非对称，来自 deepeval 自身实现，不是本项目引入：Judge 返回空 verdict 列表时，`Contextual Recall` / `Contextual Relevancy` 记 **0 分**，而 `Faithfulness` / `Answer Relevancy` 记 **满分**。即同一份异常输出会同时「误杀缺/杂」与「误放编/偏」。空回答本身已被 `measure()` 的 `actual_output` 校验拦成 `MissingTestCaseParamsError`（不会走到这条路径），但「回答非空、claims 抽不出来」仍会白拿满分。

实现依据：[DeepEval 指标说明](https://deepeval.com/docs/metrics-introduction)、[Contextual Relevancy](https://deepeval.com/docs/metrics-contextual-relevancy)、[自定义 OpenAI-compatible 模型](https://deepeval.com/integrations/models/openai)。本项目锁定并按实际安装的 `deepeval==4.1.4` 接口适配。

## 冻结清单（正式评测的门禁）

「冻结」= 跑正式评测之前，把会影响分数的输入钉死成一份字节级哈希清单（`<数据集>/freeze-manifest.json`），运行时逐项比对，任一项对不上就拒绝开跑。目的是让报告里的数字**可归因**：数据集、评分代码、被测模型配置、判分器任意一项在无人察觉时变了，分数差异就没法归因到任何一项。

清单四类内容：

| 字段 | 键的基准 | 覆盖范围 | 比对方式 |
| --- | --- | --- | --- |
| `files_sha256` | 数据集目录 | 数据集内全部文件（排除清单自身、`freeze-history/`、`__pycache__`） | 逐文件字节哈希 |
| `qa_code_sha256` | QA 仓库根 | `quality/`、`clients/`、`fixtures/` 下的 `.py` + `compose.interview-quality.yml` | 逐文件字节哈希 |
| `services` | — | 被测系统的 `chat` / `embedding` / `vision` 的 `config` | 只比清单点名的键 |
| `judge` / `interview_judge` | — | 判分通道模型、地址、请求配方、阈值、重复次数 | **整体相等**；但 `env_prefix` 不比、`deepeval_version` 只比 `major.minor`（见下） |

另有 `revision`（上述全部事实的指纹，不含自身）与 `previous_manifest_sha256` / `previous_manifest_revision`（说明上一版是什么）。

判分配置的比对口径（`quality/freeze.py::comparable_judge`）是**默认收紧**：除下面两处外，任何字段——包括以后新增的——都要完全相等。

- **`env_prefix` 不进比对**。它只说明本地从哪组环境变量读到配置（`QUALITY_JUDGE_*` 还是 `DEEPEVAL_JUDGE_*`），是本地命名；真正决定口径的端点、模型、请求配方本来就逐个比对，前缀换名而取值不变不算口径变化。
- **`deepeval_version` 只比 `major.minor`**。补丁版是修 bug，不该把已经冻好的报告判成「口径变了」；次版本可能改判分提示词，所以不放开。

四条实现约定：

1. **生成方与校验方共用一套「该冻哪些文件、怎么算哈希」**（`quality/freeze.py`）。两边各维护一份的话，漂移方向永远是「校验比生成宽松」，等于门禁静默失效。`tests/quality/test_freeze_manifest.py::test_generator_manifest_satisfies_the_gate` 就是钉住这个往返。
2. **清单自己不参与冻结**，否则每写一次清单就会让上一次的校验失败。
3. **冻结覆盖面必须钉成 LF**（`AI-Resume-Builder-QA/.gitattributes`：`*.py text eol=lf` + `compose.interview-quality.yml`）。门禁算的是 `read_bytes()` 的 sha256，本机 `core.autocrlf=true`；不钉的话「最近一次 `git checkout`」就能让 `qa_code_sha256` 变化而代码一字未改。清单**自身**仍按 **CRLF + 无末尾换行** 写——它是脚本产物、不参与自己的校验。
4. **缺判分口径时不生成清单**。生成器默认在判分环境变量没配全时**直接失败**；只冻确定性轮必须显式加 `--deterministic-only`，且会在 stderr 上警告这份清单跑不了判分轮。

一个**不对称的地方**必须记住：

- **确定性轮（`runner.py`）可以没有清单**——没有清单就直接跳过冻结校验（只是报告里 `freeze_manifest_sha256` 为 `null`）。
- **面试轮（`interview_runner.py`）必须有清单**——清单不存在直接报错。它是拿去做对外结论的那条链路。**「原样重评」也走同一道门禁**（`--score-saved`）：重评只核数据集文件、评测代码与判分器，**不核 `services`**——重评不发任何被测系统请求、也不重新检索，被测服务的配置不在这一轮的因果链上（它属于采集轮）。少了这道校验，重评就是一条绕过门禁的路：换个 Judge 端点重评同一份采集结果，分数照样能写进报告而没人拦。

生成方式（在 QA 仓库根目录执行，**需要隔离栈在线**——`--dry-run` 也要抓 `services`，它同样受「抓不到就报错」约束；离线改用 `--services-json`）：

```powershell
# 预览，不落盘
.\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --dry-run

# 完整冻结：services 从在线的隔离栈抓（需要 QA_BASE_URL / QA_ADMIN_* 已配好）
.\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py

# 离线冻结：services 用事先抓好的接口响应
.\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --services-json captured\system-services.json

# 只冻确定性轮（判分环境变量未配全时明确放行，清单里记 judge=null）
.\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --deterministic-only
```

生成器会先自校验（用与门禁完全相同的实现核一遍刚生成的清单），再把已有清单**逐字节**归档到 `<数据集>/freeze-history/before-<sha8>.json` 后才落盘。`services` 只能来自被测系统本身，抓不到就报错——不要用空对象糊过去，空对象会让这层校验静默失效。**改数据集或改评分代码后必须重新冻结。**

## 执行

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,eval]"
# 离线：schema 分发、语料编排契约、指标口径、判分器、判分通道配方、DeepEval 适配层、冻结清单；不调用任何模型
.\.venv\Scripts\python.exe -m pytest tests\quality\test_dataset_loaders.py tests\quality\test_corpus_orchestration.py tests\quality\test_deterministic_metrics.py tests\quality\test_semantic_matchers.py tests\quality\test_judge_channel.py tests\quality\test_deepeval_adapter.py tests\quality\test_freeze_manifest.py
.\.venv\Scripts\python.exe scripts\inspect_quality_corpus_chunks.py
# 冻结清单（需要隔离栈在线，或先备好 --services-json）
.\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --dry-run
.\.venv\Scripts\python.exe -m pytest tests\quality\test_real_rag_quality.py -m quality_eval --alluredir=reports\allure-results-quality
```

`tests/quality/test_real_rag_quality.py` 是 `integration` 用例，需要隔离 Compose 在 127.0.0.1:18999 正常提供服务；服务不在或返回 5xx 时会在 fixture 阶段直接报错（不是 skip），跑默认测试集时请只选上面那七个离线文件。

`scripts/freeze_quality_dataset.py` 不属于离线链路：它必须拿到被测系统真实的 `chat` / `embedding` / `vision` 配置（抓不到就报错，见[冻结清单](#冻结清单正式评测的门禁)）。隔离栈不在线时用 `--services-json` 喂事先抓好的响应才能离线跑。

`scripts/inspect_quality_corpus_chunks.py` 离线调用业务仓库的分片代码（`LogicalDocumentSplitterService` + `DocumentChunkingService`），打印每篇语料的 Chunk 数与数据集 `top_k` 分布。它只读代码和语料、不连接服务，用来确认干扰文档确实构成 TopK 竞争、以及 `top_k` 相对语料规模是否合理。**修改语料或分片参数后必须重跑**——`top_k` 停用旧值就是因为语料规模变了而它没有跟着变。

真实模型端点与 Judge 凭据不手工维护：`scripts/refresh_real_model_env.py` 读业务仓库 `.env` 的根密钥解密业务 MySQL 的 `system_service_configs`，把 chat / embedding / vision 写给 `PERF_OPENAI_*`，并按需写入 `DEEPEVAL_JUDGE_*`。省略 `--judge-model` 时完全不碰 Judge 配置，`--write` 才落盘并自动备份（默认只预览、密钥只回显长度与尾 4 位）。写入 `PERF_*` 后要重建 `backend` 与 `image-worker`；`DEEPEVAL_*` 只在 pytest 进程内读取，不需要重建容器。

真实基线还要求显式启用 `QA_RUN_RAG_QUALITY`、`QA_ALLOW_QUALITY_WRITES`、`QA_QUALITY_REAL_MODELS_CONFIRMED`、`QA_RUN_RAG_INTEGRATION`、`QA_ALLOW_RAG_WRITES`。DeepEval 另需启用 `QA_RUN_DEEPEVAL` 并在当前进程提供三项 Judge 环境变量。非 localhost 目标还需显式启用 `QA_ALLOW_REMOTE_QUALITY`。

逐条报告写入 `reports/quality/<QA_RUN_ID>/deterministic/` 或 `deepeval/`，包含 `case-results.jsonl`、`case-summary.csv`、`summary.json`。报告只保存脱敏合成语料、非敏感配置摘要和异常类型。

上传前会登记动态文件名，清理 Fixture 只删除文件名完整匹配当前 `QA_RUN_ID` 且已登记的文档。测试失败和 pytest 中断退出仍会执行清理；本地生成资产由 pytest 临时目录回收，不会删除用户文件。
