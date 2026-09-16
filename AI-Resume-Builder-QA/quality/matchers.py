"""答案单元与检索片段之间的命中判定。

旧集把「怎么算命中」写成 `match_patterns` 正则；新集不给正则，只给了语义要求
（语义等价即可，但必须保留主体、条件、动作与否定关系，孤立关键词不算覆盖）。
两代在这里收敛到同一个协议：匹配器只回答「这条片段覆盖了单元的哪些子项」，
既不关心指标怎么算，也不认识任何数据集字段名。

新增判分方式（例如 embedding 相似度）只需再实现一个 `EvidenceMatcher` 子类并
在 `matcher_for_cases` 里选一次，指标与报告都不用改。

判分配置与请求配方不在这里：它们只有一份，在 `quality/judge.py`。本模块只是
`SemanticMatcher` 的组装方，`JudgeConfig` / `OpenAICompatibleJudge` /
`judge_config_from_environment` 等名字为兼容既有调用方而在此再导出。
"""

import hashlib
import json
import re
from functools import lru_cache
from typing import Any, Iterable, NamedTuple, Sequence

from quality.judge import (
    JUDGE_ENV_KEYS,
    JUDGE_ENV_PREFIXES,
    JudgeChannel,
    JudgeConfig,
    OpenAICompatibleJudge,
    judge_channel_from_environment,
    judge_config_from_environment,
)
from quality.models import AnswerUnit
from quality.sources import SourceView

__all__ = [
    "EMPTY_HIT",
    "EvidenceMatcher",
    "JUDGE_ENV_KEYS",
    "JUDGE_ENV_PREFIXES",
    "JudgeChannel",
    "JudgeConfig",
    "OpenAICompatibleJudge",
    "PatternMatcher",
    "SemanticMatcher",
    "UnitHit",
    "collect_units",
    "judge_channel_from_environment",
    "judge_config_from_environment",
    "matcher_for_cases",
    "normalize_evidence_content",
    "shared_semantic_matcher",
]


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


class UnitHit(NamedTuple):
    """一条片段对一个单元的命中结果。"""

    parts: tuple[int, ...]
    reason: str = ""


EMPTY_HIT = UnitHit(())


class EvidenceMatcher:
    """判分器基类：子类实现 `match`，批量入口默认逐单元退化。"""

    name = "base"
    requires_model = False

    def match(self, unit: AnswerUnit, source: SourceView) -> UnitHit:
        raise NotImplementedError

    def match_many(self, units: Sequence[AnswerUnit], source: SourceView) -> dict[str, UnitHit]:
        """批量判分；有能力的实现应合并为一次模型调用，降低评测成本。"""
        return {unit.unit_id: self.match(unit, source) for unit in units}


class PatternMatcher(EvidenceMatcher):
    """按数据集自带正则判命中；完全离线，不调用模型。"""

    name = "pattern"

    def match(self, unit: AnswerUnit, source: SourceView) -> UnitHit:
        if not unit.patterns:
            raise ValueError(f"单元未声明正则，不能用正则匹配器判分：{unit.unit_id}")
        content = normalize_evidence_content(source.content)
        if not content:
            return EMPTY_HIT
        hits = tuple(
            index
            for index, alternatives in enumerate(unit.patterns)
            if any(re.search(pattern, content) for pattern in alternatives)
        )
        return UnitHit(hits)


UNIT_JUDGE_SYSTEM = (
    "你在为检索评测判分，只判断「给定片段是否支持指定事实」，不做技术答疑。\n"
    "规则：\n"
    "1. 只依据片段本身判断；不得引入片段之外的知识、常识或其他文档。\n"
    "2. 语义等价即算支持，措辞不同不影响判定。\n"
    "3. 主体、条件、动作与否定关系必须一致：把条件说反、把否定说成肯定、"
    "或只出现相同关键词但没有陈述该事实，都算不支持。\n"
    "4. 只提到同一主题、同义术语，但没有讲清该事实，算不支持。\n"
    "5. 只输出 JSON，不要解释性前后缀。\n"
    '输出格式：{"units":[{"unit_id":"...","supported":true,"reason":"简短理由"}]}'
)


