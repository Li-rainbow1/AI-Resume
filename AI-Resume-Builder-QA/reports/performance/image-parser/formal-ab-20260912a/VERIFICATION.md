# 图片解析正式性能基线验证（runId: formal-ab-20260912a）

## 1. 结论

**正式 A/B 基线成立，两组均 `passed`，`comparisons.json` 非空。** 这是本仓库首轮通过完整校验（Locust 指标 + 数据库逐篇核验 + 清理校验）的正式性能对比。

**核心结论：`legacy_serial → async_c3`，图片解析总耗时均值 34.78 秒 → 15.76 秒，降幅 54.688%。**

- 每组 10 个正式样本（另有 2 个预热样本，不计入统计），`missingCount = 0`；
- 两组 Locust 失败数均为 0，`locustExitCode = 0`、`cleanupExitCode = 0`；
- 两组数据库核验均 `passed`：各 12 篇文档全部 `completed`，每篇 `indexedCount = 5`、`failedCount = 0`、`duplicateChunkCount = 0`；
- 两组输入摘要 `imageSha256` 一致，排除输入漂移。

## 2. 运行环境与可复现标识

| 项                 | 值                                                                         |
| ----------------- | ------------------------------------------------------------------------- |
| 运行时间              | 2026-09-12 00:30–00:44（约 14 分钟）                                           |
| 宿主机               | Windows-11-10.0.26200-SP0，12 逻辑 CPU                                       |
| 模型模式              | `real`                                                                    |
| legacy 组代码        | Git tree `253428dc2e206a162186c369e8c205cda7213a80`                       |
| async 组代码         | 业务仓库提交 `cff0a1915985503c2d72840d3e6b2e71fc48ed61`                         |
| legacy 镜像         | `sha256:be83dc76dc2a99c1d79232a9d5e9a87a5eb70f7a756c0a0432a5618159333f5c` |
| async 镜像          | `sha256:e420a709dadf5da974f9761b106ae7cd43974529e7cded935d71c04603f6a879` |
| chat 模型           | `deepseek-flash`（api.deepseek.com）                                        |
| embedding 模型      | `qwen3.7-text-embedding-flash`（dashscope.aliyuncs.com）                    |
| vision 模型         | `deepseek-flash`（api.deepseek.com）                                        |
| workerConcurrency | legacy 不适用（无队列）；async = 3                                                 |
| 语料                | 内嵌 5 图的固定 PDF，`preflightBytes = 6996518`                                  |

`manifest.models.vision.model` 记录为 `deepseek-flash`，**与实际服务模型一致**（该名即 DeepSeek-V4.1-Flash 的正式模型名，非 2026-09-10 之前的路由别名 `deepseek-v4-flash-vision-exp`），本报告不存在「记录请求名而非实际模型」的失真。

## 3. A/B 关键指标

| 指标                                   |             legacy_serial |                  async_c3 | 变化                  |
| ------------------------------------ | ------------------------: | ------------------------: | ------------------- |
| 总耗时均值 `total_to_image_parsed_ms`     |                   34.78 秒 |                   15.76 秒 | **−54.688%**        |
| 总耗时中位数                               |                   32.33 秒 |                   14.61 秒 | −54.8%              |
| 总耗时 P95                              |                   52.25 秒 |                   28.65 秒 | −45.2%              |
| 上传请求均值 `upload_request_ms`           |                   34.77 秒 |                    0.52 秒 | 见第 5 节口径说明          |
| 上传请求中位数                              |                   32.33 秒 |                    0.51 秒 | 见第 5 节口径说明          |
| 上传请求 P95                             |                   52.25 秒 |                    0.56 秒 | 见第 5 节口径说明          |
| 成功入库图片数                              |                        50 |                        50 | —                   |
| 吞吐 `imagesPerSecond`                 |                 8.06 张/分钟 |                16.47 张/分钟 | +104.5%             |
| Locust 失败数                           |                         0 |                         0 | —                   |
| `locustExitCode` / `cleanupExitCode` |                     0 / 0 |                     0 / 0 | —                   |



> 表中时间统一以秒为单位，便于横向比较；字段名中的 `_ms` 是脚本内标识，不随显示单位改变。毫秒原值见 `comparison.csv`（列为 `meanMs` / `medianMs` / `p95Ms`）与各组 `summary.json`。吞吐原值为「每秒」速率（`imagesPerSecond`：0.134279 / 0.274556 张/秒），表中 ×60 折算为张/分钟；其等价「平均每张图」耗时为 7.45 秒 → 3.64 秒（−51.1%），该口径由测量时间窗推出，与「总耗时均值」不同，见第 5 节第 3 条。

