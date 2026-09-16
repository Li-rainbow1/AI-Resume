"""数据集加载与 schema 分发的契约测试。

三件事各自钉住：
1. `evidence-v3`（旧集）仍然可加载、可评分——旧数据集文件已不在本机，所以这里
   现场造一个最小同构数据集，避免「旧集可跑」只是口头承诺。
2. `interview-notes-v1`（面试八股集）按多文档 + 答案单元 + 语义判分加载。
3. 标注与语料出问题时必须在加载阶段就报错，不能带着错标注去评分。
"""

import hashlib
import json
from pathlib import Path

import pytest

from quality.loaders import (
    available_splits,
    detect_schema_version,
    load_case_set,
    resolve_cases_path,
    supported_schemas,
    verify_corpus,
)
from quality.metrics import evaluate_case
from quality.models import (
    IMAGE_KIND,
    LEGACY_SCHEMA_VERSION,
    TEXT_KIND,
    CorpusAsset,
    SourceSelector,
)

QA_ROOT = Path(__file__).resolve().parents[2]
NOTES_DIR = QA_ROOT / "testdata" / "quality" / "interview-notes-v1"
NOTES_SCHEMA = "interview-notes-v1"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )


def _legacy_case(case_id: str, question_type: str, answer: str, location: list[dict]) -> dict:
    return {
        "case_id": case_id,
        "question": f"{case_id} 的问题",
        "reference_answer": answer,
        "expected_document": "quality-corpus.md",
        "expected_source_location": location,
        "expected_facts": [answer],
        "forbidden_facts": [],
        "question_type": question_type,
        "top_k": 4,
    }


def write_legacy_dataset(root: Path) -> Path:
    """造一个最小的 evidence-v3 数据集，结构与已灭失的 golden_dataset.jsonl 同构。"""
    corpus = root / "corpus"
    (corpus / "assets").mkdir(parents=True)
    document = corpus / "quality-corpus.md"
    document.write_text("受理 -> 审核 -> 批准\n访问码 LIME-482\n", encoding="utf-8")
    badge = corpus / "assets" / "quality-ocr-badge.png"
    badge.write_bytes(b"fake-png-bytes")

    text_case = _legacy_case("TXT-001", "text", "LIME-482", [{"ingestSource": "text_document"}])
    image_case = _legacy_case("IMG-001", "image_ocr", "LIME-482",
                              [{"imageLocator": "quality-ocr-badge.png"}])
    # 无答案题不得声明来源位置，否则加载器会判定「无答案题被算作通过」。
    no_answer = _legacy_case("NA-001", "no_answer", "未提供", [])
    cases_path = root / "golden_dataset.jsonl"
    _write_jsonl(cases_path, [text_case, image_case, no_answer])
    _write_json(
        root / "evidence_annotations.json",
        {
            "corpus_manifest": [
                {"path": "corpus/quality-corpus.md", "sha256": _sha256(document)},
                {"path": "corpus/assets/quality-ocr-badge.png", "sha256": _sha256(badge)},
            ],
            "evidence_units": {
                "T-1": {"kind": "text", "match_patterns": [["lime-482"]]},
                "I-1": {"kind": "image", "asset": "assets/quality-ocr-badge.png",
                        "match_patterns": [["lime-482"]]},
            },
            "retrieval_cases": [
                {**text_case, "required_evidence": ["T-1"], "count_as_pass": True,
                 "include_in_retrieval_aggregate": True},
                {**image_case, "required_evidence": ["I-1"], "count_as_pass": True,
                 "include_in_retrieval_aggregate": True},
            ],
            "no_answer_cases": [
                {**no_answer, "required_evidence": [], "count_as_pass": False,
                 "include_in_retrieval_aggregate": False},
            ],
        },
    )
    return cases_path


