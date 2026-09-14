import re
from typing import Any

from quality.models import GoldenCase


SCORING_VERSION = "evidence-v3.2"


def normalize_evidence_content(value: object) -> str:
    """保留表格行列边界，仅消除已知节点名称的等义中文注释。"""
    content = str(value or "").lower()
    content = re.sub(r"\t+", "|", content)
    labels = {"intake": "受理", "review": "审核", "approve": "批准",
              "archive": "归档", "reject": "驳回", "revise": "修订",
              "receive": "接收", "check": "检查", "accept": "接受",
              "store": "存储", "fix": "修复"}
    for node, label in labels.items():
        content = re.sub(rf"\b{node}\s*[（(]\s*{label}\s*[）)]", node, content)
    return re.sub(r"[ \r\f\v*`]+", "", content)


def normalize_text(value: object) -> str:
    return re.sub(r"\s+|[，。！？、；：,.!?;:\-_/]", "", str(value or "")).lower()


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
        if key == "imageLocator":
            matched = any(str(candidate or "").replace("\\", "/").rsplit("/", 1)[-1] == str(expected) for candidate in candidates)
        else:
            matched = any(str(candidate) == str(expected) for candidate in candidates)
        if not matched:
            return False
    return True


def evidence_details(case: GoldenCase, sources: list[dict[str, Any]]) -> dict[str, Any]:
    """按事实子项匹配原始前 K 位；重复项占排名但不重复得分，允许跨片段覆盖证据。"""
    if case.question_type == "no_answer":
        return {"status": "not_applicable", "sources": [], "units": {}}
    if not case.evidence:
        raise ValueError("有答案题必须加载精确证据，禁止退回文档级评分")
    covered = {key: set() for key in case.evidence}
    seen_contents: set[str] = set()
    details = []
    for rank, source in enumerate(sources[:case.top_k], 1):
        content = normalize_evidence_content(source.get("content"))
        source_id = str(source.get("sourceId") or source.get("source_id") or "")
        # sourceId 标识逻辑文档，多张图片或多个分片可能共用；不能据此丢弃不同内容。
        duplicate = bool(content and content in seen_contents)
        if content:
            seen_contents.add(content)
        matches = {}
        if not duplicate:
            for key, unit in case.evidence.items():
                location = ({"imageLocator": unit["asset"].rsplit("/", 1)[-1]}
                            if unit["kind"] == "image" else {"ingestSource": "text_document"})
                if not source_matches(source, case.expected_document, location):
                    continue
                hits = [index for index, alternatives in enumerate(unit["match_patterns"])
                        if any(re.search(pattern, content) for pattern in alternatives)]
                if hits:
                    covered[key].update(hits)
                    matches[key] = hits
        details.append({"rank": rank, "source_id": source_id, "duplicate": duplicate,
                        "matched_parts": matches, "relevant": bool(matches)})
    return {"status": "evaluated", "sources": details,
            "units": {key: {"matched_parts": sorted(parts),
                            "required_parts": len(case.evidence[key]["match_patterns"]),
                            "covered": len(parts) == len(case.evidence[key]["match_patterns"])}
                      for key, parts in covered.items()}}


def evaluate_case(case: GoldenCase, answer: str, sources: list[dict[str, Any]]) -> dict[str, float | None]:
    """Recall 为证据单元覆盖率；Precision 为相关片段数/K；MRR 为首个事实命中排名倒数。"""
    detail = evidence_details(case, sources)
    if detail["status"] == "not_applicable":
        return {"recall_at_k": None, "precision_at_k": None, "mrr": None}
    units = detail["units"]
    relevant = [row for row in detail["sources"] if row["relevant"]]
    return {
        "recall_at_k": sum(unit["covered"] for unit in units.values()) / len(units),
        "precision_at_k": len(relevant) / case.top_k,
        "mrr": 1.0 / relevant[0]["rank"] if relevant else 0.0,
    }


def recall_at_k(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    return evaluate_case(case, "", sources)["recall_at_k"]


def precision_at_k(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    return evaluate_case(case, "", sources)["precision_at_k"]


def mrr(case: GoldenCase, sources: list[dict[str, Any]]) -> float | None:
    return evaluate_case(case, "", sources)["mrr"]
