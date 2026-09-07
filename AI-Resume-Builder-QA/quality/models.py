# author: jf
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    question: str
    reference_answer: str
    expected_document: str
    expected_source_location: list[dict[str, Any]]
    expected_facts: list[str]
    forbidden_facts: list[str]
    question_type: str
    top_k: int


@dataclass
class CaseResult:
    case_id: str
    question: str
    actual_answer: str
    reference_answer: str
    sources: list[dict[str, Any]]
    deterministic_metrics: dict[str, float]
    deepeval_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    passed: bool = False
    failure_reasons: list[str] = field(default_factory=list)
    bad_case_categories: list[str] = field(default_factory=list)
