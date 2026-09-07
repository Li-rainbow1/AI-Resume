<!-- author: jf -->
# AI/RAG 质量评测

## 当前状态

- 脚本已实现：15 条 Golden Dataset、确定性指标、真实 RAG 执行器、DeepEval 四项指标、Bad Case 分类、JSONL/CSV/JSON 报告和 Allure 摘要。
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

版本控制文件为 `testdata/quality/golden_dataset.jsonl`，共 15 条脱敏固定样例：正文 4 条、图片 OCR 4 条、表格或流程图 2 条、正文图片混合 2 条、无答案 3 条。运行时资产由 `quality/assets.py` 在 pytest 临时目录生成，文件名带 `QA_RUN_ID` 和 UUID；内容只含虚构编号、流程和数值。

每条数据必须包含 `case_id`、`question`、`reference_answer`、`expected_document`、`expected_source_location`、`expected_facts`、`forbidden_facts`、`question_type`、`top_k`。加载器会检查字段、类型、唯一性和总条数。

## 确定性指标

| 指标 | 输入 | 公式与方向 | 单条失败原因 | 汇总 |
| --- | --- | --- | --- | --- |
| Recall@K | 预期位置、TopK sources | 命中的预期位置数 / 预期位置数，越高越好 | TopK 未完整命中预期位置 | 逐条分数的算术平均 |
| 来源命中率 | 预期文档、TopK sources | 命中预期文档的 source 数 / TopK source 数，越高越好 | sources 未命中预期文档 | 逐条分数的算术平均 |
| 图片知识命中率 | 问题类型、位置、`ingestSource` | 图片类问题命中预期 `image_vision` Chunk 为 1，否则为 0，越高越好 | 图片 Chunk 未命中 | 逐条分数的算术平均 |
| 关键事实覆盖率 | answer、expected_facts | 回答命中的预期事实数 / 预期事实数，越高越好 | 生成遗漏事实或 OCR 内容缺失 | 逐条分数的算术平均 |
| 禁止事实命中率 | answer、forbidden_facts | 回答命中的禁止事实数 / 禁止事实数，越低越好 | 回答包含禁止事实 | 逐条分数的算术平均 |
| 无答案拒答率 | answer、sources | 含拒答表达且 sources 为空为 1，否则为 0，越高越好 | 无答案问题错误作答 | 逐条分数的算术平均 |
| 重复运行稳定性 | 多次 answer、sources | 预期事实集合与文档 ID 集合两两 Jaccard 的平均值，越高越好 | 多次检索或回答事实不一致 | 逐条分数的算术平均 |

HTTP 200 只表示请求成功。用例还会校验 answer、sources、预期文档、来源位置、关键事实、禁止事实以及重复运行结果。

## DeepEval

接入 `Faithfulness`、`Answer Relevancy`、`Contextual Precision`、`Contextual Recall`。字段映射如下：

- `input`：问题；
- `actual_output`：RAG 实际回答；
- `expected_output`：参考答案；
- `retrieval_context`：sources 中的 `content`。

固定 Judge 只从当前进程的 `DEEPEVAL_JUDGE_MODEL`、`DEEPEVAL_JUDGE_BASE_URL`、`DEEPEVAL_JUDGE_API_KEY` 读取。可选的 `DEEPEVAL_JUDGE_THRESHOLD` 控制阈值，`DEEPEVAL_JUDGE_REPEAT_COUNT` 控制同项重复评分；重复评分极差超过 0.2 会归类为“Judge评分波动”。这些变量不写入 `.env.test.example`、日志或报告。

实现依据：[DeepEval 指标说明](https://deepeval.com/docs/metrics-introduction)、[Contextual Precision](https://deepeval.com/docs/metrics-contextual-precision)、[自定义 OpenAI-compatible 模型](https://deepeval.com/integrations/models/openai)。本项目锁定并按实际安装的 `deepeval==4.1.4` 接口适配。

## 执行

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[test,eval]"
.\.venv\Scripts\python.exe -m pytest tests\quality\test_deterministic_metrics.py
.\.venv\Scripts\python.exe -m pytest tests\quality\test_real_rag_quality.py -m quality_eval --alluredir=reports\allure-results-quality
```

真实基线还要求显式启用 `QA_RUN_RAG_QUALITY`、`QA_ALLOW_QUALITY_WRITES`、`QA_QUALITY_REAL_MODELS_CONFIRMED`、`QA_RUN_RAG_INTEGRATION`、`QA_ALLOW_RAG_WRITES`。DeepEval 另需启用 `QA_RUN_DEEPEVAL` 并在当前进程提供三项 Judge 环境变量。非 localhost 目标还需显式启用 `QA_ALLOW_REMOTE_QUALITY`。

逐条报告写入 `reports/quality/<QA_RUN_ID>/deterministic/` 或 `deepeval/`，包含 `case-results.jsonl`、`case-summary.csv`、`summary.json`。报告只保存脱敏合成语料、非敏感配置摘要和异常类型。

上传前会登记动态文件名，清理 Fixture 只删除文件名完整匹配当前 `QA_RUN_ID` 且已登记的文档。测试失败和 pytest 中断退出仍会执行清理；本地生成资产由 pytest 临时目录回收，不会删除用户文件。
