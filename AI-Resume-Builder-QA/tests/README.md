# 测试类型索引

本目录按**测试类型**收敛用例，每个类型一个子目录；共享框架代码按**技术角色**留在仓库根。
属于某一类型的编排/驱动代码随该类型收进本目录（`tests/performance/`、`tests/ui/pages/`、`tests/mock/mock_server/`）。

| 类型目录 | 覆盖内容 | pytest 标记 | 前置条件 | 依赖的框架代码 | 运行命令 |
| --- | --- | --- | --- | --- | --- |
| `tests/api/` | 管理端登录加密契约、RAG 主链生命周期（Markdown/PDF/DOCX） | `live_contract`、`integration` | 运行中的 QA 服务与凭据；写入需 `QA_ALLOW_RAG_WRITES=1` | `clients/`、`fixtures/` | `pytest tests/api` |
| `tests/mock/` | OpenAI-compatible Mock AI 契约，含超时与错误分支 | `mock_ai` | 无，走 httpx ASGI 内存传输 | `tests/mock/mock_server/` | `pytest -m mock_ai` |
| `tests/interview/` | AI 面试上下文压缩、超阈值摘要、严格流式结束与请求幂等 | 无 | 无，直接导入业务仓库后端代码 | 业务仓库 `python-ai-backend` | `pytest tests/interview` |
| `tests/ui/` | Markdown 图片附件上传、解析、预览、刷新回显与删除 | `ui` | `QA_RUN_UI=1`、Chromium、前端容器 | `fixtures/`、`tests/ui/pages/` | 见根 README「Playwright 场景」 |
| `tests/performance/` | 图片解析同步串行 vs 异步并发 3 的隔离对比编排，及其离线编排校验 | 无 | 离线部分无依赖；正式基线需 Docker 与真实业务栈 | `tests/performance/locustfiles/` | 见 [`tests/performance/README.md`](performance/README.md) |
| `tests/quality/` | 确定性检索指标单元验证、真实模型 RAG 质量基线入口 | `quality_eval`、`integration` | 真实 Chat/Embedding/Vision 与 Judge 门禁 | `quality/`、`clients/`、`fixtures/` | 见 [`quality/README.md`](../quality/README.md) |

> `tests/quality/` 与 `quality/` 为历史评测框架，配套语料与 Golden Dataset 已移除，该链路停止投入、当前不可运行。

零依赖（不需要服务、Docker 或模型）可离线全量执行的三类：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/performance tests/mock tests/interview -q -o addopts=''
```

## 导入约定

- 收敛进本目录的代码统一按**仓库根命名空间包**引用：`tests.performance.*`、`tests.mock.mock_server.app`、`tests.ui.pages.*`。
- 仓库根由 `pyproject.toml` 里的 `pythonpath = ["."]`（pytest）与脚本内的 `sys.path` 注入保证在搜索路径上，
  因此 `clients/`、`fixtures/`、`quality/` 仍按原顶层包名导入。
- 直接执行 `tests/performance/` 下的编排器（或被 `runpy` / `subprocess` 拉起）时，文件自身会先把仓库根放入搜索路径。

## 留在仓库根的框架目录

`clients/`、`fixtures/`、`quality/` 不移动：它们的**文件路径本身**被
`testdata/quality/**/freeze-manifest.json` 的 `qa_code_sha256` 锚定（键是**相对仓库根**的路径），
移动或改名会让三份清单同时失效，冻结门禁直接拒绝运行。同理 `compose.interview-quality.yml` 也不能改名。
