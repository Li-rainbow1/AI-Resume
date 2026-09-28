# AI/RAG 质量评测

> 当前 RAG 检索评分版本为 `chunk-qrels-v1`。检索相关性由冻结的片段 ID 标注计算，要求本地具备配套的检索快照与 qrels 标注。离线契约测试可独立运行；真实 RAG 基线需要单独满足隔离服务、模型和写入开关。

## 当前入口

RAG 评测执行入口是 `quality/runner.py::run_quality_evaluation`。默认数据集目录为 `testdata/quality/interview-notes-v1/`，默认选择 `formal` 拆分；题目加载由 `quality/loaders.py::load_case_set` 完成，片段标注由 `quality/chunk_annotations.py::attach_annotations` 接入。

真实接口入口是 `tests/quality/test_real_rag_quality.py::test_real_rag_deterministic_baseline`。这条测试走真实 RAG 接口并写入隔离知识库，要求本机准备好 QA 配置和数据集。运行时需要同时启用：

- `QA_RUN_RAG_QUALITY=1`
- `QA_ALLOW_QUALITY_WRITES=1`
- `QA_QUALITY_REAL_MODELS_CONFIRMED=1`
- `QA_RUN_RAG_INTEGRATION=1`
- `QA_ALLOW_RAG_WRITES=1`

Chat、Embedding、Vision 服务还必须指向真实模型。检测到 Mock AI 时，测试会在上传前跳过，不会把 Mock 结果记成真实基线。目标不在 localhost 时，还需显式设置 `QA_ALLOW_REMOTE_QUALITY=1`。

## 数据集与固定片段标注

`testdata/quality/` 被 Git 忽略，数据集素材和 qrels 只保存在本机。新克隆的工作区需要自行准备目录，不能只下载仓库代码就直接跑真实评测。当前 `interview-notes-v1` 包含 50 道正式题和 10 道调试题；对应快照包含 5 篇文档、64 条图片解析记录和 209 个正文/图片片段。

当前目录需要包含：

```text
testdata/quality/interview-notes-v1/
├── formal.jsonl
├── dev.jsonl
├── corpus/
├── corpus_manifest.json
├── evidence_annotations.json
├── deepeval-formal-goldens.jsonl
├── deepeval-dev-goldens.jsonl
└── retrieval-snapshot/
    ├── annotation-manifest.json
    ├── chunks.jsonl
    ├── documents.jsonl
    ├── image_extractions.jsonl
    ├── manifest.json
    ├── qrels.jsonl
    └── review-index.jsonl
```

`chunks.jsonl` 保存固定快照里的片段 ID、内容和定位信息；`qrels.jsonl` 按题目保存完整的相关片段 ID 集合。标注针对快照中的全部 209 个片段，不能只列 TopK 内的片段。这样检索结果落在 TopK 外时，Recall 仍以完整相关集合为分母，指标不会因截断标注而虚高。当前相关性事实源是 `retrieval-snapshot/qrels.jsonl`。

准备或更新 qrels 时，先固定与被测服务相同的文档、图片解析结果和切块版本，导出片段快照；再逐题核对快照中的片段内容，记录所有能支持该题事实的 `chunk_id`。图片片段还要核对图片解析 ID 与片段偏移。更新快照或语料后，必须重新核对受影响题目的标注并更新清单。当前仓库没有自动生成 qrels 的脚本，不能用答案关键词或模型自动打分代替逐片段标注。

加载器会校验清单列出的文件 SHA-256、片段内容哈希、片段 ID 唯一性、题目与快照版本对应关系、相关 ID 是否存在，以及可回答状态是否一致。缺少 qrels、标注漂移或返回片段无法唯一映射到快照时，评测会报错停止，不会退回旧的答案单元或 Judge 评分。

`retrieval-snapshot/annotation-manifest.json` 校验片段与 qrels。数据集根目录可另外放置 `freeze-manifest.json`；存在时，Runner 还会校验数据集文件、评测源码和在线服务配置。它由 `scripts/freeze_quality_dataset.py` 生成，与 qrels 标注清单用途不同。

本机标注清单记录 60 道题对应 209 个片段，`model_calls=0`，并注明标注尚未经过独立复核。因此这些标注可以用于当前实现的回归和探索性评测；在独立复核和真实模型基线完成前，不应描述成已建立正式质量门禁。

## 一轮评测怎么走

1. 加载题目、语料及 `retrieval-snapshot` 标注，并校验哈希和 qrels。
2. 通过 RAG 客户端准备评测语料；带图片的文档等待图片处理完成。
3. 每道题调用 RAG 查询接口，读取响应中的 `sources`。
4. 根据片段内容和 ID，或文档 ID、片段类型及位置，把每个返回来源映射到冻结快照；映射不唯一就中止该题评分。
5. 用映射出的固定片段 ID 对照 qrels，计算 Recall@K、实际返回 Precision 和 MRR。
6. 报告写入 `reports/quality/<QA_RUN_ID>/deterministic/`。

## 指标口径

- **Recall@K**：TopK 中命中的唯一相关片段 ID 数 ÷ qrels 标注的相关片段总数。相关片段多于 K 时，最高可达值小于 1，报告同时记录理论上限。
- **实际返回 Precision**：命中的唯一相关片段数 ÷ 实际返回片段数。重复片段仍占返回条数和排名，但不会重复计入命中数；零返回时该指标为 `null`。
- **MRR**：第一个相关片段的排名倒数。没有相关片段时为 0；重复项仍占排名。
- **无答案题**：检索指标不适用，返回 `null`。

这些检索指标只读取 `sources` 和 qrels，不读取回答文本，也不调用 Judge。固定 qrels 已覆盖当前 RAG 评测需要的检索相关性，所以 RAG Runner 不再运行 DeepEval 检索判分。DeepEval 适配代码和 Judge 通道仍在仓库中；面试评测链路继续使用它们。

## 离线验证与真实基线

离线测试不连接 RAG 服务或 Judge；使用内存样例、假服务以及可用时读取的本地忽略数据集：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\quality -q -o addopts= --ignore=tests\quality\test_real_rag_quality.py
```

真实确定性基线单独运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\quality\test_real_rag_quality.py::test_real_rag_deterministic_baseline -m quality_eval -q
```

这条真实基线只有在 QA 服务、真实 Chat/Embedding/Vision 配置、隔离写入开关和本地数据集都满足条件时才会执行。离线测试通过只说明加载、映射、计分、Judge 配置等代码契约通过，不代表真实模型检索效果达标。

Judge 的公共请求配方集中在 `quality/judge.py`：`extra_body` 包含 `thinking.type=enabled` 和 `reasoning_effort=low`。该 Judge 用于仍需模型评判的链路；固定 qrels 的 RAG 检索指标不调用它。

## 支持范围

RAG 使用当前 `interview-notes-v1` 数据集及 `chunk-qrels-v1` 评分。旧数据加载器、语料工厂和答案单元匹配评分已移除。缺少数据集版本或使用已移除的版本会直接报错。

面试评测继续使用独立的 Judge 链路。
