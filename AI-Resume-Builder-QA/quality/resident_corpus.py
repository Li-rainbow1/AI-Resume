"""常驻语料：同一数据集的知识库素材只上传一次，跨轮复用。

此前每轮评测都是「上传 → 跑题 → teardown 清光」，下一轮重新上传、重新 OCR、重新嵌入：
64 张附件的 vision 调用是真实 API 开销，两次嵌入结果不同还给跨轮读数掺进嵌入随机噪声
（同一天 b/d 两轮的确定性指标差异主要来自「语料重嵌入」）。语料本身是数据集声明并
逐字节校验过的静态素材，理应驻留。

设计（库就是真相源，不落本地状态文件——状态文件与库脱节只会制造另一类事故）：

- **指纹进前缀**：`corpus_fingerprint` = 数据集全部素材 `(relative_path, sha256)` 排序后
  的 sha256。上传文件名前缀为
  `qa-rag-resident-{schema_version}-{fp8}-`。语料任何一字节变化 ⇒ 指纹变 ⇒ 前缀变，
  旧文档自动失效且会被下一轮清掉。
- **每轮开跑对库**：`list_all_documents()` 找出当前指纹前缀的正文——集合与声明完全
  一致且全部 `ready` 才复用（跳过上传与图片解析轮询）；否则清掉**所有** `qa-rag-resident-`
  开头的文档（其它指纹的换版残留、本指纹的不完整半套）后重新上传。
- **常驻文档不进 `CreatedDocumentRegistry`**：registry 的前缀带 run_id，与常驻前缀
  天然不匹配，teardown 清理碰不到它；复用路径也没有任何本轮「创建」行为需要回滚。
- 数据集声明素材后可计算指纹并复用常驻语料；没有素材清单时跳过复用。

边界：假设评测串行执行（runner 本就单线程）。两个不同数据集并发评测会互相清理对方的
常驻语料——出现这种需求时再按 schema 分锁，现在不做。
"""

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from quality.loaders import CaseSet
    from quality.models import QualityCorpus

RESIDENT_ROOT_PREFIX = "qa-rag-resident-"

_READY_STATUS = "ready"


def corpus_fingerprint(case_set: "CaseSet") -> str:
    """数据集全部素材 `(relative_path, sha256)` 排序后的 sha256。"""
    payload = sorted((asset.relative_path, asset.sha256) for asset in case_set.assets)
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def fingerprint8(case_set: "CaseSet") -> str:
    return corpus_fingerprint(case_set)[:8]


def is_resident_capable(case_set: "CaseSet") -> bool:
    """只有「素材来自数据集目录」的集合才可常驻：指纹有据可算。"""
    return bool(case_set.assets)


def corpus_token(case_set: "CaseSet") -> str:
    """占 `corpus_for` 的 run_id 位：决定上传文件名的前缀段。"""
    if not is_resident_capable(case_set):
        raise ValueError("该数据集没有可指纹化的素材，不支持常驻语料")
    return f"resident-{case_set.schema_version}-{fingerprint8(case_set)}"


def resident_prefix(case_set: "CaseSet") -> str:
    return f"qa-rag-{corpus_token(case_set)}-"


def resident_prefix_for(schema_version: str, assets) -> str:
    """按「素材指纹」直接算前缀，不依赖 CaseSet。

    指纹算法与 `corpus_fingerprint` 完全一致（素材 `(relative_path, sha256)` 排序后
    sha256）——语料逐字节相同的两个数据集（如 interview-notes-v1 与
    interview-formal-v1）算出**同一个前缀**，因此面试链路可以直接复用检索评测
    驻留的语料，无需自己上传。`schema_version` 用语料的原生 schema（notes 集），
    保证与检索评测驻留时的前缀完全一致。
    """
    payload = sorted((asset.relative_path, asset.sha256) for asset in assets)
    fp8 = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()[:8]
    return f"qa-rag-resident-{schema_version}-{fp8}-"


async def find_reusable_by_prefix(
    rag_client: Any,
    prefix: str,
    expected_names: set[str],
) -> dict[str, str] | None:
    """对库核对指定前缀的常驻语料（通用版）。

    可复用时返回 `file_name -> document_id`；不可复用时**不清库**（调用方决定语义：
    检索评测的重导路径会清，面试链路直接报错让用户先跑检索评测），返回 `None`。
    """
    documents = await rag_client.list_all_documents()
    ready: dict[str, str] = {}
    for item in documents:
        file_name = str(item.get("fileName") or "")
        if not file_name.startswith(prefix):
            continue
        if str(item.get("status") or "") == _READY_STATUS:
            ready[file_name] = str(item.get("documentId") or "")
    if set(ready) == expected_names and len(ready) == len(expected_names):
        return ready
    return None


async def find_reusable_documents(
    rag_client: Any,
    case_set: "CaseSet",
    corpus: "QualityCorpus",
) -> dict[str, str] | None:
    """对库核对常驻语料（检索评测入口，语义含清理）。

    可复用时返回 `file_name -> document_id`；不可复用时清掉所有常驻残留（换版残留、
    本指纹的半套）并返回 `None`，由调用方走重新上传路径。
    """
    documents = await rag_client.list_all_documents()
    prefix = resident_prefix(case_set)
    current: dict[str, dict[str, Any]] = {}
    stale: list[dict[str, Any]] = []
    for item in documents:
        file_name = str(item.get("fileName") or "")
        if not file_name.startswith(RESIDENT_ROOT_PREFIX):
            continue  # 非常驻文档（各轮自清理的 run 前缀数据）不归这里管。
        if file_name.startswith(prefix):
            current[file_name] = item
        else:
            stale.append(item)  # 其它指纹/其它 schema 的常驻语料：语料换版后自动失效。

    expected = set(corpus.document_file_names)
    ready = {
        name
        for name, item in current.items()
        if str(item.get("status") or "") == _READY_STATUS
    }
    if ready == expected and len(current) == len(expected):
        # 复用成立，但换版残留（旧指纹文档）也要顺手清掉：它们已不属于任何声明，
        # 留在库里就是未声明的干扰文档。
        await _purge(rag_client, stale)
        return {name: str(item["documentId"]) for name, item in current.items()}

    # 不可复用：陈旧指纹残留与本指纹半套一起清，让重导从干净状态开始。
    await _purge(rag_client, stale + list(current.values()))
    return None


async def _purge(rag_client: Any, items: list[dict[str, Any]]) -> None:
    """逐篇删除；任何一篇失败都直接断言——半清不剩的库状态比彻底失败更难排查。"""
    failures: list[str] = []
    for item in items:
        document_id = str(item.get("documentId") or "")
        try:
            await rag_client.delete_document(document_id)
        except Exception as exc:
            failures.append(f"{document_id}: {type(exc).__name__}")
    if failures:
        raise AssertionError("常驻语料清理失败：" + "; ".join(failures))


__all__ = [
    "RESIDENT_ROOT_PREFIX",
    "corpus_fingerprint",
    "corpus_token",
    "find_reusable_by_prefix",
    "find_reusable_documents",
    "fingerprint8",
    "is_resident_capable",
    "resident_prefix",
    "resident_prefix_for",
]
