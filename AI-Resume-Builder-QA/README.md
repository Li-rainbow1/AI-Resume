<!-- author: jf -->
# AI Resume Builder QA

本目录用于存放 AI Resume Builder 的独立质量保障资产。被测源码位于相邻的 `AI-Resume-Builder` 目录，产品缺陷修复提交到被测源码仓库；测试计划、测试数据、自动化代码和测试报告保存在本目录。

## 当前阶段

pytest + httpx、本地隔离 QA 环境、单条 Playwright UI 主链、两项收敛后的性能编排及第一版 AI/RAG 质量评测脚本已建立。历史五项 Locust 短时冒烟记录仍保留；真实模型质量基线和正式性能基线尚未执行。隔离环境使用独立容器、网络、端口和数据卷，不连接现有业务数据库。

## 当前文档

- `docs/自动化与性能测试范围决策.md`：当前测试范围、工具选型、实施顺序和证据口径。
- `docs/第一阶段自动化测试执行记录.md`：接口自动化与核心 UI 回归执行证据。
- `docs/Locust性能测试冒烟执行记录.md`：历史五项性能场景的小流量冒烟记录与当前范围说明。
- `docs/AI-RAG质量评测执行记录.md`：Golden Dataset 与质量评测基础设施执行记录。
- `docs/接口清单.md`：34 个 REST 接口和实时语音 WebSocket 的调用、权限、输入输出与风险。
- `docs/角色权限矩阵.md`：游客、普通用户和管理员的页面、接口与数据归属边界。
- `docs/核心业务数据流.md`：简历、RAG、AI 面试、实时语音及外部依赖的数据流。
- `docs/步骤三-人工验证清单.md`：已完成的自动预检与需要用户操作的验证项。
- `docs/bugs/BUG-20260901-rag-upload-413.md`：知识库文档上传 413 缺陷记录。
- `docs/bugs/BUG-20260902-rag-embedding-batch-limit.md`：RAG Embedding 单批输入超过供应商上限缺陷记录。
- `docs/bugs/BUG-20260902-rag-embedding-content-timeout.md`：RAG Embedding 单 Chunk 内容触发上游超时缺陷记录。
- `docs/bugs/BUG-20260902-rag-topk-context-limit.md`：RAG TopK 与上下文片段数不一致缺陷记录。
- `docs/bugs/BUG-20260902-realtime-ticket-multiprocess.md`：实时语音票据跨进程消费缺陷记录。

## 第一阶段目录

```text
clients/              鉴权、SSE 与 RAG 接口客户端
fixtures/             配置、管理员 Token、数据工厂与清理器
tests/api/            RAG 主链接口自动化
tests/mock/           Mock AI 契约测试
tests/interview/      AI 面试上下文、压缩与流式完整性边界
tests/ui/             Markdown 图片预览 Playwright 场景
pages/                登录、知识库与 Markdown 预览 Page Object
testdata/             测试数据生成规则
mock_server/          OpenAI-compatible Mock AI
reports/              JUnit、临时数据与运行产物
performance/          图片解析性能编排、公共组件和报告说明
quality/              Golden Dataset 加载、确定性指标、DeepEval、Bad Case 与报告
tests/quality/        指标单元验证和真实 RAG 质量基线入口
```

## 启动与执行

### 启动 QA 环境

日常启动只需运行：

```powershell
.\scripts\start_qa.ps1
```

脚本会在缺少 `.env.test` 时自动生成本机隔离配置，启动所有 QA 容器并等待后端、Mock AI 与前端可访问。业务或前端镜像有更新时，显式传入 `-Build`：

```powershell
.\scripts\start_qa.ps1 -Build
```

首次使用仍需先安装 Python 测试依赖；脚本不会自动安装依赖或覆盖已有 `.env.test`。

### 首次安装与执行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest -m mock_ai
```

首次搭建或需要生成一套新隔离身份时：

```powershell
.\.venv\Scripts\python.exe scripts\create_local_env.py
docker compose --env-file .env.test -f compose.qa.yml up -d --build
.\.venv\Scripts\python.exe -m pytest --alluredir=reports/allure-results-full
.\.venv\Scripts\python.exe scripts\verify_run_cleanup.py
```

`.env.test` 由脚本随机生成并被 Git 忽略；`.env.test.example` 只保留字段说明和安全占位值。Compose 会在独立 MySQL 中创建一名管理员和一名普通用户，运行时散列密码，并把 Chat、Embedding、Vision/OCR 全部指向 Mock AI。`QA_RUN_ID` 同时隔离文件名、临时目录和清理范围。

Mock AI 可单独启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn mock_server.app:app --host 127.0.0.1 --port 18080
```

