<!-- author: jf -->
# AI Resume Builder QA

本目录用于存放 AI Resume Builder 的独立质量保障资产。被测源码位于相邻的 `AI-Resume-Builder` 目录，产品缺陷修复提交到被测源码仓库；测试计划、测试数据、自动化代码和测试报告保存在本目录。

## 当前阶段

pytest + httpx、本地隔离 QA 环境、单条 Playwright UI 主链、五项 Locust 性能脚本及第一版 AI/RAG 质量评测脚本已建立。Locust 已完成单用户短时冒烟；真实模型质量基线和正式性能基线尚未执行。隔离环境使用独立容器、网络、端口和数据卷，不连接现有业务数据库。

## 当前文档

- `docs/baseline.md`：被测版本、运行环境和自动检查结果。
- `docs/AI-Resume-Builder-测试实施计划.md`：测试开发与质量改进的分阶段实施计划。
- `docs/manual-smoke-checklist.md`：需要人工在页面完成的冒烟检查。
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
tests/ui/             Markdown 图片预览 Playwright 场景
pages/                登录、知识库与 Markdown 预览 Page Object
testdata/             测试数据生成规则
mock_server/          OpenAI-compatible Mock AI
reports/              JUnit、临时数据与运行产物
performance/          五项 Locust 场景、公共组件、脱敏问题与 Worker 对比入口
quality/              Golden Dataset 加载、确定性指标、DeepEval、Bad Case 与报告
tests/quality/        指标单元验证和真实 RAG 质量基线入口
```

## 执行方式

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

Locust 的五个独立场景、门禁、报告和 Worker 1/3 切换方式见 [`performance/README.md`](performance/README.md)。运行产物位于 `reports/locust/` 且不会进入 Git。

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
- 已执行：Chromium 下 Markdown 图片附件上传、图片增强、预览、刷新回显与删除场景 `1 passed`，稳定环境耗时 9.49 秒，清理后残留为 0。
- 已执行：五项 Locust 脚本的小流量冒烟；正式性能基线、容量结论和优化结论均未执行。
- 已实现：15 条 Golden Dataset、七项确定性指标、DeepEval 四项指标、Bad Case 分类和质量报告；指标单元验证已执行。
- 未执行：真实 Chat、Embedding、Vision/OCR 与固定 Judge 的小规模质量基线；当前 Mock AI 隔离环境不满足真实性门禁。
- 本轮未做：正式 AI/RAG 质量门禁、本地一键回归和远端推送。
- 未完成内容不得提前写成简历成果。
