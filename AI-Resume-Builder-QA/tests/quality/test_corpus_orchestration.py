"""语料编排契约：数据集声明的素材怎么变成一轮可上传的语料。

上半部分是**真实数据集**上跑的工厂契约（`testdata/quality/interview-notes-v1`），
下半部分用假上传客户端跑 `run_quality_evaluation` 的入库段。两者都完全离线。

⚠️ 真实语料只在本地保留（`.gitignore` 忽略 `testdata/quality/`，资料是第三方笔记与插图）
⇒ 依赖它的用例带 `requires_local_corpus`，语料缺失时**跳过**而不是失败。

为什么这些断言值得写死：

- **正文与附件必须同批、且附件路径要等于正文里的 Markdown 相对路径**。后端靠上传
  manifest 里的 `relativePath` 把附件挂到正文；路径写错不会有任何报错，只会让图片
  静默挂不上、图片题永久无解。
- **正文必须与数据集声明的 `sha256` 逐字节一致**。哈希是证据行号可信的前提。唯一一次
  有意的改写（Obsidian `![[...]]` → 常规 Markdown 链接，且文件名里的空格编码成 `%20`）
  发生在**冻结生成时**并已记入 `corpus_manifest.json` 的 `transformations`；运行期再做
  任何「顺手修一下 Markdown」都会让落盘内容与清单失配。
- **每一个附件都要有归属**：要么被正文引用，要么被数据集点名为已知的未引用项。
  差额对不上说明语料或正文被改过，而这件事不会体现在任何一项指标上。
"""

import hashlib
import os
import tempfile
from pathlib import Path
from uuid import uuid4

import pytest

from clients.rag import UploadAsset
from quality.corpus import corpus_for, expected_unreferenced_for, supported_corpus_schemas
from quality.loaders import CaseSet, load_case_set
from quality.models import (
    LEGACY_SCHEMA_VERSION,
    TEXT_KIND,
    AnswerUnit,
    CorpusAsset,
    EvalCase,
    QualityCorpus,
    SourceSelector,
)
from quality.resident_corpus import corpus_token, find_reusable_documents
from quality.runner import run_quality_evaluation

DATASET = Path(__file__).resolve().parents[2] / "testdata" / "quality" / "interview-notes-v1"
NOTES_SCHEMA = "interview-notes-v1"
DOCUMENT_NAMES = ("1-测试.md", "3-MySQL.md", "5-Redis.md", "6-计算机网络.md", "9-AI.md")

# 语料只在本地保留（`.gitignore` 忽略 testdata/quality/）：资料是第三方笔记与插图。
# 所以依赖真实语料的用例必须在语料缺失时**跳过**而不是失败——「语料不在」说明这台机器
# 没有本地语料，不说明实现坏了。其余用例现场构造数据集/语料，任何机器都能跑。
requires_local_corpus = pytest.mark.skipif(
    not DATASET.is_dir(),
    reason=f"{DATASET.name} 语料只在本地保留、不入库；跳过依赖真实语料的契约用例",
)


def _notes_case_set(split: str = "dev") -> CaseSet:
    return load_case_set(DATASET, split)


@requires_local_corpus
def test_notes_corpus_puts_documents_and_attachments_in_one_batch() -> None:
    """69 项素材一次上传：5 篇正文 + 64 张附件，附件相对路径按正文里的写法给。"""
    case_set = _notes_case_set()
    with tempfile.TemporaryDirectory() as temp:
        corpus = corpus_for(case_set, Path(temp), "runx")

    documents = [asset for asset in corpus.primary_assets if asset.role != "attachment"]
    assert [asset.path.name for asset in documents] == [f"qa-rag-runx-{name}" for name in DOCUMENT_NAMES]
    assert documents[0].path.name.endswith(DOCUMENT_NAMES[0]), "数据集用 endswith 匹配预期文档"
    assert len(corpus.attachment_assets) == 64
    assert corpus.document_file_names == [asset.path.name for asset in documents]
    # 5 篇正文彼此就是干扰，不再需要额外的干扰文档。
    assert corpus.noise_assets == []
    assert corpus.noise_file_names == []

    # 附件的 relativePath 必须等于正文里的 Markdown 相对路径（`附件/<原名>`），
    # 而不是数据集相对路径（`corpus/附件/<原名>`）。
    for asset in corpus.attachment_assets:
        assert asset.relative_path.startswith("附件/")
        assert "/" not in asset.relative_path.removeprefix("附件/")
        assert asset.content_type == "image/png"


