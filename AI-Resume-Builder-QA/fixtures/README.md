# fixtures —— 配置、鉴权、数据工厂与清理器（技术角色包）

pytest 插件式共享夹具，由根目录 `conftest.py` 的 `pytest_plugins` 统一加载，
被 `tests/api`、`tests/quality`、`tests/ui` 与多个 `scripts/` 共用，不属于任何单一测试类型。

| 模块 | 职责 |
| --- | --- |
| `fixtures/config.py` | 读取 `.env.test` 生成 `QaSettings`，含隔离地址与写入授权校验 |
| `fixtures/auth.py` | 管理员 Token 夹具 |
| `fixtures/data_factory.py` | 按 `QA_RUN_ID` 生成带唯一标记的 Markdown/PDF/DOCX 与图片素材 |
| `fixtures/lifecycle.py` | 已创建文档注册表，供运行结束后的精确清理 |
| `fixtures/ui.py` | Playwright 页面与浏览器夹具 |

> 本目录的文件路径与字节哈希被 `testdata/quality/**/freeze-manifest.json` 的 `qa_code_sha256`
> 以「相对仓库根」的键锚定，**不能移动或改名**，否则三份冻结清单同时失效。