def write_notes_dataset(root: Path, *, mutate: dict | None = None) -> Path:
    """造一个最小的 interview-notes-v1 数据集，两篇文档 + 两类证据。"""
    corpus = root / "corpus"
    (corpus / "附件").mkdir(parents=True)
    documents = {}
    for name in ("1-笔记.md", "2-其他.md"):
        path = corpus / name
        path.write_text(f"{name} 的正文。\n", encoding="utf-8")
        documents[name] = path
    image = corpus / "附件" / "a.png"
    image.write_bytes(b"fake-png-bytes")
    _write_json(
        root / "corpus_manifest.json",
        {
            "dataset_version": NOTES_SCHEMA,
            "assets": [
                *({"path": f"corpus/{name}", "kind": "document", "sha256": _sha256(path)}
                  for name, path in documents.items()),
                {"path": "corpus/附件/a.png", "kind": "attachment", "sha256": _sha256(image)},
            ],
        },
    )
    _write_json(
        root / "evidence_annotations.json",
        {
            "schema_version": NOTES_SCHEMA,
            "evidence_units": {
                "T-L10-12": {"evidence_id": "T-L10-12", "document": "1-笔记.md", "kind": TEXT_KIND,
                             "path": "corpus/1-笔记.md", "line_start": 10, "line_end": 12, "quote": "正文"},
                "T-IMG-L20": {"evidence_id": "T-IMG-L20", "document": "1-笔记.md", "kind": IMAGE_KIND,
                              "path": "corpus/附件/a.png", "reference_transcription": "图里写了 A"},
                "T2-L5-6": {"evidence_id": "T2-L5-6", "document": "2-其他.md", "kind": TEXT_KIND,
                            "path": "corpus/2-其他.md", "line_start": 5, "line_end": 6, "quote": "其他正文"},
            },
        },
    )
    answerable = {
        "schema_version": NOTES_SCHEMA,
        "case_id": "INTN-F-001",
        "question": "正文说了什么？",
        "reference_answer": "说了 A 和 B。",
        "answerable": True,
        "question_type": "text",
        "answer_units": [
            {"unit_id": "INTN-F-001-U01", "claim": "正文说了 A",
             "acceptable_evidence_ids": ["T-L10-12"], "acceptance": "语义等价即可"},
            {"unit_id": "INTN-F-001-U02", "claim": "图里写了 A",
             "acceptable_evidence_ids": ["T-IMG-L20"], "acceptance": "语义等价即可"},
        ],
        "expected_documents": ["1-笔记.md"],
        "required_evidence_ids": ["T-L10-12", "T-IMG-L20"],
        "forbidden_claims": ["说了 C"],
        "top_k": 4,
        "image_requirement": "required",
        "include_in_retrieval_aggregate": True,
        "include_in_answer_aggregate": True,
        "annotation_status": "test",
    }
    no_answer = {
        "schema_version": NOTES_SCHEMA,
        "case_id": "INTN-F-048",
        "question": "生产环境部署了几个节点？",
        "reference_answer": "资料没有提供。",
        "answerable": False,
        "question_type": "no_answer",
        "answer_units": [],
        "expected_documents": [],
        "required_evidence_ids": [],
        "forbidden_claims": ["凭空给出节点数"],
        "top_k": 4,
        "image_requirement": "none",
        "include_in_retrieval_aggregate": False,
        "include_in_answer_aggregate": False,
        "refusal_rubric": {"pass": ["明确说明资料未提供"], "fail": ["凭空给出具体信息"],
                           "review": ["仅凭拒答关键词不能判分"]},
    }
    rows = [answerable, no_answer]
    for case_id, changes in (mutate or {}).items():
        for row in rows:
            if row["case_id"] == case_id:
                row.update(changes)
    _write_jsonl(root / "formal.jsonl", rows)
    return root / "formal.jsonl"


# --------------------------------------------------------------------------- #
# 分发
# --------------------------------------------------------------------------- #


def test_schema_registry_covers_both_generations() -> None:
    assert set(supported_schemas()) >= {LEGACY_SCHEMA_VERSION, NOTES_SCHEMA}


def test_detect_schema_version_falls_back_to_legacy(tmp_path: Path) -> None:
    cases_path = write_legacy_dataset(tmp_path)
    assert detect_schema_version(cases_path) == LEGACY_SCHEMA_VERSION
    # 注释行与空行不参与判定。
    cases_path.write_text("# 注释\n\n" + cases_path.read_text(encoding="utf-8"), encoding="utf-8")
    assert detect_schema_version(cases_path) == LEGACY_SCHEMA_VERSION


def test_unknown_schema_is_rejected_instead_of_guessed(tmp_path: Path) -> None:
    unknown = tmp_path / "cases.jsonl"
    _write_jsonl(unknown, [{"schema_version": "unknown-v9", "case_id": "X-1"}])
    with pytest.raises(ValueError, match="没有注册"):
        load_case_set(unknown)


