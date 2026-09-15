# clients —— 接口客户端（技术角色包）

被测系统的 HTTP / SSE 客户端封装，被 `tests/api`、`tests/quality`、`tests/ui` 与多个 `scripts/` 共用，
不属于任何单一测试类型，因此按**技术角色**留在仓库根，而不是收进某个 `tests/<类型>/`。

| 模块 | 职责 |
| --- | --- |
| `clients/auth.py` | 管理端登录：密码 AES-GCM 加密、Token 获取与刷新 |
| `clients/rag.py` | 知识库文档上传、状态轮询、分页查询与删除 |
| `clients/sse.py` | SSE 行解析（只识别 `data:` 行）与流式结果收集 |

> 本目录的文件路径与字节哈希被 `testdata/quality/**/freeze-manifest.json` 的 `qa_code_sha256`
> 以「相对仓库根」的键锚定，**不能移动或改名**，否则三份冻结清单同时失效。
