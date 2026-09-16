from typing import Any

from quality.models import EvalCase
from quality.sources import view_source


def _document_hit(case: EvalCase, sources: list[dict[str, Any]]) -> bool:
    """是否至少有一条来源落在预期文档上；多文档题命中任意一篇即可。"""
    if not case.expected_documents:
        return False
    return any(
        view.document_matches(expected)
        for source in sources
        for view in [view_source(source)]
        for expected in case.expected_documents
    )


def classify_bad_case(
    case: EvalCase,
    answer: str,
    sources: list[dict[str, Any]],
    metrics: dict[str, float | None],
    upstream_error: str | None = None,
    judge_scores: dict[str, float] | None = None,
) -> tuple[list[str], list[str]]:
    categories: list[str] = []
    reasons: list[str] = []
    if upstream_error:
        return ["上游模型或网络失败"], [upstream_error]
    document_hit = _document_hit(case, sources)
    if case.answerable and not document_hit:
        categories.append("文档未命中")
        reasons.append("sources 未命中预期文档")
    if document_hit and metrics["recall_at_k"] is not None and metrics["recall_at_k"] < 1:
        categories.append("证据未完整命中")
        reasons.append("预期事实证据未在 TopK 内完整命中，需复核来源或匹配规则")
    if judge_scores and max(judge_scores.values(), default=1.0) - min(judge_scores.values(), default=1.0) > 0.2:
        categories.append("Judge评分波动")
        reasons.append("同项 Judge 评分波动超过 0.2")
    return list(dict.fromkeys(categories)), list(dict.fromkeys(reasons))