P95 采用**最近秩**定义（`ordered[ceil(n × 0.95) - 1]`）。本轮每组仅 10 个样本，`ceil(10 × 0.95) = 10`，故上表 P95 **等于该组样本最大值**，不是平滑的尾部指标；引用时不要把它当作长尾外推依据。若要真正的尾部刻画，需提高 `--measure-samples`。

`sampleCount = 10`、`valueCount = 10`、`missingCount = 0`（两组、两项指标一致），明细见 `comparison.csv`。

## 4. 正确性核验（数据库逐篇）

| 项                           | legacy_serial   | async_c3       |
| --------------------------- | --------------- | -------------- |
| `databaseValidation.status` | passed          | passed         |
| 文档数                         | 12              | 12             |
| 文档终态                        | 全部 `completed`  | 全部 `completed` |
| `indexedCount` 分布           | 12 篇均为 5        | 12 篇均为 5       |
| `failedCount` 合计            | 0               | 0              |
| `duplicateChunkCount` 合计    | 0               | 0              |
| `skippedImages.total`       | 0               | 0              |
| `imageChunkCount` 分布        | 5×3、6×4、7×3、8×2 | 5×1、6×9、8×2    |

`imageChunkCount` 只在 `imageChunkCount >= indexedCount` 与「无重复分片」两项上被约束，逐篇存在差异属正常（同一张图可按内容切出多个分片）；本轮 `skippedImages.total = 0`，即**没有任何图片被视觉分类判为 `decorative` / `empty`**。注意**样本口径不同**：数据库核验覆盖全部 **12 篇**（2 篇预热 + 10 篇正式），而第 3 节的耗时与吞吐统计**只用 10 篇正式样本**（预热不计入），故上表「成功入库图片数 50」= 10 篇 × 5 张，预热那 2 篇的 10 张不在其中。

## 5. 口径与边界说明

1. **两个组别的「上传请求完成」不是同一语义，两者不可直接横向比。** 旧版为同步串行链路，「上传请求完成」即全部图片处理完成，故其 `upload_request_ms`（34.77 秒）与 `total_to_image_parsed_ms`（34.78 秒）几乎相等；新版异步链路的「上传请求完成」只到上传流 `batch-complete`（0.52 秒），图片处理在其后由 Worker 异步完成。因此正式对比应采用 **`total_to_image_parsed_ms`**，即第 3 节给出的 54.688% 降幅；把 `upload_request_ms` 拿来对比会得到「降低 98.5%」的错误结论。
2. 总耗时终点口径：旧版以同步上传完成为终点，新版以状态接口观察到完成为终点（轮询间隔 1 秒，属观测误差，报告不将其称为精确队列等待时间）。
3. 吞吐口径：正式样本成功处理图片数 ÷ 正式样本测量时间窗（首个正式样本开始至最后一个结束，**含样本之间的空闲间隔**），得 8.06 → 16.47 张/分钟（原值 `imagesPerSecond` = 0.134279 → 0.274556 张/秒），等价平均每张图 7.45 秒 → 3.64 秒（降幅 51.1%）。该「每张图耗时」与第 3 节的「总耗时均值」**不是同一指标**：34.78 秒 ÷ 5 张 = 6.96 秒/张 ≠ 7.45 秒/张，差异来自测量窗口包含样本间空档，两者不可互相换算。属非稳态吞吐，不能外推为系统容量。**该值是聚合比率，不是「逐样本速率再取平均」**：若按 10 个样本各自的「5 张 ÷ 单样本耗时」再求平均，legacy 为 9.28 张/分钟、async 为 20.28 张/分钟，均高于按整窗的 8.06 / 16.47，差额即上述空档（两组各约 24.5 秒）。报告采用更保守的整窗口径，两种口径不可混用。
4. 本轮为**单轮、单机、单语料、10 个正式样本**；结论限于「图片解析：同步串行 vs 异步并发 3」这一对比维度，不构成生产容量推断，也不含 CI/CD 语义。
5. 真实模型调用会产生费用；报告只记录模型名与地址主机，不写入密钥。

## 6. 与历史轮次的关系

