import json
import hashlib
import re
from dataclasses import replace
from pathlib import Path

from quality.models import GoldenCase


REQUIRED_FIELDS = {
    "case_id",
    "question",
    "reference_answer",
    "expected_document",
    "expected_source_location",
    "expected_facts",
    "forbidden_facts",
    "question_type",
    "top_k",
}
SUPPORTED_TYPES = {"text", "image_ocr", "table_or_flow", "mixed", "no_answer"}


def load_golden_dataset(path: Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    seen_ids: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        payload = json.loads(raw_line)
        missing = REQUIRED_FIELDS - payload.keys()
        if missing:
            raise ValueError(f"第 {line_number} 行缺少字段：{sorted(missing)}")
        case = GoldenCase(**payload)
        if case.case_id in seen_ids:
            raise ValueError(f"case_id 重复：{case.case_id}")
        if case.question_type not in SUPPORTED_TYPES:
            raise ValueError(f"不支持的问题类型：{case.question_type}")
        if not 1 <= case.top_k <= 5:
            raise ValueError(f"top_k 超出接口范围：{case.case_id}")
        if not case.question.strip() or not case.reference_answer.strip() or not case.expected_facts:
            raise ValueError(f"必要内容为空：{case.case_id}")
        if case.question_type != "no_answer" and not case.expected_source_location:
            raise ValueError(f"有答案用例缺少来源位置：{case.case_id}")
        seen_ids.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("评测数据集不能为空")
    annotations = json.loads(path.with_name("evidence_annotations.json").read_text(encoding="utf-8"))
    for asset in annotations["corpus_manifest"]:
        asset_path = (path.parent / asset["path"]).resolve()
        if not asset_path.is_relative_to(path.parent.resolve()):
            raise ValueError("语料路径越界")
        if hashlib.sha256(asset_path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("语料已变化，需重新核对证据")
    reviewed = annotations["retrieval_cases"] + annotations["no_answer_cases"]
    by_id = {item["case_id"]: item for item in reviewed}
    if len(by_id) != len(reviewed) or set(by_id) != seen_ids:
        raise ValueError("题目与证据标注 ID 不一致")
    enriched = []
    for case in cases:
        item = by_id[case.case_id]
        if any(item[key] != getattr(case, key) for key in ("question", "reference_answer", "top_k", "question_type")):
            raise ValueError(f"题目与证据版本不一致：{case.case_id}")
        evidence = {key: annotations["evidence_units"][key] for key in item["required_evidence"]}
        if case.question_type == "no_answer":
            if evidence or item["count_as_pass"] or item["include_in_retrieval_aggregate"]:
                raise ValueError("无答案题不得自动计为通过")
        elif not evidence:
            raise ValueError(f"缺少精确证据：{case.case_id}")
        for unit in evidence.values():
            if not unit.get("match_patterns") or any(not group for group in unit["match_patterns"]):
                raise ValueError("证据缺少匹配规则")
            for group in unit["match_patterns"]:
                for pattern in group:
                    re.compile(pattern)
        enriched.append(replace(case, evidence=evidence))
    return enriched