def test_split_resolution_reports_choices(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "formal.jsonl", [{"schema_version": NOTES_SCHEMA}])
    _write_jsonl(tmp_path / "dev.jsonl", [{"schema_version": NOTES_SCHEMA}])
    # DeepEval 的 golden 导出不是独立拆分，不能算进来。
    _write_jsonl(tmp_path / "deepeval-formal-goldens.jsonl", [{"input": "x"}])
    assert set(available_splits(tmp_path)) == {"formal", "dev"}
    assert resolve_cases_path(tmp_path)[1].name == "formal.jsonl"
    assert resolve_cases_path(tmp_path, "dev")[1].name == "dev.jsonl"
    with pytest.raises(ValueError, match="没有名为 missing 的拆分"):
        resolve_cases_path(tmp_path, "missing")


# --------------------------------------------------------------------------- #
# 旧集（evidence-v3）仍可跑
# --------------------------------------------------------------------------- #


def test_legacy_dataset_still_loads_and_scores(tmp_path: Path) -> None:
    cases_path = write_legacy_dataset(tmp_path)
    case_set = load_case_set(cases_path)
    assert case_set.schema_version == LEGACY_SCHEMA_VERSION
    assert [case.case_id for case in case_set.cases] == ["TXT-001", "IMG-001", "NA-001"]
    assert len(case_set.assets) == 2

    text_case, image_case, no_answer = case_set.cases
    # 旧集每个正则组变成一个必须命中的子项，全部命中也只算一个答案单元。
    assert [unit.required_parts for unit in text_case.units] == [1]
    assert text_case.units[0].patterns == (("lime-482",),)
    assert text_case.units[0].selectors == (
        SourceSelector(document="quality-corpus.md", kind=TEXT_KIND, locator=None),
    )
    assert image_case.units[0].selectors == (
        SourceSelector(document="quality-corpus.md", kind=IMAGE_KIND,
                       locator="assets/quality-ocr-badge.png"),
    )
    assert no_answer.answerable is False and no_answer.units == ()
    # 旧集只测检索侧两项，回答侧两项当时没有可比口径。
    assert text_case.judge_metrics == ("contextual_recall", "contextual_relevancy")

    source = {"content": "访问码 LIME-482", "metadata": {
        "originalFilename": "qa-rag-run-x-quality-corpus.md", "ingestSource": "text_document"}}
    assert evaluate_case(text_case, "", [source]) == {
        "recall_at_k": 1.0, "precision_at_k": 1 / 4, "precision_at_returned": 1.0, "mrr": 1.0,
    }


def test_corpus_drift_is_rejected(tmp_path: Path) -> None:
    cases_path = write_legacy_dataset(tmp_path)
    (tmp_path / "corpus" / "quality-corpus.md").write_text("被改过的正文", encoding="utf-8")
    with pytest.raises(ValueError, match="语料已变化"):
        load_case_set(cases_path)


def test_corpus_path_must_stay_inside_dataset(tmp_path: Path) -> None:
    write_legacy_dataset(tmp_path)
    with pytest.raises(ValueError, match="路径越界"):
        verify_corpus(tmp_path, [CorpusAsset(relative_path="../outside.md", sha256="0" * 64)])


def test_grading_annotations_must_match_question_file(tmp_path: Path) -> None:
    cases_path = write_legacy_dataset(tmp_path)
    annotations_path = tmp_path / "evidence_annotations.json"
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    annotations["evidence_units"]["T-1"]["match_patterns"] = []
    _write_json(annotations_path, annotations)
    with pytest.raises(ValueError, match="缺少匹配规则"):
        load_case_set(cases_path)


def test_missing_corpus_file_is_reported(tmp_path: Path) -> None:
    cases_path = write_legacy_dataset(tmp_path)
    (tmp_path / "corpus" / "assets" / "quality-ocr-badge.png").unlink()
    with pytest.raises(ValueError, match="语料文件缺失"):
        load_case_set(cases_path)


# --------------------------------------------------------------------------- #
# 面试八股集（interview-notes-v1）
# --------------------------------------------------------------------------- #


def test_notes_dataset_loads_units_and_selectors(tmp_path: Path) -> None:
    cases_path = write_notes_dataset(tmp_path)
    case_set = load_case_set(cases_path)
    assert case_set.schema_version == NOTES_SCHEMA
    answerable, no_answer = case_set.cases
    assert [unit.unit_id for unit in answerable.units] == ["INTN-F-001-U01", "INTN-F-001-U02"]
    # 语义单元没有正则，判分只能交给语义判分器。
    assert all(unit.uses_semantics for unit in answerable.units)
    assert all(unit.required_parts == 1 for unit in answerable.units)
    assert answerable.units[1].selectors[0].kind == IMAGE_KIND
    assert answerable.units[1].selectors[0].locator == "corpus/附件/a.png"
    assert answerable.judge_metrics == (
        "contextual_recall", "contextual_relevancy", "faithfulness", "answer_relevancy"
    )
    assert no_answer.answerable is False and no_answer.units == ()
    assert no_answer.refusal_rubric["pass"] == ["明确说明资料未提供"]


