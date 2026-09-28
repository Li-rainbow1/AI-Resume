"""DeepEval 指标适配层。

指标集由题目声明（`EvalCase.judge_metrics`）：旧集只用检索侧两项，面试八股集
要求 Faithfulness 与 Answer Relevancy 也要在册。之前这四项里有两项只在
`METRIC_NAMES` 里出现过、从未被真正测量，属于静默缺口；现在未实现的指标名会
直接报错，不再悄悄消失。

Judge 通道不在这里定义：配置与请求配方只有一份，在 `quality/judge.py`；把它接到
DeepEval 模型接口上的是 `quality/deepeval_judge.py`。三条判分链路（确定性层的语义
判分器、本层四项指标、面试链路）因此发出完全相同的报文——模型、地址、
`temperature=0`、`response_format=json_object`、关思考、超时与重试策略都同源。
"""

import os
from importlib.metadata import version
from typing import Any

from quality.judge import JudgeConfig, judge_config_from_environment, judge_knob
from quality.models import CaseResult, EvalCase

# deepeval 默认把遥测（PostHog）打开：每次判分都会尝试外发事件。本链路按约定只打
# 本机服务（非 localhost 目标要显式开 `QA_ALLOW_REMOTE_QUALITY`），离线或内网跑时
# 这些请求只会白等超时（实测日志：`analytics lane flush ran out of budget`）。
# 必须放在模块顶层、且早于任何 `import deepeval`：`deepeval.config.settings` 在首次
# `get_settings()` 时就把环境读进缓存，之后再改环境变量不生效。
# 遥测用 `setdefault`，留出「显式配置想开遥测」的余地；`DEEPEVAL_DISABLE_DOTENV`
# 保持强制，避免 deepeval 自带的 `.env` 反过来覆盖本进程的 Judge 配置。
os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "1")
os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"


METRIC_NAMES = {
    "faithfulness": "Faithfulness",
    "answer_relevancy": "Answer Relevancy",
    "contextual_recall": "Contextual Recall",
    "contextual_relevancy": "Contextual Relevancy",
}

RETRIEVAL_METRIC_KEYS = ("contextual_recall", "contextual_relevancy")
ANSWER_METRIC_KEYS = ("faithfulness", "answer_relevancy")
ALL_METRIC_KEYS = RETRIEVAL_METRIC_KEYS + ANSWER_METRIC_KEYS
DEFAULT_METRIC_KEYS = RETRIEVAL_METRIC_KEYS


def judge_config_summary(config: JudgeConfig | None = None) -> dict[str, Any]:
    """记录实际评分配置；地址去除凭证、查询参数和片段，不记录密钥。

    通道部分（模型、地址、请求配方、超时、重试）直接取自 `JudgeConfig.summary()`，
    本层只补上自己独有的指标级开关，避免同一个事实在两处各写一遍。
    """
    resolved = config or judge_config_from_environment()
    return {
        **resolved.summary(),
        "threshold": float(judge_knob(resolved, "THRESHOLD", "0.5")),
        "repeat_count": max(1, int(judge_knob(resolved, "REPEAT_COUNT", "1"))),
        "deepeval_version": version("deepeval"),
    }


def metric_keys_for(case: EvalCase, metric_keys: tuple[str, ...] | None = None) -> tuple[str, ...]:
    if metric_keys is not None:
        return tuple(metric_keys)
    if case.chunk_snapshot_version:
        return ()
    return tuple(case.judge_metrics or DEFAULT_METRIC_KEYS)


def evaluate_with_deepeval(
    case: EvalCase,
    result: CaseResult,
    metric_keys: tuple[str, ...] | None = None,
) -> dict[str, dict[str, Any]]:
    keys = metric_keys_for(case, metric_keys)
    if not keys:
        return {}
    from deepeval.metrics import (
        AnswerRelevancyMetric,
        ContextualRecallMetric,
        ContextualRelevancyMetric,
        FaithfulnessMetric,
    )
    from deepeval.test_case import LLMTestCase

    from quality.deepeval_judge import DeepEvalJudgeLLM

    # Faithfulness 默认把「依据不足（idk）」的陈述也计入得分，判分偏松；
    # 开启 penalize_ambiguous_claims 后无依据的补充会被扣分。
    # Answer Relevancy 没有该参数，故按指标分别传参。
    metric_factories: dict[str, tuple[Any, dict[str, Any]]] = {
        "contextual_recall": (ContextualRecallMetric, {}),
        "contextual_relevancy": (ContextualRelevancyMetric, {}),
        "faithfulness": (FaithfulnessMetric, {"penalize_ambiguous_claims": True}),
        "answer_relevancy": (AnswerRelevancyMetric, {}),
    }
    keys = metric_keys_for(case, metric_keys)
    unknown = [key for key in keys if key not in metric_factories]
    if unknown:
        raise ValueError("未实现的 DeepEval 指标：" + ", ".join(unknown))

    config = judge_config_from_environment()
    model = DeepEvalJudgeLLM(config)
    threshold = float(judge_knob(config, "THRESHOLD", "0.5"))
    repeat_count = max(1, int(judge_knob(config, "REPEAT_COUNT", "1")))
    test_case = LLMTestCase(
        input=case.question,
        actual_output=result.actual_answer,
        expected_output=case.reference_answer,
        retrieval_context=[str(source.get("content") or "") for source in result.sources],
    )
    measured: dict[str, dict[str, Any]] = {}
    for key in keys:
        metric_factory, options = metric_factories[key]
        scores: list[float] = []
        reasons: list[str] = []
        passes: list[bool] = []
        for _ in range(repeat_count):
            metric = metric_factory(
                model=model, threshold=threshold, include_reason=True, async_mode=False, **options
            )
            metric.measure(test_case)
            score = metric.score
            if score is None:
                # measure 跑完了却没给出分数，只可能是判分器内部失败（`metric.error`
                # 写着原因）。此时记 0 分会让它与「真的被判了 0 分」混为一谈，
                # 必须直接失败，交给 runner 归类成「Judge 执行失败」。
                raise RuntimeError(
                    f"{METRIC_NAMES[key]} 未返回分数：{metric.error or '未记录原因'}"
                )
            scores.append(float(score))
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
