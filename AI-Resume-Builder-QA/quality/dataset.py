"""数据集加载入口。

真正的 schema 分发在 `quality/loaders.py`。本模块只保留历史入口名，让既有调用
点（runner、脚本、测试）不必同时改两处；返回的题目已经统一成 `EvalCase`，
不再有 `GoldenCase`。
"""

from pathlib import Path

from quality.loaders import (  # noqa: F401  (对外转发，便于调用点只导入一个模块)
    available_splits,
    detect_schema_version,
    load_case_set,
    resolve_cases_path,
    supported_schemas,
    verify_corpus,
)
from quality.models import CaseSet, EvalCase

QA_ROOT = Path(__file__).resolve().parents[1]
# 旧集的默认路径；该数据集已不在本机，保留常量是为了让旧入口报错时能指出位置。
DEFAULT_DATASET = QA_ROOT / "testdata" / "quality" / "golden_dataset.jsonl"


def load_golden_dataset(path: Path | None = None, split: str | None = None) -> list[EvalCase]:
    """旧入口：只要题目列表。需要语料清单请直接用 `load_case_set`。"""
    return list(load_case_set(path or DEFAULT_DATASET, split).cases)


__all__ = [
    "DEFAULT_DATASET",
    "QA_ROOT",
    "CaseSet",
    "EvalCase",
    "available_splits",
    "detect_schema_version",
    "load_case_set",
    "load_golden_dataset",
    "resolve_cases_path",
    "supported_schemas",
    "verify_corpus",
]
