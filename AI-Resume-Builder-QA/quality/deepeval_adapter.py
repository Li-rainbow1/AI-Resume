import os
from importlib.metadata import version
from urllib.parse import urlsplit, urlunsplit
from typing import Any

from quality.models import CaseResult, GoldenCase


METRIC_NAMES = {
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Answer Relevancy",
    "contextual_recall": "Contextual Recall",
    "contextual_relevancy": "Contextual Relevancy",
}


def judge_config_summary() -> dict[str, Any]:
    """记录实际评分配置；地址去除凭证、查询参数和片段，不记录密钥。"""
    endpoint = urlsplit(os.environ["DEEPEVAL_JUDGE_BASE_URL"])
    host = endpoint.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    if endpoint.port:
        host += f":{endpoint.port}"
    return {
        "model": os.environ["DEEPEVAL_JUDGE_MODEL"].strip(),
        "base_url": urlunsplit((endpoint.scheme, host, endpoint.path, "", "")),
        "temperature": 0,
        "threshold": float(os.getenv("DEEPEVAL_JUDGE_THRESHOLD", "0.5")),
        "repeat_count": max(1, int(os.getenv("DEEPEVAL_JUDGE_REPEAT_COUNT", "1"))),
        "deepeval_version": version("deepeval"),
    }


def evaluate_with_deepeval(case: GoldenCase, result: CaseResult) -> dict[str, dict[str, Any]]:
    os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"
    from deepeval.metrics import (
        ContextualRecallMetric,
        ContextualRelevancyMetric,
    )
    from deepeval.models import GPTModel
    from deepeval.test_case import LLMTestCase

    model = GPTModel(
        model=os.environ["DEEPEVAL_JUDGE_MODEL"],
        base_url=os.environ["DEEPEVAL_JUDGE_BASE_URL"],
        api_key=os.environ["DEEPEVAL_JUDGE_API_KEY"],
        temperature=0,
    )
    threshold = float(os.getenv("DEEPEVAL_JUDGE_THRESHOLD", "0.5"))
    repeat_count = max(1, int(os.getenv("DEEPEVAL_JUDGE_REPEAT_COUNT", "1")))
    metric_factories = {
        "contextual_recall": ContextualRecallMetric,
        "contextual_relevancy": ContextualRelevancyMetric,
    }
    test_case = LLMTestCase(
        input=case.question,
        actual_output=result.actual_answer,
        expected_output=case.reference_answer,
        retrieval_context=[str(source.get("content") or "") for source in result.sources],
    )
    measured: dict[str, dict[str, Any]] = {}
    for key, metric_factory in metric_factories.items():
        scores: list[float] = []
        reasons: list[str] = []
        passes: list[bool] = []
        for _ in range(repeat_count):
            metric = metric_factory(model=model, threshold=threshold, include_reason=True, async_mode=False)
            metric.measure(test_case)
            scores.append(float(metric.score or 0.0))
            reasons.append(str(metric.reason or ""))
            passes.append(bool(metric.is_successful()))
        measured[key] = {
            "display_name": METRIC_NAMES[key],
            "score": sum(scores) / len(scores),
            "score_spread": max(scores) - min(scores),
            "scores": scores,
            "passed": all(passes),
            "reason": reasons[-1],
        }
    return measured
