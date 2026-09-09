<!-- author: jf -->
# 性能测试使用说明

## 当前范围与入口

| 场景 | 编排入口 | 写入门禁 | 核心指标与正确性校验 |
| --- | --- | --- | --- |
| 图片解析串行/并发对比 | `run_image_worker_comparison.py` | `PERF_RUN_IMAGE_WORKER` + `PERF_ALLOW_RAG_WRITES` | 上传请求完成耗时、发起上传至全部图片入库总耗时、六张图片状态、失败数、重复图片 Chunk |
| AI 面试 SSE 流式性能 | `run_interview_sse_baseline.py` | `PERF_RUN_INTERVIEW` + `PERF_ALLOW_INTERVIEW_WRITES` + `PERF_ALLOW_RESUME_WRITES` | 首事件、首个非空正文片段、正文片段最大间隔、合法 `done` 到达耗时、完成率和会话关联 |

当前入口只支持这两个场景。自动保存、RAG 查询和文件上传属于历史范围；对应旧脚本已移除，历史执行记录继续保留事实。

## 共同安全边界

- 目标地址仅允许 `localhost` 或 `127.0.0.1` 的隔离 QA 服务。
- 写入必须同时通过场景开关和对应写入授权；选择场景不会自动放开写入权限。
- 测试数据使用 `QA_RUN_ID + UUID`，报告与日志不输出账号、Token、密钥或真实简历内容。
- 清理失败会使编排器返回非零。图片解析场景仅销毁本轮生成的专属 Compose 项目、网络和数据卷；SSE 场景会核验本轮知识文档、简历和面试会话残留。
- 当前 Mock 配置固定记录在报告中；Mock 结果不可与真实模型结果混算。

## 图片解析串行/并发对比

编排器顺序执行三组相互隔离的环境：

1. `legacy_serial`：从 Git tree `5901d9ac0f4ec21f2d72f4dd40b4da1c8638c3d2` 提取旧版代码，构建独立镜像，运行旧同步串行链路。
2. `async_c1`：提取业务仓库当前 HEAD 的完整快照及迁移，构建独立镜像并固定镜像 ID，异步图片解析 Worker 并发为 1。工作区未提交内容不进入该基线。
3. `async_c3`：使用同一新版镜像，异步图片解析 Worker 并发为 3。

旧 tree、旧版构建上下文或旧版迁移缺失时，脚本直接失败，不会拿新版并发 1 代替旧基线。三组各自使用独立 Compose 项目、网络、后端端口和数据卷，结束后只销毁自己的资源。

每组默认使用单用户、同一正文结构的六图 PDF；图片字节和正文保持一致，仅替换隔离标识。先预热 2 次，再采集 20 次正式样本。Mock Vision 延迟默认 1 秒，Mock Embedding 延迟默认 0.1 秒，均写入报告。

每个正式样本记录两条时间，之后按实际文档 ID 单独核验数据库：

- 上传请求完成耗时：从发起上传到上传流读完，并确认收到 `batch-complete`。
- 图片解析总耗时：旧版以同步上传完成为终点，新版以状态接口观察到完成为终点；数据库校验在采样后执行，不计入上述耗时。

异步组通过状态轮询观察终态。轮询间隔只会带来观测误差，报告不会将它称为精确队列等待时间。编排器会按实际文档 ID 查询 PostgreSQL，确认每篇文档有 6 条已入库图片记录、0 条失败记录、至少 6 个图片 Chunk，且没有重复图片 Chunk；失败样本保留在 `samples.jsonl`，不会从均值、中位数、P95 或失败率中静默排除。

```powershell
$env:PYTHONUTF8 = '1'
$env:QA_RUN_ID = 'perf-image-本次唯一标识'
$env:PERF_RUN_IMAGE_WORKER = '1'
$env:PERF_ALLOW_RAG_WRITES = '1'
.\.venv\Scripts\python.exe performance\run_image_worker_comparison.py
```

## AI 面试 SSE 流式性能

该场景沿用实际接口的 NDJSON 事件格式，解析网络分片、空行、`error`、缺少 `done` 和无效 JSON。`accepted`、`processing` 只能计入“首事件”，不能代替正文首包。

Mock 默认配置为：首个模型分片延迟 500 毫秒、20 个分片、相邻分片间隔 50 毫秒。还可配置缺少 `done`、无效内容和断流行为，用于单独验证异常路径；异常验证不混入正常性能基线。

默认依次运行 1、5、10 个用户，每档预热 30 秒、正式测量 120 秒。登录、创建简历、启动会话与清理请求不计入核心业务指标。只有无 `error`、收到唯一合法 `done`、包含非空正文和有效评分字段且 `sessionId` 匹配请求时，才计为成功回合；每次请求同时携带唯一 `requestId` 供服务端历史关联核验。样本数不足会标记“证据不足”并返回非零。

```powershell
$env:PYTHONUTF8 = '1'
$env:QA_RUN_ID = 'perf-interview-本次唯一标识'
$env:PERF_RUN_INTERVIEW = '1'
$env:PERF_ALLOW_INTERVIEW_WRITES = '1'
$env:PERF_ALLOW_RESUME_WRITES = '1'
.\.venv\Scripts\python.exe performance\run_interview_sse_baseline.py
```

## 一键入口与报告

```powershell
.\scripts\run_regression.ps1 -Performance image_worker_comparison
.\scripts\run_regression.ps1 -Performance interview_sse
.\scripts\run_regression.ps1 -Performance image_worker_comparison,interview_sse
```

两项同时选择时按参数顺序串行运行。每轮都写入唯一报告目录，非空报告目录会被拒绝，避免覆盖历史数据。报告包含镜像标识、宿主机资源摘要、Mock 配置、计时口径、原始样本、Locust CSV/HTML、组别汇总和清理结果；图片对比还记录新旧 Git 标识及六张图片的 SHA-256。SSE 开始前核对实际运行容器的 Mock 配置，配置不一致时需要重建 QA Mock 服务。

本轮只交付脚本和静态检查；正式性能基线、真实模型结果及简历量化数字均待后续实际运行后补入。

## 本轮检查记录

- Python 语法、PowerShell 参数语法、QA 及新旧图片栈 Compose 配置解析通过；作者标记、已配置凭据扫描与 `git diff --check` 通过。
- 一次性离线检查通过：六张图片互不相同且跨样本字节稳定，PDF/DOCX 均插入六张图片；三组图片调度顺序及 SSE 的 1/5/10 用户预热、测量分派符合预期。
- 一次性离线检查通过：首事件和首正文计时区分、正文分片重组、无效 JSON、缺少 `done`、断流、无效评分、零样本拒绝、数据库计数断言、Locust 失败后仍调用清理校验。
- 上述检查使用内存替身验证编排和失败分支；旧版镜像实际构建、完整三组服务运行、真实数据库校验与清理、正式负载及真实模型均未执行。
- 三份旧性能脚本及仅供自动保存脚本使用的公共配置字段已清理；历史报告保留。