def test_notes_dataset_rejects_unknown_evidence_id(tmp_path: Path) -> None:
    write_notes_dataset(tmp_path, mutate={"INTN-F-001": {"answer_units": [
        {"unit_id": "U01", "claim": "正文说了 A", "acceptable_evidence_ids": ["MISSING"]}]}})
    with pytest.raises(ValueError, match="不存在的证据"):
        load_case_set(tmp_path / "formal.jsonl")


def test_notes_dataset_rejects_answerable_without_units(tmp_path: Path) -> None:
    write_notes_dataset(tmp_path, mutate={"INTN-F-001": {"answer_units": []}})
    with pytest.raises(ValueError, match="必须声明答案单元"):
        load_case_set(tmp_path / "formal.jsonl")


def test_notes_dataset_rejects_cross_document_leak(tmp_path: Path) -> None:
    """答案单元的证据落在预期文档之外：这会不知不觉把别的文档算成正确答案。"""
    write_notes_dataset(tmp_path, mutate={"INTN-F-001": {"answer_units": [
        {"unit_id": "U01", "claim": "其他地方也讲了", "acceptable_evidence_ids": ["T2-L5-6"]}]}})
    with pytest.raises(ValueError, match="证据落在预期文档之外"):
        load_case_set(tmp_path / "formal.jsonl")


def test_notes_dataset_rejects_unlisted_expected_document(tmp_path: Path) -> None:
    write_notes_dataset(tmp_path, mutate={"INTN-F-001": {"expected_documents": ["3-MySQL.md", "1-笔记.md"]}})
    with pytest.raises(ValueError, match="不在语料清单里"):
        load_case_set(tmp_path / "formal.jsonl")


def test_notes_dataset_rejects_no_answer_with_units(tmp_path: Path) -> None:
    write_notes_dataset(tmp_path, mutate={"INTN-F-048": {"expected_documents": ["1-笔记.md"]}})
    with pytest.raises(ValueError, match="不该声明答案单元或预期文档"):
        load_case_set(tmp_path / "formal.jsonl")


def test_notes_dataset_rejects_answerable_flag_mismatch(tmp_path: Path) -> None:
    write_notes_dataset(tmp_path, mutate={"INTN-F-048": {"answerable": True}})
    with pytest.raises(ValueError, match="answerable 与 question_type 不一致"):
        load_case_set(tmp_path / "formal.jsonl")


@pytest.mark.skipif(not NOTES_DIR.exists(), reason="面试八股集不在本机")
def test_real_interview_notes_dataset_matches_its_validation_report() -> None:
    """真实数据集：结构、跨文档题、无答案题与语料哈希都要与说明一致。"""
    case_set = load_case_set(NOTES_DIR, "formal")
    assert case_set.schema_version == NOTES_SCHEMA
    assert len(case_set.cases) == 50
    assert len(load_case_set(NOTES_DIR, "dev").cases) == 10
    # load_case_set 内部逐个校验 69 份语料的 SHA-256，能加载即代表哈希全对。
    assert len(case_set.assets) == 69
    assert sum(asset.kind == "document" for asset in case_set.assets) == 5
    assert sum(asset.kind == "attachment" for asset in case_set.assets) == 64

    by_id = {case.case_id: case for case in case_set.cases}
    answerable = by_id["INTN-F-001"]
    assert len(answerable.units) == 2
    assert answerable.units[0].selectors[0].document == "1-测试.md"
    assert "需求覆盖矩阵" in answerable.units[0].claim
    # 跨文档题：答案单元落在两篇及以上预期文档上，单文档结构表达不出来。
    assert by_id["INTN-F-032"].covers_every_document is True
    assert by_id["INTN-F-032"].expected_documents == ("1-测试.md", "3-MySQL.md")
    image_unit = by_id["INTN-F-003"].units[0].selectors[0]
    assert image_unit.kind == IMAGE_KIND and str(image_unit.locator).endswith(".png")
    no_answer = by_id["INTN-F-048"]
    assert no_answer.answerable is False and no_answer.units == ()
    assert no_answer.expected_documents == () and no_answer.refusal_rubric
    # 正式集 45 道有答案题参与聚合，无答案 5 题单列。
    assert sum(case.answerable for case in case_set.cases) == 45
    assert sum(case.include_in_answer_aggregate for case in case_set.cases) == 45
