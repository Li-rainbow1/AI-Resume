<!-- author: jf -->
# AI Resume Builder QA

本目录用于存放 AI Resume Builder 的独立质量保障资产。被测源码位于相邻的 `AI-Resume-Builder` 目录，产品缺陷修复提交到被测源码仓库；测试计划、测试数据、自动化代码和测试报告保存在本目录。

## 当前阶段

第一阶段 pytest + httpx 基础设施与本地隔离 QA 环境已建立，包含管理员加密登录 Fixture、动态测试数据、安全清理器、OpenAI-compatible Mock AI，以及 Markdown/PDF/DOCX 的 RAG 主链自动化。隔离环境使用独立容器、网络、端口和数据卷，不连接现有业务数据库。

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
testdata/             测试数据生成规则
mock_server/          OpenAI-compatible Mock AI
reports/              JUnit、临时数据与运行产物
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

## 真实性边界

- 已完成：步骤一的 Git、运行时版本、Docker Compose 配置、容器状态和 HTTP 健康检查。
- 已完成：步骤二的 OpenAPI、前端调用层、FastAPI 路由、权限边界和数据依赖只读梳理；未发送业务请求。
- 已完成：步骤三的服务健康、未登录授权、Nginx 上传上限和 Embedding 默认环境只读预检。
- 已完成：步骤三 Embedding 配置与连通性人工验证；知识库上传已完成失败复现、源码修复、容器重建、真实向量入库与 RAG 查询回归；Python pgvector 适配层已完成容器导入与只读向量查询核对。
- 本轮不执行：不影响现有数据的全新空库初始化复现；该项不纳入本轮验收。
- 已记录：`413`、Embedding 批量上限、RAG TopK、实时语音多进程问题均有缺陷文档；真实浏览器/多 worker 回归以各缺陷文档的未执行项为准。
- 已完成：第一阶段 pytest/httpx、管理员鉴权 Fixture、运行 ID 隔离数据、自动清理、Mock AI 与 RAG 主链用例。
- 已执行：本地隔离环境全套 `16 passed`；Markdown/PDF/DOCX 三格式 RAG 主链 `3 passed`，当前运行 ID 残留文档数为 0。
- 本轮未做：Playwright、Locust、DeepEval、本地一键回归和远端推送。
- 未完成内容不得提前写成简历成果。
