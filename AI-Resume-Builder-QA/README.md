# AI Resume Builder QA

AI Resume Builder 的独立质量保障仓库，覆盖**接口自动化、Mock AI 契约、AI 面试流式边界、Playwright UI 场景、图片解析性能对比与 RAG 质量评测**，并保留可复现的报告产物。

被测源码为同项目的业务仓库（Vue 3 前端 + Python AI Backend）。本仓库只承载测试代码、测试数据、测试报告与 Mock AI 服务，不含业务实现。

## 测试范围

| 类型         | 目录                   | 工具                | pytest 标记      | 说明                                  |
| ---------- | -------------------- | ----------------- | -------------- | ----------------------------------- |
| 接口自动化      | `tests/api/`         | pytest + httpx    | `integration`  | RAG 主链与管理端鉴权，运行 ID 隔离数据并自动清理        |
| Mock AI 契约 | `tests/mock/`        | pytest + FastAPI  | `mock_ai`      | OpenAI-compatible Mock AI 服务本体与契约用例 |
| AI 面试边界    | `tests/interview/`   | pytest            | —              | 上下文完整性、超阈值摘要、严格流式结束与请求幂等            |
| UI 自动化     | `tests/ui/`          | pytest-playwright | `ui`           | Markdown 图片附件上传、解析、预览、刷新回显与删除主链     |
| 性能对比       | `tests/performance/` | Locust            | —              | 图片解析同步串行与异步并发 3 的对照基线               |
| 质量评测       | `tests/quality/`     | pytest + DeepEval | `quality_eval` | Golden Dataset 上的检索指标、生成指标与报告       |

各类型的入口、前置条件与运行命令见 [`tests/README.md`](tests/README.md)。

## 目录结构

```text
tests/          按测试类型收敛的用例与框架代码，六类见上表
clients/        鉴权、SSE 与 RAG 接口客户端
fixtures/       配置、管理员 Token、数据工厂与运行清理
quality/        Golden Dataset 加载、确定性指标、DeepEval 适配与报告
testdata/       测试数据与 Golden Dataset 素材
scripts/        环境生成、回归编排、清理校验与质量语料检查
reports/        测试报告与运行产物
compose.qa.yml  隔离 QA 全栈编排
```

`clients/`、`fixtures/`、`quality/` 保留在仓库根：它们被 `testdata/quality/**/freeze-manifest.json` 按仓库根相对路径逐字节锚定，移动或改名会使已冻结清单全部失效。收敛进 `tests/` 的框架代码统一以命名空间包 `tests.*` 引用，仓库根由 pytest 的 `pythonpath` 与脚本内的 `sys.path` 注入保证在搜索路径上。

内部文档与过程记录（`docs/`、`code-review/`）只在本地保留，不入本仓库。

## 环境要求

- Python >= 3.11（见 `pyproject.toml` 的 `requires-python`）
- Docker Desktop，用于隔离 QA 全栈：后端、图片 Worker、MySQL、pgvector、Redis、MinIO 与前端
- Node.js 22，仅在重建前端镜像时需要
- Allure CLI 与 Java 运行时，仅在一键回归生成 Allure HTML 报告时需要

## 快速开始

首次安装测试依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

生成一套隔离身份并启动 QA 环境：

```powershell
.\.venv\Scripts\python.exe scripts\create_local_env.py
docker compose --env-file .env.test -f compose.qa.yml up -d --build
```

日常启动可走封装脚本，它会在缺少 `.env.test` 时自动生成本机隔离配置，并等待后端、Mock AI 与前端可访问：

```powershell
.\scripts\start_qa.ps1
.\scripts\start_qa.ps1 -Build      # 业务或前端镜像有更新时追加构建
```

## 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest -m mock_ai      # Mock AI 契约，无需运行中的业务服务
.\.venv\Scripts\python.exe -m pytest tests\api       # 接口主链，需隔离 QA 服务已就绪
.\.venv\Scripts\python.exe -m pytest tests\interview # AI 面试上下文与流式边界
```

UI 与质量评测默认不执行，需显式开启：

```powershell
$env:QA_RUN_UI = '1'
.\.venv\Scripts\python.exe -m pytest tests\ui -m ui --browser chromium

$env:QA_RUN_RAG_QUALITY = '1'
$env:QA_ALLOW_QUALITY_WRITES = '1'
.\.venv\Scripts\python.exe -m pytest tests\quality\test_real_rag_quality.py -m quality_eval
```

真实模型评测还需提供 `QA_QUALITY_REAL_MODELS_CONFIRMED`、RAG 集成写入门禁、`QA_RUN_DEEPEVAL` 与三个 `DEEPEVAL_JUDGE_*` 变量，并安装 `.[test,eval]`。

一键回归按「健康检查、接口、Mock、面试、UI、清理校验」顺序执行，失败即中止且始终执行清理校验：

```powershell
.\scripts\run_regression.ps1
.\scripts\run_regression.ps1 -Performance image_worker_comparison
.\scripts\run_regression.ps1 -Quality
```

## 报告产物

`reports/` 下**只入库「跑完即固定」的报告成品**；每次运行都会产生的产物一律不入库：`reports/runtime/`、`reports/regression/` 轮次目录、`reports/playwright/`、`reports/junit/`、Allure 原始结果，以及性能编排生成的业务源码快照。面试逐题捕获同样不入库，其内容已内嵌在 `reports/quality/<run_id>/interview/case-results.jsonl` 的 `capture` 字段。

| 路径                                                      | 内容                                               |
| ------------------------------------------------------- | ------------------------------------------------ |
| `reports/performance/image-parser/formal-ab-20260912a/` | 图片解析性能正式基线，含 `VERIFICATION.md`、逐组指标与 Locust 原始统计 |
| `reports/quality/formal-v2-project-scope-20260914/`     | RAG 检索质量正式集报告，含评分修正后的离线重算结果                      |
| `reports/quality/formal-v2-20260913094309/`             | RAG 检索质量首轮正式集报告                                  |
| `reports/quality/int-76e348f67fae/`                     | 面试链路生成侧评测，逐题记录真实面试回复与每轮实际收到的依据                   |
| `reports/quality/api-title-comparison-20260912/`        | 为文档分片补充标题归属信息前后的检索 A/B 对照，含评分修正重算与混例证据定位         |
| `reports/locust/image-worker-comparison/`               | 图片解析对照的 Locust 统计与 HTML 报告                       |

一键回归的每轮产物写入 `reports/regression/<QA_RUN_ID>/`（不入库），以 `allure-report/index.html` 为主要报告入口。

## 隔离与安全

- QA 栈使用独立容器、网络、端口与数据卷，**不连接业务数据库**；隔离环境内的后端与图片 Worker 统一指向本地 Mock AI。
- `.env.test` 由 `scripts/create_local_env.py` 随机生成并被 Git 忽略；仓库内只保留字段说明与占位值的 `.env.test.example`。
- 凭据只通过当前进程的环境变量传入，不写入受版本控制的文件、命令历史、日志或报告。
- 写入类用例受显式开关控制（如 `QA_ALLOW_RAG_WRITES`），并按 `QA_RUN_ID` 精确清理本轮数据；每轮结束执行 `scripts/verify_run_cleanup.py` 校验残留。
- 真实模型质量评测在命中 Mock AI 时安全跳过，不生成虚假基线。

## 说明

本仓库为个人项目的质量保障资产，暂未附开源许可；如需引用或复用请先联系作者。