@requires_local_corpus
def test_notes_corpus_copies_assets_byte_for_byte() -> None:
    """落盘素材的 sha256 必须等于数据集声明的哈希：证据行号以此为前提。"""
    case_set = _notes_case_set()
    declared = {
        Path(asset.relative_path).name: (asset.relative_path, asset.sha256) for asset in case_set.assets
    }
    assert len(declared) == 69
    with tempfile.TemporaryDirectory() as temp:
        corpus = corpus_for(case_set, Path(temp), "runx")
        for asset in corpus.primary_assets:
            # 正文落盘时加了 run 前缀，附件保留原名：两种都要能对回数据集声明。
            original = asset.path.name.removeprefix("qa-rag-runx-")
            relative, expected = declared[original]
            assert hashlib.sha256(asset.path.read_bytes()).hexdigest() == expected, relative


def test_unregistered_schema_is_rejected_before_upload(tmp_path: Path) -> None:
    """未接线的 schema 在**上传前**拒绝，而不是把语料按错的形状传进知识库。"""
    case_set = CaseSet(
        schema_version="not-wired-v9",
        dataset_dir=tmp_path,
        cases_path=tmp_path / "cases.jsonl",
        cases=(),
    )
    with pytest.raises(ValueError, match="语料编排尚未接线"):
        corpus_for(case_set, tmp_path, "runx")
    assert NOTES_SCHEMA in supported_corpus_schemas()
    assert LEGACY_SCHEMA_VERSION in supported_corpus_schemas()


@requires_local_corpus
def test_unreferenced_attachments_are_declared_not_tolerated() -> None:
    r"""未引用附件只能「点名声明」，不能静默容忍。

    `interview-notes-v1` 声明的是**空集**：冻结生成时已把 `6-计算机网络.md` 第 74 行的
    Obsidian 链接归一化成常规 Markdown 写法（空格编码为 `%20`，否则会被后端
    `[^\s)\r\n]+` 截断），64 张附件全部有正文归属，多一张少一张都算语料漂移。声明位保留，
    供将来真出现「挂不上的附件」时点名。
    """
    assert expected_unreferenced_for(_notes_case_set()) == ()
    legacy = CaseSet(
        schema_version=LEGACY_SCHEMA_VERSION, dataset_dir=DATASET, cases_path=DATASET, cases=()
    )
    assert expected_unreferenced_for(legacy) == ()


def test_declared_unreferenced_exception_must_exist_in_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """点名了就必须真在素材里：点名项消失说明「已知例外」已失效，要当场报错。"""
    monkeypatch.setattr("quality.corpus._NOTES_UNREFERENCED_ATTACHMENTS", ("附件/orphan.png",))

    def case_set(*names: str) -> CaseSet:
        return CaseSet(
            schema_version=NOTES_SCHEMA,
            dataset_dir=tmp_path,
            cases_path=tmp_path / "cases.jsonl",
            cases=(),
            assets=tuple(
                CorpusAsset(relative_path=f"corpus/附件/{name}", sha256="0" * 64, kind="image")
                for name in names
            ),
        )

    assert expected_unreferenced_for(case_set("orphan.png")) == ("附件/orphan.png",)
    with pytest.raises(ValueError, match="未引用附件已不在数据集素材里"):
        expected_unreferenced_for(case_set("other.png"))


