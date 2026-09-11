from typing import Any

from quality.metrics import normalize_text, source_matches
from quality.models import GoldenCase


def classify_bad_case(
    case: GoldenCase,
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
    document_hit = any(source_matches(source, case.expected_document) for source in sources)
    if case.question_type != "no_answer" and not document_hit:
        categories.append("文档未命中")
        reasons.append("sources 未命中预期文档")
    if case.question_type in {"image_ocr", "table_or_flow", "mixed"} and metrics["image_knowledge_hit_rate"] is not None and metrics["image_knowledge_hit_rate"] < 1:
        categories.append("图片Chunk未命中")
        reasons.append("sources 未命中预期 image_vision Chunk")
    if document_hit and metrics["recall_at_k"] is not None and metrics["recall_at_k"] < 1:
        categories.append("来源位置错误")
        reasons.append("预期来源位置未在 TopK 内完整命中")
    source_text = normalize_text(" ".join(str(source.get("content") or "") for source in sources))
    expected_in_source = all(normalize_text(fact) in source_text for fact in case.expected_facts)
    if case.question_type in {"image_ocr", "table_or_flow", "mixed"} and document_hit and not expected_in_source:
        categories.append("OCR内容缺失")
        reasons.append("检索内容缺少预期图片事实")
    if expected_in_source and metrics["fact_coverage_rate"] is not None and metrics["fact_coverage_rate"] < 1:
        categories.append("检索成功但生成遗漏事实")
        reasons.append("检索内容包含事实，回答未完整覆盖")
    if metrics["forbidden_fact_hit_rate"] > 0:
        categories.append("回答包含禁止事实")
        reasons.append("回答命中 forbidden_facts")
    if case.question_type == "no_answer" and metrics["no_answer_rejection_rate"] is not None and metrics["no_answer_rejection_rate"] < 1:
        categories.append("无答案问题错误作答")
        reasons.append("回答未拒答或仍返回来源")
    if judge_scores and max(judge_scores.values(), default=1.0) - min(judge_scores.values(), default=1.0) > 0.2:
        categories.append("Judge评分波动")
        reasons.append("同项 Judge 评分波动超过 0.2")
    return list(dict.fromkeys(categories)), list(dict.fromkeys(reasons))
