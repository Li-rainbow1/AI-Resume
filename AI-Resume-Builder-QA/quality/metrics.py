"""确定性检索指标。

四项指标只读「答案单元」与「检索返回的来源」，完全不看回答文本——回答正确性
由 DeepEval 那几项承担。这里不认识任何数据集字段名：旧集的正则与新集的语义
判分都通过 `quality.matchers.EvidenceMatcher` 注入。

口径（与数据集 README 对齐）：
- `recall_at_k`：TopK 内被覆盖的答案单元数 / 单元总数。一个单元可以由多条片段
  合起来支持，同一单元不重复计分。
- `precision_at_k`：TopK 内真正支持了至少一个所问事实的非重复片段数 / top_k。
- `precision_at_returned`：相关非重复片段数 / 实际返回片段数；无返回记 N/A。
  它和上一个分母不同，两个都要看，避免把「少返回」混同成「返回准」。
- `mrr`：首个真正相关片段排名的倒数，没有相关结果为 0。
"""

import re
from typing import Any, Iterable

from quality.matchers import (  # noqa: F401  (normalize_evidence_content 由本模块继续对外提供)
    EMPTY_HIT,
    EvidenceMatcher,
    normalize_evidence_content,
    matcher_for_cases,
)
from quality.models import (
    IMAGE_KIND,
    TEXT_KIND,
    AnswerUnit,
    EvalCase,
    SourceSelector,
)
from quality.sources import SourceView, view_source, view_sources

SCORING_VERSION = "evidence-v4"

RETRIEVAL_METRICS = ("recall_at_k", "precision_at_k", "precision_at_returned", "mrr")


def normalize_text(value: object) -> str:
    """仅用于数据集标注自洽检查，不参与任何生产指标。"""
    return re.sub(r"\s+|[，。！？、；：,.!?;:\-_/]", "", str(value or "")).lower()


def selector_matches(selector: SourceSelector, view: SourceView) -> bool:
    """来源是否落在单元的某个可接受范围内。"""
    if not view.document_matches(selector.document):
        return False
    if selector.kind == IMAGE_KIND and not view.locator_matches(selector.locator):
        return False
    if selector.kind == TEXT_KIND and view.is_image:
        return False
    if selector.locator and not view.locator_matches(selector.locator):
        return False
    return True


def source_matches(
    source: dict[str, Any],
    expected_document: str | None = None,
    location: dict[str, Any] | None = None,
) -> bool:
    """兼容入口：把「单文档 + 位置字典」翻译成一个来源选择器再判定。"""
    selector = SourceSelector(document=expected_document)
    if location:
        if location.get("imageLocator"):
            selector = SourceSelector(
                document=expected_document,
                kind=IMAGE_KIND,
                locator=str(location["imageLocator"]),
            )
        elif location.get("ingestSource"):
            selector = SourceSelector(document=expected_document, kind=TEXT_KIND)
    return selector_matches(selector, view_source(source))


def _eligible_units(case: EvalCase, view: SourceView) -> list[AnswerUnit]:
    return [
        unit
        for unit in case.units
        if any(selector_matches(selector, view) for selector in unit.selectors)
    ]


def evidence_details(
    case: EvalCase,
    sources: list[dict[str, Any]],
    matcher: EvidenceMatcher | None = None,
) -> dict[str, Any]:
    """逐片段记录命中情况；重复内容占排名但不重复得分。"""
    if not case.answerable:
        return {"status": "not_applicable", "sources": [], "units": {}}
    if not case.units:
        raise ValueError("有答案题必须声明答案单元，禁止退回文档级评分")
    resolved = matcher or matcher_for_cases([case])
    views = view_sources(sources)[: case.top_k]
    covered: dict[str, set[int]] = {unit.unit_id: set() for unit in case.units}
    seen_contents: set[str] = set()
    details: list[dict[str, Any]] = []
    for view in views:
        content = normalize_evidence_content(view.content)
        # sourceId 标识逻辑文档，多张图片或多个分片可能共用；不能据此丢弃不同内容。
        duplicate = bool(content) and content in seen_contents
        if content:
            seen_contents.add(content)
        matched: dict[str, list[int]] = {}
        if not duplicate:
            eligible = _eligible_units(case, view)
            if eligible:
                hits = resolved.match_many(eligible, view)
                for unit in eligible:
                    parts = (hits.get(unit.unit_id) or EMPTY_HIT).parts
                    if parts:
                        covered[unit.unit_id].update(parts)
                        matched[unit.unit_id] = sorted(parts)
        details.append({
            "rank": view.rank,
            "source_id": view.source_id,
            "document": view.document,
            "is_image": view.is_image,
            "locator": view.locator,
            "duplicate": duplicate,
            "matched_parts": matched,
            "relevant": bool(matched),
        })
    units = {
        unit.unit_id: {
            "claim": unit.claim,
            "matched_parts": sorted(covered[unit.unit_id]),
            "required_parts": unit.required_parts,
            "covered": len(covered[unit.unit_id]) == unit.required_parts,
        }
        for unit in case.units
    }
    return {
        "status": "evaluated",
        "matcher": getattr(resolved, "name", "unknown"),
        "returned_count": len(views),
        "sources": details,
        "units": units,
    }


def evaluate_case(
    case: EvalCase,
    answer: str,
    sources: list[dict[str, Any]],
    matcher: EvidenceMatcher | None = None,
) -> dict[str, float | None]:
    """四项检索指标；无答案题为 N/A，不返回 0 或 1 以免稀释汇总均值。"""
    detail = evidence_details(case, sources, matcher)
    if detail["status"] == "not_applicable":
        return dict.fromkeys(RETRIEVAL_METRICS)
    units = detail["units"]
    relevant = [row for row in detail["sources"] if row["relevant"]]
    returned = int(detail["returned_count"])
    return {
        "recall_at_k": sum(unit["covered"] for unit in units.values()) / len(units),
        "precision_at_k": len(relevant) / case.top_k,
        "precision_at_returned": len(relevant) / returned if returned else None,
        "mrr": 1.0 / relevant[0]["rank"] if relevant else 0.0,
    }


def retrieval_metrics(
    cases: Iterable[EvalCase],
    sources_by_case: dict[str, list[dict[str, Any]]],
    matcher: EvidenceMatcher | None = None,
) -> dict[str, dict[str, float | None]]:
    """批量求值；matcher 只解析一次，避免每题重建判分器与丢掉判分缓存。"""
    case_list = list(cases)
    resolved = matcher or matcher_for_cases(case_list)
    return {
        case.case_id: evaluate_case(case, "", sources_by_case.get(case.case_id, []), resolved)
        for case in case_list
    }


def recall_at_k(case: EvalCase, sources: list[dict[str, Any]], matcher: EvidenceMatcher | None = None) -> float | None:
    return evaluate_case(case, "", sources, matcher)["recall_at_k"]


def precision_at_k(case: EvalCase, sources: list[dict[str, Any]], matcher: EvidenceMatcher | None = None) -> float | None:
    return evaluate_case(case, "", sources, matcher)["precision_at_k"]


def mrr(case: EvalCase, sources: list[dict[str, Any]], matcher: EvidenceMatcher | None = None) -> float | None:
    return evaluate_case(case, "", sources, matcher)["mrr"]


__all__ = [
    "RETRIEVAL_METRICS",
    "SCORING_VERSION",
    "evaluate_case",
    "evidence_details",
    "mrr",
    "normalize_evidence_content",
    "normalize_text",
    "precision_at_k",
    "recall_at_k",
    "retrieval_metrics",
    "selector_matches",
    "source_matches",
]