def test_declared_hash_is_enforced(tmp_path: Path) -> None:
    """数据集声明哈希与文件内容不符时，工厂必须拒绝，不能带着坏语料上传。"""
    dataset = tmp_path / "ds" / "corpus"
    dataset.mkdir(parents=True)
    (dataset / "a.md").write_text("# 正文\n", encoding="utf-8")
    case_set = CaseSet(
        schema_version=NOTES_SCHEMA,
        dataset_dir=tmp_path / "ds",
        cases_path=tmp_path / "ds" / "cases.jsonl",
        cases=(),
        assets=(CorpusAsset(relative_path="corpus/a.md", sha256="0" * 64, kind="document"),),
    )
    with pytest.raises(ValueError, match="语料已变化"):
        corpus_for(case_set, tmp_path / "temp", "runx")


def _text_case() -> EvalCase:
    """正则单元：`matcher_for_cases` 会选离线的正则匹配器，整条链路不碰模型。"""
    return EvalCase(
        schema_version=NOTES_SCHEMA,
        case_id="Q-001",
        question="MySQL 的索引为什么用 B+ 树？",
        reference_answer="因为范围查询与磁盘 IO。",
        top_k=4,
        answerable=True,
        units=(
            AnswerUnit(
                unit_id="u1",
                claim="范围查询友好",
                selectors=(SourceSelector(document="1-测试.md", kind=TEXT_KIND),),
                required_parts=1,
                patterns=(("范围",),),
            ),
        ),
        expected_documents=("1-测试.md",),
        question_type="single",
    )


def _corpus(*, unreferenced: tuple[str, ...] = ("附件/orphan.png",)) -> QualityCorpus:
    """2 篇正文 + 4 张附件的人工语料，避免测试依赖数据集的具体规模。"""
    documents = ["qa-rag-tc-1.md", "qa-rag-tc-2.md"]
    assets = [UploadAsset(Path(name), "text/markdown", name) for name in documents]
    assets.extend(
        UploadAsset(Path(f"{index}.png"), "image/png", f"附件/{index}.png", role="attachment")
        for index in range(4)
    )
    return QualityCorpus(
        primary_assets=assets,
        noise_assets=[],
        document_file_names=documents,
        noise_file_names=[],
        expected_unreferenced_attachments=unreferenced,
    )


class _FakeRagClient:
    """假上传客户端：逐篇回传 `file_name` / `document_id` 与图片统计，不发任何请求。"""

    def __init__(self, *, per_document: tuple[int, ...] = (2, 1), missing_on: str | None = None) -> None:
        #: 逐篇的图片引用数；默认合计 3，比 4 张附件少 1，与 `_corpus` 的声明差额对齐。
        self.per_document = per_document
        self.missing_on = missing_on
        self.batches: list[list[UploadAsset]] = []
        self.polls: list[str] = []

    async def upload_stream(self, assets: list[UploadAsset]) -> list[dict]:
        self.batches.append(list(assets))
        events: list[dict] = []
        index = 0
        for asset in assets:
            if asset.role == "attachment":
                continue
            missing = 1 if asset.relative_path == self.missing_on else 0
            referenced = self.per_document[index] + missing
            events.append(
                {
                    "event": "file-result",
                    "result": {
                        "status": "success",
                        "document_id": f"doc-{asset.relative_path}",
                        "file_name": asset.relative_path,
                        "referenced_image_count": referenced,
                        "matched_image_count": self.per_document[index],
                        "missing_image_count": missing,
                    },
                }
            )
            index += 1
        events.append({"event": "batch-complete"})
        return events

    async def poll_image_enrichment(self, document_id: str, _timeout: float, _interval: float) -> dict:
        self.polls.append(document_id)
        return {"status": "completed", "failedCount": 0}

    async def query(self, _question: str, _top_k: int) -> dict:
        return {"answer": "", "sources": []}


class _LibraryClient:
    """带可变库状态的假客户端：只实现常驻语料核对需要的两个方法。"""

    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents
        self.deleted: list[str] = []

    async def list_all_documents(self) -> list[dict]:
        return [item for item in self.documents if item["documentId"] not in self.deleted]

    async def delete_document(self, document_id: str) -> dict:
        self.deleted.append(document_id)
        return {"status": "deleted"}


