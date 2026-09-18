"""按 `schema_version` 分发数据集加载器。

一个数据集只能声明一件事：这些题目怎么变成 `EvalCase`。指标、匹配器、报告都
不认识数据集字段名，所以新增一个数据集 = 新增一个加载函数 + 一行注册。

两代 schema 的差异在加载阶段被抹平：
- `evidence-v3`（旧集）：题上一篇 `expected_document`，证据带 `match_patterns`
  正则；加载后每个正则组变成一个「子项」，全部命中也只算一个答案单元。
- `interview-notes-v1`（面试八股集）：题上多篇 `expected_documents`，答案单元给
  `acceptable_evidence_ids`（或关系），不给正则，命中由语义判分器决定。

语料完整性校验（路径不得越界 + SHA-256 必须一致）两代共用同一段实现。
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Callable, Iterable, Sequence

from quality.deepeval_adapter import RETRIEVAL_METRIC_KEYS
from quality.models import (
    IMAGE_KIND,
    LEGACY_SCHEMA_VERSION,
    TEXT_KIND,
    AnswerUnit,
    CaseSet,
    CorpusAsset,
    EvalCase,
    SourceSelector,
)

Loader = Callable[[Path, Path], tuple[list[EvalCase], list[CorpusAsset]]]

_LOADERS: dict[str, Loader] = {}

# 只有这几类文件名参与「拆分」识别；DeepEval 的 golden 导出是同题同批，不是独立拆分。
_SPLIT_EXCLUDED_PREFIXES = ("deepeval-",)


def register_loader(schema_version: str) -> Callable[[Loader], Loader]:
    def decorator(loader: Loader) -> Loader:
        _LOADERS[schema_version] = loader
        return loader

    return decorator


def supported_schemas() -> tuple[str, ...]:
    return tuple(sorted(_LOADERS))


def available_splits(dataset_dir: Path) -> dict[str, Path]:
    """数据集目录里可用的拆分文件；键是 `formal` / `dev` 这类拆分名。"""
    return {
        path.stem: path
        for path in sorted(dataset_dir.glob("*.jsonl"))
        if not path.name.startswith(_SPLIT_EXCLUDED_PREFIXES)
    }


def resolve_cases_path(dataset_path: Path, split: str | None = None) -> tuple[Path, Path]:
    """把「数据集目录或题目文件」解析成 (数据集目录, 题目文件)。"""
    if dataset_path.is_dir():
        splits = available_splits(dataset_path)
        if not splits:
            raise ValueError(f"数据集目录里没有可用的 jsonl 拆分文件：{dataset_path}")
        if split:
            if split not in splits:
                raise ValueError(
                    f"数据集没有名为 {split} 的拆分，可选：{', '.join(sorted(splits))}"
                )
            return dataset_path, splits[split]
        if len(splits) == 1:
            return dataset_path, next(iter(splits.values()))
        if "formal" in splits:
            return dataset_path, splits["formal"]
        raise ValueError(
            f"数据集有多个拆分，必须显式指定：(答案只能是 {'/'.join(sorted(splits))})"
        )
    if not dataset_path.exists():
        raise FileNotFoundError(f"数据集不存在：{dataset_path}")
    return dataset_path.parent, dataset_path


def detect_schema_version(cases_path: Path) -> str:
    """读第一道题声明的 schema_version；旧集没有该字段，回落到 evidence-v3。"""
    for raw_line in cases_path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        payload = json.loads(raw_line)
        return str(payload.get("schema_version") or LEGACY_SCHEMA_VERSION)
    raise ValueError(f"数据集为空：{cases_path}")


def iter_cases(cases_path: Path) -> Iterable[dict]:
    for line_number, raw_line in enumerate(cases_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {line_number} 行不是合法 JSON：{exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"第 {line_number} 行不是 JSON 对象")
        yield payload


def verify_corpus(dataset_dir: Path, assets: Sequence[CorpusAsset]) -> None:
    """语料路径不得越界，且哈希必须与标注一致；否则证据位置已经不可信。"""
    root = dataset_dir.resolve()
    for asset in assets:
        asset_path = (dataset_dir / asset.relative_path).resolve()
        if not asset_path.is_relative_to(root):
            raise ValueError(f"语料路径越界：{asset.relative_path}")
        if not asset_path.exists():
            raise ValueError(f"语料文件缺失：{asset.relative_path}")
        if hashlib.sha256(asset_path.read_bytes()).hexdigest() != asset.sha256:
            raise ValueError(f"语料已变化，需重新核对证据：{asset.relative_path}")


def load_case_set(dataset_path: Path, split: str | None = None) -> CaseSet:
    """加载任一受支持 schema 的数据集。"""
    dataset_dir, cases_path = resolve_cases_path(Path(dataset_path), split)
    schema_version = detect_schema_version(cases_path)
    if schema_version not in _LOADERS:
        raise ValueError(
            f"没有注册 schema_version={schema_version} 的加载器；"
            f"已支持：{', '.join(supported_schemas()) or '（空）'}"
        )
    cases, assets = _LOADERS[schema_version](cases_path, dataset_dir)
    if not cases:
        raise ValueError(f"评测数据集不能为空：{cases_path}")
    verify_corpus(dataset_dir, assets)
    return CaseSet(
        schema_version=schema_version,
        dataset_dir=dataset_dir,
        cases_path=cases_path,
        cases=tuple(cases),
        assets=tuple(assets),
    )


# --------------------------------------------------------------------------- #
# 旧集：单文档 + 正则
# --------------------------------------------------------------------------- #

_LEGACY_REQUIRED_FIELDS = {
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
_LEGACY_TYPES = {"text", "image_ocr", "table_or_flow", "mixed", "no_answer"}
_LEGACY_MAX_TOP_K = 5


def _legacy_unit(unit_id: str, unit: dict, expected_document: str) -> AnswerUnit:
    if unit.get("kind") not in {TEXT_KIND, IMAGE_KIND}:
        raise ValueError(f"证据类型不支持：{unit_id}")
    patterns = unit.get("match_patterns")
    if not patterns or any(not group for group in patterns):
        raise ValueError("证据缺少匹配规则")
    for group in patterns:
        for pattern in group:
            re.compile(pattern)
    selector = SourceSelector(
        document=expected_document,
        kind=unit["kind"],
        locator=unit.get("asset") if unit["kind"] == IMAGE_KIND else None,
    )
    return AnswerUnit(
        unit_id=unit_id,
        claim=unit_id,
        selectors=(selector,),
        required_parts=len(patterns),
        patterns=tuple(tuple(group) for group in patterns),
        acceptance="正则组全部命中即覆盖",
    )


@register_loader(LEGACY_SCHEMA_VERSION)
def load_legacy_v3(cases_path: Path, dataset_dir: Path) -> tuple[list[EvalCase], list[CorpusAsset]]:
    annotations = json.loads((dataset_dir / "evidence_annotations.json").read_text(encoding="utf-8"))
    assets = [
        CorpusAsset(relative_path=str(item["path"]), sha256=str(item["sha256"]), kind="document")
        for item in annotations["corpus_manifest"]
    ]
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for payload in iter_cases(cases_path):
        missing = _LEGACY_REQUIRED_FIELDS - payload.keys()
        if missing:
            raise ValueError(f"第 {payload.get('case_id')} 题缺少字段：{sorted(missing)}")
        case_id = str(payload["case_id"])
        if case_id in seen_ids:
            raise ValueError(f"case_id 重复：{case_id}")
        question_type = str(payload["question_type"])
        if question_type not in _LEGACY_TYPES:
            raise ValueError(f"不支持的问题类型：{question_type}")
        top_k = int(payload["top_k"])
        if not 1 <= top_k <= _LEGACY_MAX_TOP_K:
            raise ValueError(f"top_k 超出接口范围：{case_id}")
        if not str(payload["question"]).strip() or not str(payload["reference_answer"]).strip():
            raise ValueError(f"必要内容为空：{case_id}")
        if not payload["expected_facts"]:
            raise ValueError(f"必要内容为空：{case_id}")
        if question_type != "no_answer" and not payload["expected_source_location"]:
            raise ValueError(f"有答案用例缺少来源位置：{case_id}")
        seen_ids.add(case_id)
        cases.append(payload)

    reviewed = annotations["retrieval_cases"] + annotations["no_answer_cases"]
    by_id = {item["case_id"]: item for item in reviewed}
    if len(by_id) != len(reviewed) or set(by_id) != seen_ids:
        raise ValueError("题目与证据标注 ID 不一致")

    enriched: list[EvalCase] = []
    for payload in cases:
        case_id = str(payload["case_id"])
        item = by_id[case_id]
        if any(item[key] != payload[key] for key in ("question", "reference_answer", "top_k", "question_type")):
            raise ValueError(f"题目与证据版本不一致：{case_id}")
        expected_document = str(payload["expected_document"])
        answerable = payload["question_type"] != "no_answer"
        evidence = {key: annotations["evidence_units"][key] for key in item["required_evidence"]}
        if not answerable:
            if evidence or item["count_as_pass"] or item["include_in_retrieval_aggregate"]:
                raise ValueError("无答案题不得自动计为通过")
        elif not evidence:
            raise ValueError(f"缺少精确证据：{case_id}")
        enriched.append(
            EvalCase(
                schema_version=LEGACY_SCHEMA_VERSION,
                case_id=case_id,
                question=str(payload["question"]),
                reference_answer=str(payload["reference_answer"]),
                top_k=int(payload["top_k"]),
                answerable=answerable,
                units=tuple(_legacy_unit(key, unit, expected_document) for key, unit in evidence.items()),
                expected_documents=(expected_document,),
                forbidden_claims=tuple(payload["forbidden_facts"]),
                question_type=str(payload["question_type"]),
                expected_facts=tuple(payload["expected_facts"]),
                # 旧集只测检索侧两项；回答侧两项当时没有可比口径，不追溯补测。
                judge_metrics=RETRIEVAL_METRIC_KEYS,
            )
        )
    return enriched, assets


# --------------------------------------------------------------------------- #
# 面试八股集：多文档 + 语义判分
# --------------------------------------------------------------------------- #

_NOTES_SCHEMA_VERSION = "interview-notes-v1"
_NOTES_REQUIRED_FIELDS = {
    "schema_version",
    "case_id",
    "question",
    "reference_answer",
    "answerable",
    "answer_units",
    "expected_documents",
    "top_k",
    "question_type",
    "image_requirement",
}
_NOTES_IMAGE_REQUIREMENTS = {"none", "required", "associated"}


def _notes_unit(payload: dict, evidence_units: dict) -> AnswerUnit:
    unit_id = str(payload.get("unit_id") or "")
    claim = str(payload.get("claim") or "").strip()
    if not unit_id or not claim:
        raise ValueError("答案单元缺少 unit_id 或 claim")
    evidence_ids = list(payload.get("acceptable_evidence_ids") or [])
    if not evidence_ids:
        raise ValueError(f"答案单元没有可接受来源：{unit_id}")
    selectors: list[SourceSelector] = []
    for evidence_id in evidence_ids:
        if evidence_id not in evidence_units:
            raise ValueError(f"答案单元引用了不存在的证据：{unit_id} -> {evidence_id}")
        evidence = evidence_units[evidence_id]
        kind = str(evidence.get("kind") or "")
        if kind not in {TEXT_KIND, IMAGE_KIND}:
            raise ValueError(f"证据类型不支持：{evidence_id}")
        selectors.append(
            SourceSelector(
                document=str(evidence.get("document") or ""),
                kind=kind,
                locator=str(evidence.get("path") or "") if kind == IMAGE_KIND else None,
            )
        )
    return AnswerUnit(
        unit_id=unit_id,
        claim=claim,
        selectors=tuple(selectors),
        required_parts=1,
        acceptance=str(payload.get("acceptance") or ""),
    )


@register_loader(_NOTES_SCHEMA_VERSION)
def load_interview_notes_v1(cases_path: Path, dataset_dir: Path) -> tuple[list[EvalCase], list[CorpusAsset]]:
    evidence_units = json.loads(
        (dataset_dir / "evidence_annotations.json").read_text(encoding="utf-8")
    )["evidence_units"]
    manifest = json.loads((dataset_dir / "corpus_manifest.json").read_text(encoding="utf-8"))
    assets = [
        CorpusAsset(
            relative_path=str(item["path"]),
            sha256=str(item["sha256"]),
            kind=str(item.get("kind") or "document"),
        )
        for item in manifest["assets"]
    ]
    documents = {asset.relative_path for asset in assets if asset.kind == "document"}

    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for payload in iter_cases(cases_path):
        missing = _NOTES_REQUIRED_FIELDS - payload.keys()
        if missing:
            raise ValueError(f"第 {payload.get('case_id')} 题缺少字段：{sorted(missing)}")
        case_id = str(payload["case_id"])
        if case_id in seen_ids:
            raise ValueError(f"case_id 重复：{case_id}")
        seen_ids.add(case_id)
        if not str(payload["question"]).strip() or not str(payload["reference_answer"]).strip():
            raise ValueError(f"必要内容为空：{case_id}")
        top_k = int(payload["top_k"])
        if top_k < 1:
            raise ValueError(f"top_k 非法：{case_id}")
        question_type = str(payload["question_type"])
        answerable = bool(payload["answerable"])
        if answerable != (question_type != "no_answer"):
            raise ValueError(f"answerable 与 question_type 不一致：{case_id}")
        image_requirement = str(payload["image_requirement"])
        if image_requirement not in _NOTES_IMAGE_REQUIREMENTS:
            raise ValueError(f"图片要求不支持：{case_id} -> {image_requirement}")

        raw_units = list(payload["answer_units"] or [])
        expected_documents = tuple(str(name) for name in payload["expected_documents"] or [])
        unknown = [name for name in expected_documents if f"corpus/{name}" not in documents]
        if unknown:
            raise ValueError(f"预期文档不在语料清单里：{case_id} -> {unknown}")
        refusal_rubric = payload.get("refusal_rubric") or {}

        if not answerable:
            if raw_units or expected_documents:
                raise ValueError(f"无答案题不该声明答案单元或预期文档：{case_id}")
            if not refusal_rubric:
                raise ValueError(f"无答案题缺少拒答判据：{case_id}")
            units: tuple[AnswerUnit, ...] = ()
        else:
            if not expected_documents:
                raise ValueError(f"有答案题必须声明预期文档：{case_id}")
            if not raw_units:
                raise ValueError(f"有答案题必须声明答案单元：{case_id}")
            units = tuple(_notes_unit(unit, evidence_units) for unit in raw_units)
            unit_ids = [unit.unit_id for unit in units]
            if len(set(unit_ids)) != len(unit_ids):
                raise ValueError(f"答案单元 ID 重复：{case_id}")
            for unit in units:
                stray = sorted(
                    {
                        selector.document
                        for selector in unit.selectors
                        if selector.document and selector.document not in expected_documents
                    }
                )
                if stray:
                    raise ValueError(f"答案单元的证据落在预期文档之外：{case_id} -> {stray}")

        cases.append(
            EvalCase(
                schema_version=_NOTES_SCHEMA_VERSION,
                case_id=case_id,
                question=str(payload["question"]),
                reference_answer=str(payload["reference_answer"]),
                top_k=top_k,
                answerable=answerable,
                units=units,
                expected_documents=expected_documents,
                forbidden_claims=tuple(str(item) for item in payload["forbidden_claims"] or []),
                question_type=question_type,
                topic=str(payload.get("topic") or ""),
                category=str(payload.get("category") or ""),
                image_requirement=image_requirement,
                include_in_retrieval_aggregate=bool(payload.get("include_in_retrieval_aggregate", True)),
                include_in_answer_aggregate=bool(payload.get("include_in_answer_aggregate", True)),
                refusal_rubric=dict(refusal_rubric),
                notes=str(payload.get("notes") or ""),
                # 只声明检索侧两项。本集对应的业务接口是 `POST /api/ai/rag/query`，
                # 它**不生成回答**：返回的 `answer` 是把检索命中的片段拼成的上下文摘要
                # （产品侧 `build_answer_from_sources`，纯字符串拼装，不调模型），本来就
                # 是喂给下游 prompt 用的。所以 Faithfulness 恒真（回答本身就是上下文）、
                # Answer Relevancy 与 Contextual Relevancy 重复，两项在这条链路上都测不出
                # 信号。生成侧两项归面试链路（`interview_runner.py` 打真实生成的
                # `assistantReply`），45 道有答案题只做检索侧的计划样本。
                judge_metrics=RETRIEVAL_METRIC_KEYS,
            )
        )
    return cases, assets


_INTERVIEW_FORMAL_SCHEMA_VERSION = "interview-formal-v1"
_INTERVIEW_FORMAL_REQUIRED_FIELDS = {"case_id", "group", "request", "should_refuse"}
_INTERVIEW_FORMAL_USER_INPUT_MAX_CHARS = 240
_INTERVIEW_FORMAL_GROUPS = {"normal", "no_evidence"}


@register_loader(_INTERVIEW_FORMAL_SCHEMA_VERSION)
def load_interview_formal_v1(cases_path: Path, dataset_dir: Path) -> tuple[list[EvalCase], list[CorpusAsset]]:
    """面试生成侧评测集：每题一个 `request`（直接作为 turn/stream 请求体）。

    题目不是检索题：没有 answer_units / 证据标注，判分输入在**采集时**从 done 事件
    组装（`actual_output` = assistantReply，`retrieval_context` = sources）。loader 只
    负责 schema 校验与题数统计，供冻结生成器与自检使用；采集由 `interview_runner`
    按行直接驱动。语料与 interview-notes-v1 同源（指纹一致 ⇒ 常驻复用），由
    `corpus_manifest.json` 与冻结清单的 `files_sha256` 负责，不走 `verify_corpus`。
    """
    cases: list[EvalCase] = []
    seen_ids: set[str] = set()
    for payload in iter_cases(cases_path):
        missing = _INTERVIEW_FORMAL_REQUIRED_FIELDS - payload.keys()
        if missing:
            raise ValueError(f"第 {payload.get('case_id')} 题缺少字段：{sorted(missing)}")
        case_id = str(payload["case_id"])
        if case_id in seen_ids:
            raise ValueError(f"case_id 重复：{case_id}")
        seen_ids.add(case_id)
        group = str(payload["group"])
        if group not in _INTERVIEW_FORMAL_GROUPS:
            raise ValueError(f"分组合法性：{case_id} -> {group}")
        should_refuse = bool(payload["should_refuse"])
        if group == "no_evidence" and not should_refuse:
            raise ValueError(f"无依据题必须声明 should_refuse：{case_id}")
        if group == "normal" and should_refuse:
            raise ValueError(f"有依据题不应声明 should_refuse：{case_id}")
        request = payload["request"]
        if not isinstance(request, dict):
            raise ValueError(f"request 必须是对象：{case_id}")
        user_input = str(request.get("userInput") or "").strip()
        if not user_input:
            raise ValueError(f"userInput 为空：{case_id}")
        if len(user_input) > _INTERVIEW_FORMAL_USER_INPUT_MAX_CHARS:
            raise ValueError(
                f"userInput 超过 {_INTERVIEW_FORMAL_USER_INPUT_MAX_CHARS} 字（检索 query 截断线）：{case_id}"
            )
        reference_answer = str(payload.get("reference_answer") or "")
        if group == "normal" and not reference_answer:
            raise ValueError(f"有依据题缺少参考答案：{case_id}")
        cases.append(
            EvalCase(
                schema_version=_INTERVIEW_FORMAL_SCHEMA_VERSION,
                case_id=case_id,
                question=user_input,
                reference_answer=reference_answer,
                top_k=4,
                answerable=True,
                units=(),
                expected_documents=(),
                question_type=group,
                topic=str(payload.get("topic") or ""),
                # 生成侧两项归本链路；检索侧三项对本集无意义（不检索判分）。
                judge_metrics=("faithfulness", "answer_relevancy"),
                notes=str(payload.get("source_case_id") or ""),
            )
        )
    return cases, []
