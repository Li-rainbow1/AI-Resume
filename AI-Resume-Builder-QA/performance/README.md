# 性能测试使用说明

## 当前范围与入口

| 场景 | 编排入口 | 写入门禁 | 核心指标与正确性校验 |
| --- | --- | --- | --- |
| 图片解析串行/并发对比 | `run_image_worker_comparison.py` | `PERF_RUN_IMAGE_WORKER` + `PERF_ALLOW_RAG_WRITES` | 上传请求完成耗时、发起上传至全部图片处理完成总耗时、五张图片状态、失败数、重复图片 Chunk |

当前入口只支持这一个场景。AI 面试 SSE 流式性能已从当前范围移除，对应脚本、样本与开关一并清理；自动保存、RAG 查询和文件上传属于历史范围，对应旧脚本更早已移除。历史执行记录继续保留事实。

## 共同安全边界

- 目标地址仅允许 `localhost` 或 `127.0.0.1` 的隔离 QA 服务。
- 写入必须同时通过场景开关和对应写入授权；选择场景不会自动放开写入权限。
- 测试数据使用 `QA_RUN_ID + UUID`，报告与日志不输出账号、Token、密钥或真实简历内容。
- 清理失败会使编排器返回非零。图片解析场景仅销毁本轮生成的专属 Compose 项目、网络和数据卷。
- 默认模型模式为 `mock`，用于先验证调度和报告链路；设置 `--model-mode real` 或 `PERF_MODEL_MODE=real` 后才会连接外部 Chat、Embedding、Vision。两种模式的结果分开记录，不能混算。

## 图片解析串行/并发对比

编排器默认执行两组相互隔离的环境，也支持通过参数单独运行任意一组：

1. `legacy_serial`：从 Git tree `5901d9ac0f4ec21f2d72f4dd40b4da1c8638c3d2` 提取旧版代码，构建独立镜像，运行旧同步串行链路。
2. `async_c3`：提取业务仓库当前 HEAD 的完整快照及迁移，构建独立镜像并固定镜像 ID，异步图片解析 Worker 并发为 3。工作区未提交内容不进入该基线。

旧 tree、旧版构建上下文或旧版迁移缺失时，脚本直接失败。两组各自使用独立 Compose 项目、网络、后端端口和数据卷，结束后只销毁自己的资源；选择单组时不会启动另一组。

每组默认使用单用户、同一正文结构的五图 PDF；图片字节和正文保持一致，仅替换隔离标识。固定素材见 [`testdata/performance/README.md`](../testdata/performance/README.md)，来自本地论文 PDF 的 5 个内嵌图片对象。启动前检查文件低于后端 10 MiB 限制。先预热 2 次，再采集 20 次正式样本；已完成旧版基线时可将正式样本数调整为 10，并单独运行 `async_c3`。Mock 模式下 Vision 延迟默认 1 秒、Embedding 延迟默认 0.1 秒；真实模式记录模型名和地址主机，报告不写入密钥。

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
# 只运行新版异步并发 3，不重复执行旧版
.\.venv\Scripts\python.exe performance\run_image_worker_comparison.py --variants async_c3 --measure-samples 10
```

`--variants legacy_serial,async_c3` 可显式选择两组；`--variants legacy_serial` 或 `--variants async_c3` 可分别运行单组。真实配置编排器 `scripts/run_real_performance.py` 对应使用 `--image-variants`。

真实模型模式还需提前设置 `PERF_REAL_CHAT_BASE_URL`、`PERF_REAL_CHAT_MODEL`、`PERF_REAL_EMBEDDING_BASE_URL`、`PERF_REAL_EMBEDDING_MODEL`、`PERF_REAL_VISION_BASE_URL`、`PERF_REAL_VISION_MODEL`，以及对应 API Key（本地兼容服务可为空），再追加 `--model-mode real`。真实模式会产生模型调用，运行前应确认费用、限流和数据发送范围。

## 一键入口与报告

```powershell
.\scripts\run_regression.ps1 -Performance image_worker_comparison
```

每轮都写入唯一报告目录，非空报告目录会被拒绝，避免覆盖历史数据。报告包含镜像标识、宿主机资源摘要、模型模式与模型摘要、计时口径、原始样本、Locust CSV/HTML、组别汇总和清理结果；Mock 模式额外记录延迟配置，图片对比还记录新旧 Git 标识及五张图片的 SHA-256。

本轮只交付脚本和静态检查；正式性能基线、真实模型结果及简历量化数字均待后续实际运行后补入。

## 本轮检查记录

- 2026-09-10 脚本修复复验：`python -m pytest tests/performance -q -o addopts=''` 为 19 passed。覆盖五图素材一致性与大小、Locust 独立协程退出和 CSV 关闭顺序、失败日志与汇总保留、追加采样独立目录、启动失败清理、健康等待、隔离地址和迁移目录、返回回复与保存回复一致性。Python 编译与 `git diff --check` 通过。五页 PDF 已渲染查看；本次未构建镜像、未发起压测、未调用真实模型，容器运行与真实数据库行为待后续验证。
- 以下为历史阶段检查记录，不作为本次运行结果：
- Python 语法、PowerShell 参数语法、QA 及新旧图片栈 Compose 配置解析通过；作者标记、已配置凭据扫描与 `git diff --check` 通过。
- 历史一次性离线检查使用六张图片；当前已收敛为五张，脚本样本数断言及数据库核验同步调整。离线检查结果不代表真实模型性能结果。
- 一次性离线检查通过：首事件和首正文计时区分、正文分片重组、无效 JSON、缺少 `done`、断流、无效评分、零样本拒绝、数据库计数断言、Locust 失败后仍调用清理校验。
- 上述检查使用内存替身验证编排和失败分支；旧版镜像实际构建、完整两组服务运行、真实数据库校验与清理、正式负载及真实模型均未执行。
- 三份旧性能脚本及仅供自动保存脚本使用的公共配置字段已清理；历史报告保留。
