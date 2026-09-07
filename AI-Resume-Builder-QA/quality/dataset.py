# author: jf
import json
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
    if not 12 <= len(cases) <= 20:
        raise ValueError("第一版 Golden Dataset 必须包含 12～20 条样例")
    return cases