class SemanticMatcher(EvidenceMatcher):
    """按 `claim` 语义判覆盖；判分结果按 (单元, 片段) 缓存，重评同批片段不再调用模型。"""

    name = "semantic-llm"
    requires_model = True
    # 一次请求判完该片段对所有单元；重试上限内仍在收紧提示重问。
    cache_scope = "unit-content"

    def __init__(self, judge: JudgeChannel, cache: dict[str, UnitHit] | None = None) -> None:
        self.judge = judge
        self.cache = {} if cache is None else cache

    def summary(self) -> dict[str, Any]:
        return {"matcher": self.name, "adapter": "unit-claims-v1", **self.judge.summary()}

    def match_many(self, units: Sequence[AnswerUnit], source: SourceView) -> dict[str, UnitHit]:
        pending = [unit for unit in units if self._cache_key(unit, source) not in self.cache]
        if not source.content.strip():
            return {unit.unit_id: EMPTY_HIT for unit in units}
        if pending:
            for attempt in range(1, self._max_attempts() + 1):
                try:
                    verdicts = self._ask(pending, source, strict=attempt > 1)
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    if attempt == self._max_attempts():
                        raise
                    continue
                for unit in pending:
                    supported, reason = verdicts[unit.unit_id]
                    self.cache[self._cache_key(unit, source)] = (
                        UnitHit((0,), reason) if supported else UnitHit((), reason)
                    )
                break
        return {unit.unit_id: self.cache[self._cache_key(unit, source)] for unit in units}

    def match(self, unit: AnswerUnit, source: SourceView) -> UnitHit:
        return self.match_many([unit], source)[unit.unit_id]

    def _max_attempts(self) -> int:
        summary = self.judge.summary()
        return max(1, int(summary.get("max_attempts") or 1))

    def _cache_key(self, unit: AnswerUnit, source: SourceView) -> str:
        digest = hashlib.sha256(
            f"{unit.unit_id}\x1f{unit.claim}\x1f{source.content}".encode("utf-8")
        ).hexdigest()
        return digest

    def _ask(self, units: Sequence[AnswerUnit], source: SourceView, strict: bool) -> dict[str, tuple[bool, str]]:
        system = UNIT_JUDGE_SYSTEM
        if strict:
            system += "\n上次输出不符合格式或不完整；本次必须覆盖全部 unit_id，且 supported 为布尔值。"
        payload = {
            "片段": source.content,
            "待判事实": [
                {"unit_id": unit.unit_id, "fact": unit.claim, "acceptance": unit.acceptance}
                for unit in units
            ],
        }
        response = self.judge.answer_json(system, json.dumps(payload, ensure_ascii=False))
        rows = response.get("units")
        if not isinstance(rows, list):
            raise ValueError("判分结果缺少 units 列表")
        verdicts: dict[str, tuple[bool, str]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("判分结果条目不是对象")
            unit_id = str(row.get("unit_id") or "")
            supported = row.get("supported")
            if not isinstance(supported, bool):
                raise ValueError(f"判分结果缺少布尔 supported：{unit_id}")
            verdicts[unit_id] = (supported, str(row.get("reason") or ""))
        missing = [unit.unit_id for unit in units if unit.unit_id not in verdicts]
        if missing:
            raise ValueError("判分结果缺少单元：" + ", ".join(missing))
        return verdicts


def collect_units(cases: Iterable[Any]) -> tuple[AnswerUnit, ...]:
    return tuple(unit for case in cases for unit in getattr(case, "units", ()))


def matcher_for_cases(cases: Iterable[Any], judge: JudgeChannel | None = None) -> EvidenceMatcher:
    """按题目声明选择判分器。

    全部单元都带正则时用离线正则匹配器；只要有一个单元是语义单元，就必须给出
    判分通道（默认从环境读），不允许悄悄退回关键词匹配——那是旧集已经证明会
    漏判的做法。
    """
    units = collect_units(cases)
    if all(not unit.uses_semantics for unit in units):
        return PatternMatcher()
    if judge is not None:
        return SemanticMatcher(judge)
    return shared_semantic_matcher()


@lru_cache(maxsize=1)
def shared_semantic_matcher() -> SemanticMatcher:
    """全进程共用一份语义判分器，让 (单元, 片段) 判分缓存真正生效。"""
    return SemanticMatcher(judge_channel_from_environment())