| runId                   | 整体 status  | legacy_serial | async_c3   | 说明                 |
| ----------------------- | ---------- | ------------- | ---------- | ------------------ |
| formal-ab-20260911x     | failed     | 未产出           | 未产出        | 编排层早期失败            |
| formal-ab-20260911y     | failed     | failed        | 未运行        | —                  |
| formal-ab-20260911z     | failed     | passed        | **failed** | async 组 DB 校验失败，见下 |
| **formal-ab-20260912a** | **passed** | **passed**    | **passed** | 首轮完整通过             |

`formal-ab-20260911z` 的 async 组失败原因已闭环：`error = 图片解析数据库校验失败：documentId=a0190b1dd8c24c7181d99ae08a033ed4，结果={extractionCount:5, indexedCount:4, failedCount:0, imageChunkCount:4}`。旧校验强制要求 `indexedCount == 5`，而第 5 张图被视觉分类为 `decorative` / `empty` 后按产品契约写入 `skipped`（合法成功终态：不建分片、文档仍 `completed`）。QA 侧校验已按 `indexedCount + skippedCount = 候选数` 校正（提交 `1c73556`），本轮即通过该校验。

本轮 `skippedImages.total = 0`，说明**新放宽口径实际未被触发**——反过来印证 `20260911z` 的 `indexedCount = 4` 属**偶发视觉分类行为**，不是异步并发导致的系统性退化。

业务仓库 HEAD 已从 `20260911z` 时的版本推进到 `cff0a19`（含视觉健康检查与空正文 OCR 的修复）。该修复只改动健康检查探针与 `extract_markdown` 的空正文字段，而图片解析性能链路走的是 `analyze_image` 分类接口，**不在同一路径上**，故两轮 legacy 组口径仍可比。

## 7. 复现命令

```powershell
cd 'D:\javalearn\秋招准备\项目\AI-Resume-Builder-QA'
$env:PYTHONUTF8 = '1'
.\.venv\Scripts\python.exe scripts\run_real_performance.py --run-id <runId>
```

该入口自行设置 `PERF_MODEL_MODE=real`、`PERF_RUN_IMAGE_WORKER=1`、`PERF_ALLOW_RAG_WRITES=1`，并从业务数据库 `system_service_configs` 解密注入 chat / embedding / vision 配置，无需手工导出 `PERF_REAL_*`。省略 `--image-variants` 时两组都跑；**产出 `comparisons.json` 要求两组在同一轮内均 `passed`**，单跑一组时该文件为 `{}`。

运行必须在用户本机终端直接执行：经代理 shell 执行会触发批量删除保护，中断 Locust 收尾，导致 `locustExitCode = 1` 并使整轮判失败。

## 8. 证据文件

| 文件                                                                 | 内容                                                   |
| ------------------------------------------------------------------ | ---------------------------------------------------- |
| `summary.json`                                                     | 被测 tree/提交、镜像、模型、数据与资源条件、分组                          |
| `comparisons.json`                                                 | `legacyToAsyncC3` 总耗时均值与降幅                           |
| `comparison.csv`                                                   | 两组两项指标的 mean / median / p95 / 样本数 / 缺失数              |
| `legacy_serial/summary.json`、`async_c3/summary.json`               | 各组样本统计、DB 校验、队列指标、吞吐、退出码                             |
| `legacy_serial/locust_stats.csv`、`async_c3/locust_stats.csv`       | Locust 最终统计（失败数 0）                                   |
| `legacy_serial/locust_failures.csv`、`locust_exceptions.csv`        | 仅表头，无失败或异常记录                                         |
| `async_c3/locust_failures.csv`、`locust_exceptions.csv`             | 仅表头，无失败或异常记录                                         |
| `async_c3/queue-metrics.jsonl`                                     | 异步组队列采样（`pendingPeak=1`、`streamLengthPeak=1`，收尾全为 0） |
| `legacy_serial/samples.jsonl`、`async_c3/samples.jsonl`             | 各 12 行（2 预热 + 10 正式）逐样本记录                            |
| `legacy_serial/compose-up.log`、`compose-down.log`                  | 专属栈生命周期                                              |
| `async_c3/compose-up.log`、`compose-down.log`                       | 专属栈生命周期                                              |
| `legacy_serial/service-errors.json`、`async_c3/service-errors.json` | 均为 `exitCode: 0`、`errors: []`                        |
| `current-build.log`                                                | 当前版本镜像构建日志                                           |
| `runtime/`                                                         | 本轮隔离运行产物与快照                                          |