def _resident_case_set(tmp_path: Path, names: tuple[str, ...]) -> CaseSet:
    """带素材指纹的数据集：指纹由素材 (relative_path, sha256) 决定，不依赖真实语料。"""
    return CaseSet(
        schema_version=NOTES_SCHEMA,
        dataset_dir=tmp_path,
        cases_path=tmp_path / "cases.jsonl",
        cases=(),
        assets=tuple(
            CorpusAsset(relative_path=f"corpus/{name}", sha256="0" * 64, kind="document")
            for name in names
        ),
    )


def _resident_corpus_for(case_set: CaseSet) -> QualityCorpus:
    token = corpus_token(case_set)
    file_names = [f"qa-rag-{token}-{name}" for name in ("1-测试.md", "5-Redis.md")]
    return QualityCorpus(
        primary_assets=[UploadAsset(Path(name), "text/markdown", name) for name in file_names],
        noise_assets=[],
        document_file_names=file_names,
        noise_file_names=[],
        expected_unreferenced_attachments=(),
    )


def _library_item(token: str, name: str, document_id: str, status: str = "ready") -> dict:
    return {
        "documentId": document_id,
        "fileName": f"qa-rag-{token}-{name}",
        "status": status,
    }


@pytest.mark.asyncio
async def test_resident_corpus_is_reused_when_library_matches(tmp_path: Path) -> None:
    """指纹前缀的正文齐且全部 ready ⇒ 复用：返回映射、不删任何东西。"""
    case_set = _resident_case_set(tmp_path, ("1-测试.md", "5-Redis.md"))
    corpus = _resident_corpus_for(case_set)
    token = corpus_token(case_set)
    client = _LibraryClient([
        _library_item(token, "1-测试.md", "doc-1"),
        _library_item(token, "5-Redis.md", "doc-2"),
    ])

    reusable = await find_reusable_documents(client, case_set, corpus)

    assert reusable == {"qa-rag-{0}-1-测试.md".format(token): "doc-1",
                        "qa-rag-{0}-5-Redis.md".format(token): "doc-2"}
    assert client.deleted == []


@pytest.mark.asyncio
async def test_incomplete_resident_set_is_purged_and_reimported(tmp_path: Path) -> None:
    """同指纹但只剩半套（正文被单独删过）⇒ 清掉半套、返回 None 走重导。"""
    case_set = _resident_case_set(tmp_path, ("1-测试.md", "5-Redis.md"))
    corpus = _resident_corpus_for(case_set)
    token = corpus_token(case_set)
    client = _LibraryClient([_library_item(token, "1-测试.md", "doc-1")])

    reusable = await find_reusable_documents(client, case_set, corpus)

    assert reusable is None
    assert client.deleted == ["doc-1"]


@pytest.mark.asyncio
async def test_stale_fingerprint_documents_are_purged(tmp_path: Path) -> None:
    """语料换版后旧指纹前缀的文档不再是任何声明 ⇒ 清掉，新指纹照常复用。"""
    case_set = _resident_case_set(tmp_path, ("1-测试.md", "5-Redis.md"))
    corpus = _resident_corpus_for(case_set)
    token = corpus_token(case_set)
    client = _LibraryClient([
        _library_item("resident-OLD-fingerprint", "1-测试.md", "doc-old"),
        _library_item(token, "1-测试.md", "doc-1"),
        _library_item(token, "5-Redis.md", "doc-2"),
    ])

    reusable = await find_reusable_documents(client, case_set, corpus)

    assert reusable is not None and len(reusable) == 2
    assert client.deleted == ["doc-old"]


