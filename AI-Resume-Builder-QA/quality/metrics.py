import re
from itertools import combinations
from typing import Any

from quality.models import GoldenCase


REFUSAL_MARKERS = ("未提供", "无法从", "没有相关", "暂无相关", "资料中没有", "知识库中未")


def normalize_text(value: object) -> str:
    return re.sub(r"\s+|[，。！？、；：,.!?;:\-_/]", "", str(value or "")).lower()


def _fact_matches(answer: str, fact: str) -> bool:
    normalized_answer = normalize_text(answer)
    alternatives = [normalize_text(item) for item in str(fact or "").split("|")]
    for candidate in alternatives:
        if not candidate:
            continue
        if re.search(r"\d", candidate):
            if re.search(rf"(?<!\d){re.escape(candidate)}(?!\d)", normalized_answer):
                return True
        elif candidate in normalized_answer:
            return True
    return False


def fact_coverage(answer: str, facts: list[str]) -> float:
    return sum(_fact_matches(answer, fact) for fact in facts) / len(facts) if facts else 1.0


def forbidden_fact_rate(answer: str, forbidden_facts: list[str]) -> float:
    return (
        sum(_fact_matches(answer, fact) for fact in forbidden_facts) / len(forbidden_facts)
        if forbidden_facts
        else 0.0
    )


def source_matches(source: dict[str, Any], expected_document: str, location: dict[str, Any] | None = None) -> bool:
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    filename = str(metadata.get("originalFilename") or "")
    if expected_document and not filename.endswith(expected_document):
        return False
    if not location:
        return True
    for key, expected in location.items():
        candidates = [metadata.get(key)]
        if key == "imageLocator":
            candidates = [metadata.get("imageSourceLocator"), metadata.get("relativePath")]
        if not any(str(expected) in str(candidate or "") for candidate in candidates):
            return False
    return True


def recall_at_k(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    if case.question_type == "no_answer":
        return None
    expected = case.expected_source_location
    hits = sum(any(source_matches(source, case.expected_document, location) for source in sources[: case.top_k]) for location in expected)
    return hits / len(expected) if expected else 0.0


def source_hit_rate(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    if case.question_type == "no_answer":
        return None
    considered = sources[: case.top_k]
    if not considered:
        # 无答案用例已在上面返回 None，走到这里只会是有答案用例：没有召回到任何来源即记 0 分。
        return 0.0
    relevant = sum(source_matches(source, case.expected_document) for source in considered)
    return relevant / len(considered)


def image_knowledge_hit(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    if case.question_type not in {"image_ocr", "table_or_flow", "mixed"}:
        return None
    for source in sources[: case.top_k]:
        metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
        if metadata.get("ingestSource") == "image_vision" and any(
            source_matches(source, case.expected_document, location) for location in case.expected_source_location
        ):
            return 1.0
    return 0.0


def no_answer_rejection(case: GoldenCase, answer: str, sources: list[dict[str, Any]]) -> float:
    if case.question_type != "no_answer":
        return 1.0
    normalized = normalize_text(answer)
    refused = any(normalize_text(marker) in normalized for marker in REFUSAL_MARKERS)
    return 1.0 if refused and forbidden_fact_rate(answer, case.forbidden_facts) == 0 else 0.0


def evaluate_case(case: GoldenCase, answer: str, sources: list[dict[str, Any]]) -> dict[str, float | None]:
    return {
        "recall_at_k": recall_at_k(case, sources),
        "source_hit_rate": source_hit_rate(case, sources),
        "image_knowledge_hit_rate": image_knowledge_hit(case, sources),
        "fact_coverage_rate": fact_coverage(answer, case.expected_facts),
        "forbidden_fact_hit_rate": forbidden_fact_rate(answer, case.forbidden_facts),
        "no_answer_rejection_rate": no_answer_rejection(case, answer, sources),
    }


def repeated_run_stability(results: list[tuple[str, list[dict[str, Any]]]], expected_facts: list[str]) -> float:
    if len(results) < 2:
        return 1.0
    signatures: list[set[str]] = []
    for answer, sources in results:
        facts = {fact for fact in expected_facts if _fact_matches(answer, fact)}
        documents = {
            str((source.get("metadata") or {}).get("documentId"))
            for source in sources
            if (source.get("metadata") or {}).get("documentId")
        }
        signatures.append({f"fact:{item}" for item in facts} | {f"doc:{item}" for item in documents})
    scores: list[float] = []
    for left, right in combinations(signatures, 2):
        union = left | right
        scores.append(len(left & right) / len(union) if union else 1.0)
    return sum(scores) / len(scores)
