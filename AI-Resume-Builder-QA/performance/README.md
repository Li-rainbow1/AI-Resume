<!-- author: jf -->
# Locust 性能测试使用说明

## 场景与入口

| 场景 | locustfile | 写入门禁 | 关键业务指标 |
| --- | --- | --- | --- |
| 高频自动保存 | `locustfiles/autosave.py` | `PERF_RUN_AUTOSAVE` + `PERF_ALLOW_RESUME_WRITES` | 保存请求响应、吞吐、P95/P99、失败率 |
| RAG 并发查询 | `locustfiles/rag_query.py` | `PERF_RUN_RAG_QUERY` + `PERF_ALLOW_RAG_WRITES` | answer/sources/预期文档命中、查询耗时 |
| 文件并发上传 | `locustfiles/file_upload.py` | `PERF_RUN_FILE_UPLOAD` + `PERF_ALLOW_RAG_WRITES` | HTTP 响应、SSE 首事件、SSE 完整时间、file-result/batch-complete |
| 图片 Worker 1/3 | `locustfiles/image_worker_comparison.py` | `PERF_RUN_IMAGE_WORKER` + `PERF_ALLOW_RAG_WRITES` | 正文入库、队列等待、增强完整时间、图片计数、失败率、加速比 |
| AI 面试 SSE | `locustfiles/interview_sse.py` | `PERF_RUN_INTERVIEW` + `PERF_ALLOW_INTERVIEW_WRITES` + `PERF_ALLOW_RESUME_WRITES` | 首事件、完整响应、done/回复/评分字段、完成率 |

所有 HTTP 200 响应仍会执行字段和业务状态断言，断言失败会进入 Locust 失败统计。流式接口完整消费 SSE/NDJSON，HTTP 建连耗时、首事件时间和完整流耗时分别记录。

RAG 查询场景在 Locust 启动阶段只上传一份共享文档，准备完成后重置统计起点。所有虚拟用户查询同一份固定知识，上传与向量写入不进入查询性能统计；测试停止后由统一监听器清理文档。

## 安全边界

- 目标地址只允许 `localhost` 或 `127.0.0.1`，用于隔离 QA Compose。
- 数据名使用 `QA_RUN_ID + UUID`；上传前登记完整文件名，取得 ID 后追加登记。
- 清理只处理本轮登记并包含当前运行 ID 的知识文档、简历和面试会话。
- 末份简历无法通过 API 删除时，仅在隔离 MySQL 中按 `resume_id + user_id + resume_name` 精确删除。
- 面试暂无删除接口，仅在隔离 MySQL 中按 `session_id + user_id` 精确删除；会话 ID 必须匹配当前运行前缀。
- 合成数据不含真实简历、账号、Token 或 Key。报告与日志不输出登录凭据。
- Chat、Embedding、Vision/OCR 由 Compose 指向 `mock-ai`；正式压测前仍需复核容器环境。

## 单场景运行

以下以自动保存为例。Windows 下 Locust 读取含中文的 `pyproject.toml` 时需要启用 UTF-8：

```powershell
$env:PYTHONUTF8 = '1'
$env:PERF_RUN_AUTOSAVE = '1'
$env:PERF_ALLOW_RESUME_WRITES = '1'
$env:LOCUST_USERS = '1'
$env:LOCUST_SPAWN_RATE = '1'
$env:LOCUST_RUN_TIME = '15s'
.\.venv\Scripts\python.exe -m locust -f performance\locustfiles\autosave.py --headless -u $env:LOCUST_USERS -r $env:LOCUST_SPAWN_RATE -t $env:LOCUST_RUN_TIME --csv reports\locust\autosave\stats --html reports\locust\autosave\report.html
```

其余场景替换 locustfile，并仅开启表格中对应的两项门禁。RAG 问题从 `testdata/rag_questions.example.csv` 读取，CSV 只保存脱敏问题。

## 图片 Worker 对比

```powershell
$env:PYTHONUTF8 = '1'
$env:LOCUST_USERS = '1'
$env:LOCUST_SPAWN_RATE = '1'
$env:LOCUST_RUN_TIME = '15s'
.\.venv\Scripts\python.exe performance\run_image_worker_comparison.py
```

脚本只重建 `compose.qa.yml` 的 `backend` 和 `image-worker`，依次设置并发 1、3，最后恢复 `.env.test` 中的并发值。输出包括两组 Locust CSV/HTML 和 `comparison.csv`。任一关键指标缺失、请求数为 0 或存在失败请求时，脚本将返回非零状态且不生成有效对比结论。短时单用户结果只用于确认脚本、断言、流消费、清理和报告链路，不能作为容量或优化结论。

## 正式压测前置条件

1. 固定被测提交、镜像、宿主机资源和 Compose 配置。
2. 使用独立运行 ID，确认三个写入门禁的授权范围。
3. 预热后多轮执行，设置足够样本数，并记录 CPU、内存、Redis、MySQL、pgvector、MinIO 和 Worker 队列。
4. 运行后执行 `scripts/verify_run_cleanup.py`，确认当前运行数据残留为 0。
5. 仅把同口径、多轮且样本充分的结果写入正式性能基线。