@pytest.mark.asyncio
async def test_non_resident_documents_are_left_alone(tmp_path: Path) -> None:
    """run 前缀的普通测试数据不归常驻管理：既不算复用依据，也绝不能被顺手删掉。"""
    case_set = _resident_case_set(tmp_path, ("1-测试.md", "5-Redis.md"))
    corpus = _resident_corpus_for(case_set)
    token = corpus_token(case_set)
    client = _LibraryClient([
        {"documentId": "doc-run", "fileName": "qa-rag-notes-v1-20260917i-1-测试.md", "status": "ready"},
        _library_item(token, "1-测试.md", "doc-1"),
        _library_item(token, "5-Redis.md", "doc-2"),
    ])

    reusable = await find_reusable_documents(client, case_set, corpus)

    assert reusable is not None and len(reusable) == 2
    assert client.deleted == []


def _remove_tree(root: Path) -> None:
    """本机回收站通道不可靠，逐项删除自己造的报告目录。"""
    if not root.exists():
        return
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.remove(Path(current) / name)
        for name in directories:
            os.rmdir(Path(current) / name)
    try:
        os.rmdir(root)
    except OSError:
        pass


async def _run_corpus_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corpus: QualityCorpus,
    client: _FakeRagClient,
) -> list:
    """只驱动入库段：加载器与语料工厂都换成桩，判分走离线的正则匹配器。"""
    (tmp_path / "cases.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "evidence_annotations.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "quality.runner.load_case_set",
        lambda *_args, **_kwargs: CaseSet(
            schema_version=NOTES_SCHEMA,
            dataset_dir=tmp_path,
            cases_path=tmp_path / "cases.jsonl",
            cases=(_text_case(),),
        ),
    )
    monkeypatch.setattr("quality.runner.corpus_for", lambda *_args, **_kwargs: corpus)
    monkeypatch.setattr("quality.runner.write_reports", lambda *_args, **_kwargs: None)
    run_id = "tc-" + uuid4().hex[:8]
    try:
        results = await run_quality_evaluation(
            client,  # type: ignore[arg-type]
            run_id,
            tmp_path,
            1,
            0.01,
            False,
            "localhost",
        )
    finally:
        _remove_tree(Path(__file__).resolve().parents[2] / "reports" / "quality" / run_id)
    return results


@pytest.mark.asyncio
async def test_every_document_is_verified_and_enriched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """每篇正文都要经后端逐篇回传核验，有图片的逐篇轮询解析。"""
    corpus = _corpus()
    client = _FakeRagClient()
    results = await _run_corpus_stage(tmp_path, monkeypatch, corpus, client)

    # 上传后的题目按「语料准备失败」以外的方式继续跑（这里查询返回空命中）。
    assert results[0].evaluation_status in {"failed", "not_applicable"}
    assert client.polls == [f"doc-{name}" for name in corpus.document_file_names]
    # 正文与附件同批上传，不允许拆成两批。
    assert len(client.batches) == 1
    assert len(client.batches[0]) == len(corpus.primary_assets)


@pytest.mark.asyncio
async def test_missing_image_reference_stops_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """有引用找不到附件时必须当场失败：相关题目会变成永久无解。"""
    client = _FakeRagClient(missing_on="qa-rag-tc-2.md")
    results = await _run_corpus_stage(tmp_path, monkeypatch, _corpus(), client)

    # 走「上传阶段失败」分支：不判通过，失败原因只留异常类型（不泄漏原始正文）。
    assert [result.passed for result in results] == [False]
    assert results[0].failure_reasons == ["AssertionError"]
    # 按序处理：第一篇已经轮询过，发现缺引用的第二篇必须中止，不再往下传。
    assert client.polls == ["doc-qa-rag-tc-1.md"]


@pytest.mark.asyncio
async def test_attachment_surplus_must_match_the_declared_exceptions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """附件数与「被引用数 + 声明的未引用数」不符时必须失败。

    4 张附件、报出 4 次引用 ⇒ 实际差额 0，而语料声明了 1 张未引用（正常应差 1）。
    这类漂移不会体现在任何一项指标上，只能在这里拦住。
    """
    client = _FakeRagClient(per_document=(2, 2))
    results = await _run_corpus_stage(tmp_path, monkeypatch, _corpus(), client)
    assert results[0].failure_reasons == ["AssertionError"]
