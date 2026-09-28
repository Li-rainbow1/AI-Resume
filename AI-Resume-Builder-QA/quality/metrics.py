"""固定片段ID检索指标：Recall、实际返回Precision与MRR均不调用模型。"""

import re
from typing import Any, Iterable

from quality.models import EvalCase

SCORING_VERSION = "chunk-qrels-v1"

RETRIEVAL_METRICS = ("recall_at_k", "precision_at_returned", "mrr")


def normalize_text(value: object) -> str:
    """仅用于数据集标注自洽检查，不参与任何生产指标。"""
    return re.sub(r"\s+|[，。！？、；：,.!?;:\-_/]", "", str(value or "")).lower()


def evidence_details(case: EvalCase, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """按冻结ID判相关；重复ID占返回条数与排名，但不重复得分。"""
    from quality.chunk_annotations import ChunkAnnotationError, resolve_chunk
    if not case.answerable or not case.include_in_retrieval_aggregate:
        return {"status": "not_applicable", "sources": [], "units": {}}
    if not case.chunk_snapshot_version or not case.chunk_snapshot or not case.relevant_chunk_ids:
        raise ChunkAnnotationError("缺少固定片段标注，禁止回退到Judge或答案单元评分")
    if case.top_k < 1:
        raise ChunkAnnotationError("TopK必须为正整数")
    gold = set(case.relevant_chunk_ids)
    seen = set()
    details = []
    for rank, source in enumerate(sources[:case.top_k], 1):
        chunk_id = resolve_chunk(source, case.chunk_snapshot)
        duplicate = chunk_id in seen
        seen.add(chunk_id)
        details.append({"rank": rank, "chunk_id": chunk_id, "duplicate": duplicate,
                        "relevant": chunk_id in gold and not duplicate})
    return {"status": "evaluated", "matcher": "fixed-chunk-id", "snapshot_version": case.chunk_snapshot_version,
            "returned_count": len(details), "zero_returned": not details,
            "relevant_total": len(gold), "relevant_retrieved": sum(x["relevant"] for x in details),
            "max_possible_recall_at_k": min(case.top_k, len(gold)) / len(gold),
            "sources": details, "units": {}}


def evaluate_case(
    case: EvalCase,
    answer: str,
    sources: list[dict[str, Any]],
) -> dict[str, float | None]:
    """三项检索指标；无答案题为 N/A，不返回 0 或 1 以免稀释汇总均值。"""
    detail = evidence_details(case, sources)
    if detail["status"] == "not_applicable":
        return dict.fromkeys(RETRIEVAL_METRICS)
    relevant = [row for row in detail["sources"] if row["relevant"]]
    returned = int(detail["returned_count"])
    return {
        "recall_at_k": detail["relevant_retrieved"] / detail["relevant_total"],
        "precision_at_returned": len(relevant) / returned if returned else None,
        "mrr": 1.0 / relevant[0]["rank"] if relevant else 0.0,
    }


def retrieval_metrics(
    cases: Iterable[EvalCase],
    sources_by_case: dict[str, list[dict[str, Any]]],
) -> dict[str, dict[str, float | None]]:
    """批量计算固定片段检索指标。"""
    case_list = list(cases)
    return {
        case.case_id: evaluate_case(case, "", sources_by_case.get(case.case_id, []))
        for case in case_list
    }


def recall_at_k(case: EvalCase, sources: list[dict[str, Any]]) -> float | None:
    return evaluate_case(case, "", sources)["recall_at_k"]


def mrr(case: EvalCase, sources: list[dict[str, Any]]) -> float | None:
    return evaluate_case(case, "", sources)["mrr"]


__all__ = [
    "RETRIEVAL_METRICS",
    "SCORING_VERSION",
    "evaluate_case",
    "evidence_details",
    "mrr",
    "normalize_text",
    "recall_at_k",
    "retrieval_metrics",
]