RAG 主链只在隔离 QA 数据库中创建和删除当前运行 ID 的知识库数据。若连接其他授权测试环境，凭据仍只通过当前进程环境变量提供，禁止写入受版本控制文件、命令历史、日志或报告：

```powershell
$env:QA_RUN_RAG_INTEGRATION = '1'
$env:QA_ALLOW_RAG_WRITES = '1'
$env:QA_RUN_LIVE_CONTRACT = '1'
$env:QA_ADMIN_USERNAME = '<仅在本机当前进程填写>'
$env:QA_ADMIN_PASSWORD = '<仅在本机当前进程填写>'
.\.venv\Scripts\python.exe -m pytest -m integration
```

隔离 Compose 内的业务后端和图片 Worker 使用 `http://mock-ai:8000`。Mock 只接受占位 Key，不保存请求 Authorization；隔离数据库不存在会覆盖环境变量的既有管理员配置。

Playwright 场景使用 Chromium，只在显式开启 `QA_RUN_UI` 时执行。失败时在 `reports/playwright/` 保留截图，并生成独立 HTML 报告：

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
docker compose --env-file .env.test -f compose.qa.yml up -d --build frontend
$env:QA_RUN_UI = '1'
.\.venv\Scripts\python.exe -m pytest tests\ui\test_markdown_image_preview.py -m ui --browser chromium --screenshot only-on-failure --output reports\playwright\artifacts --html=reports\playwright\report.html --self-contained-html
.\.venv\Scripts\python.exe scripts\verify_run_cleanup.py
```

UI 用例上传前登记带 `QA_RUN_ID` 的完整文件名。正常删除、断言失败或 pytest 中断退出时，清理 Fixture 都会再次分页查询并仅删除完全匹配的本轮文档。本地生成的 Markdown 和图片只位于 pytest 临时目录，不会删除源文件。

当前性能场景的门禁、报告与图片解析两组对比方式见 [`performance/README.md`](performance/README.md)。图片解析组别可以单独运行，避免重复执行已完成的旧版基线。运行产物位于 `reports/` 且不会进入 Git。

AI/RAG 质量评测的数据集、公式、真实模型门禁、DeepEval 配置和报告结构见 [`quality/README.md`](quality/README.md)。当前隔离 Compose 使用 Mock AI，真实质量用例会在上传前安全跳过，不会生成虚假的质量基线。

## 真实性边界

- 已完成：步骤一的 Git、运行时版本、Docker Compose 配置、容器状态和 HTTP 健康检查。
- 已完成：步骤二的 OpenAPI、前端调用层、FastAPI 路由、权限边界和数据依赖只读梳理；未发送业务请求。
- 已完成：步骤三的服务健康、未登录授权、Nginx 上传上限和 Embedding 默认环境只读预检。
- 已完成：步骤三 Embedding 配置与连通性人工验证；知识库上传已完成失败复现、源码修复、容器重建、真实向量入库与 RAG 查询回归；Python pgvector 适配层已完成容器导入与只读向量查询核对。
- 本轮不执行：不影响现有数据的全新空库初始化复现；该项不纳入本轮验收。
- 已记录：`413`、Embedding 批量上限、RAG TopK、实时语音多进程问题均有缺陷文档；真实浏览器/多 worker 回归以各缺陷文档的未执行项为准。
- 已完成：第一阶段 pytest/httpx、管理员鉴权 Fixture、运行 ID 隔离数据、自动清理、Mock AI 与 RAG 主链用例。
- 已执行：本地隔离环境全套 `16 passed`；Markdown/PDF/DOCX 三格式 RAG 主链 `3 passed`，当前运行 ID 残留文档数为 0。
- 2026-09-09 增量验证：上下文完整性用例并入一键入口后，API/Mock `24 passed`、UI `1 passed`、清理校验通过；覆盖富文本简历提取、超阈值摘要、摘要失败回滚、严格流式结束和请求幂等。
- 2026-09-09 隔离接口冒烟：AI 面试首轮实际收到 `accepted/processing/chunk/done`，相同 `requestId` 重试只恢复缓存结果；临时面试会话已按精确 ID 清理。
- 已执行：Chromium 下 Markdown 图片附件上传、图片解析、预览、刷新回显与删除场景 `1 passed`，稳定环境耗时 9.49 秒，清理后残留为 0。
- 历史范围：五项 Locust 脚本曾完成小流量冒烟；当前性能范围收敛为图片解析串行/并发对比，AI 面试 SSE 流式性能已从当前范围移除。正式性能基线、容量结论和优化结论均未执行。
- 已实现：20 条 Golden Dataset、七项确定性指标、DeepEval 四项指标、Bad Case 分类和质量报告；数据集素材与逐题依据见 `testdata/quality/README.md`，真实模型质量基线尚未执行。
- 未执行：真实 Chat、Embedding、Vision/OCR 与固定 Judge 的小规模质量基线；当前 Mock AI 隔离环境不满足真实性门禁。
- 本轮未做：正式 AI/RAG 质量门禁和远端推送；本地一键回归状态见下文。
- 未完成内容不得提前写成简历成果。

## 本地一键回归

2026-09-09 历史回归：默认 API/Mock **26 passed**、UI **2 passed**，清理通过。运行报告为 `reports/regression/reg-d5255f40af3c4ba1807b9787/summary.json`。其中 AI 面试流式异常用例已后续移出 UI 回归，当前 UI 回归只保留 Markdown 图片预览场景；AI 面试边界保留在接口级测试中。

```powershell
.\scripts\run_regression.ps1
.\scripts\run_regression.ps1 -Performance image_worker_comparison
.\scripts\run_regression.ps1 -Quality
```

前置条件：QA `.venv` 已安装 `.[test]`、Playwright Chromium 已安装，`.env.test` 中隔离账号有效，既有 Compose 服务已启动且健康。入口不安装依赖、不启动或重建容器。默认检查运行中服务、localhost 绑定端口、后端和 Worker 模型地址、管理员生效配置及 Chromium 启动能力。

默认依次执行健康检查、`tests/api`、`tests/mock`、`tests/clients`、`tests/interview`、Markdown 图片预览 UI 回归和现有清理校验。UI 强制启用；pytest 零用例、任一 skip、失败或报告缺失均返回非零。默认覆盖进程中继承的性能和质量开关为关闭；接口及 UI 的合成文档写入只在核验 Mock AI 的隔离 QA 环境执行。不会调用真实 Chat、Embedding、Vision/OCR 或 Judge。

`-Performance image_worker_comparison` 在默认 Mock 回归后追加指定场景。可选值仅为 `image_worker_comparison`；入口仅启用对应 `PERF_RUN_*`，忽略环境中其他场景开关，不修改对应 `PERF_ALLOW_*` 写入授权。图片解析场景默认比较旧同步串行和新异步并发 3，也可以用 `-ImageVariant async_c3` 单独执行新版组。命令、Mock 口径和报告结构见性能说明。

`-Quality` 只执行真实模型健康门禁、质量评测及清理校验，不运行 API/UI，也不检查前端或启动 Chromium。Mock 配置在健康门禁即被拒绝。真实评测要求提前提供 `QA_RUN_RAG_QUALITY`、`QA_ALLOW_QUALITY_WRITES`、`QA_QUALITY_REAL_MODELS_CONFIRMED`、RAG 集成写入门禁、`QA_RUN_DEEPEVAL` 和三个 Judge 环境变量，并安装 `.[test,eval]`。入口不会自动授权质量写入或切换模型。两个可选参数不能同时使用，旧 `-WithPerformance`、`-WithQuality` 参数已移除。

每次生成唯一 `QA_RUN_ID`，原终端变量在退出时恢复。输出目录为 `reports/regression/<QA_RUN_ID>/`，其中 `allure-report/index.html` 为主要测试报告入口，报告界面使用中文并生成单文件报告，可直接双击打开；同时保留 `summary.json`、各阶段日志、JUnit XML、pytest HTML、Allure Results。两个通过的 UI 场景会附带关键页面截图，失败时仍由 Playwright 额外保留失败截图。可选性能 CSV/HTML 同样放在本轮目录；质量逐条报告沿用 `reports/quality/<QA_RUN_ID>/`。所有产物均由现有 Git 忽略规则覆盖。

一键回归生成 Allure HTML 报告需要本机已安装 Allure CLI 并加入 `PATH`，同时要求可用的 Java 运行时。报告工具缺失、生成失败或缺少 `index.html` 均会使本轮回归返回非零；接口、UI 和 AI 质量用例失败时仍会尝试生成报告，方便查看失败详情。

无论阶段成功或失败，都执行 `scripts/verify_run_cleanup.py`。业务清理由既有 Fixture/Locust 注册表执行；入口不增加删除逻辑。清理校验仅统计本轮前缀残留，非零残留或校验无法完成都会使整轮失败。进程被强制终止、主机断电时无法保证 finally 执行，应保留运行 ID 并人工核验。

2026-09-08 入口调整后默认实跑：API/Mock `16 passed`、UI `1 passed`、清理校验通过，退出码 0，总耗时 42.05 秒。历史范围的 `-Performance autosave` 单用户 15 秒冒烟总请求 10、失败 0（包含准备和清理），本轮简历残留 0。`-Quality` 在健康门禁拒绝 Mock，退出码 1，清理通过，未执行 API/UI 或真实质量基线。
