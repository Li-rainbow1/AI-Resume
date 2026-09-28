"""评测题目、证据标注与运行结果。RAG 评分使用冻结片段 ID，答案单元用于数据集描述。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # 只在类型检查期引入：`clients.rag` 依赖 httpx，而本模块要保持「纯结构、
    # 可被离线判分链路导入」。
    from clients.rag import UploadAsset

NOTES_SCHEMA_VERSION = "interview-notes-v1"

ANY_KIND = "any"
TEXT_KIND = "text"
IMAGE_KIND = "image"


@dataclass(frozen=True)
class SourceSelector:
    """一个答案单元可接受的来源范围。

    单元内多个选择器是或关系（数据集里的 `acceptable_evidence_ids` 就是这种
    含义）；`document` 用后缀匹配逻辑文档名，因此上传时加不加 run 前缀都能命中。
    """

    document: str | None = None
    kind: str = ANY_KIND
    locator: str | None = None


@dataclass(frozen=True)
class AnswerUnit:
    """数据集标注的一条答案事实及其可接受证据范围。"""

    unit_id: str
    claim: str
    selectors: tuple[SourceSelector, ...] = ()
    acceptance: str = ""


@dataclass(frozen=True)
class EvalCase:
    """一道评测题。"""

    schema_version: str
    case_id: str
    question: str
    reference_answer: str
    top_k: int
    answerable: bool
    units: tuple[AnswerUnit, ...] = ()
    expected_documents: tuple[str, ...] = ()
    forbidden_claims: tuple[str, ...] = ()
    question_type: str = "text"
    topic: str = ""
    category: str = ""
    image_requirement: str = "none"
    include_in_retrieval_aggregate: bool = True
    include_in_answer_aggregate: bool = True
    refusal_rubric: dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    # 本题适用哪些 DeepEval 指标；空表示用适配层默认集。**按数据集对应的业务链路声明**：
    # 检索链路（`/api/ai/rag/query` 不生成回答）由固定片段标注评分，回答侧两项
    # 留给面试链路（它在 `interview_runner.py` 里自己点指标）。因此这件事必须由题目声明，
    # 而不是写死在适配层。
    judge_metrics: tuple[str, ...] = ()
    relevant_chunk_ids: tuple[str, ...] = ()
    chunk_snapshot: tuple[dict[str, Any], ...] = field(default=(), repr=False, compare=False)
    chunk_snapshot_version: str = ""

    @property
    def unit_ids(self) -> tuple[str, ...]:
        return tuple(unit.unit_id for unit in self.units)

    @property
    def covers_every_document(self) -> bool:
        """跨文档题：答案单元落在两篇及以上预期文档上。"""
        documents = {
            selector.document
            for unit in self.units
            for selector in unit.selectors
            if selector.document
        }
        return len(documents) > 1


@dataclass(frozen=True)
class CorpusAsset:
    """数据集声明的入库素材；`relative_path` 相对数据集目录。"""

    relative_path: str
    sha256: str
    kind: str = "document"


@dataclass(frozen=True)
class QualityCorpus:
    """一轮评测要入库的语料。

    `primary_assets` 是**一次**上传的正文与附件：后端按上传 manifest 里的
    `relativePath` 把附件关联到正文，所以附件必须与正文同批、且相对路径要和正文中的
    Markdown 图片引用一致，分两批上传会让图片永远挂不上。

    `noise_assets` 是额外的干扰文档，单独上传是为了便于分别登记与核对数量；当前数据集的正文之间也可互为干扰。

    `document_file_names` 是本批次全部正文文件（不含干扰文档），`noise_file_names`
    是干扰文档；两者都要进清理注册表。

    `expected_unreferenced_attachments` 是「已上传但正文没有引用」的附件相对路径。
    这类附件不会被关联、也不会进入检索，属于**已知例外**，必须在数据集里点名而不是
    静默容忍——数量对不上就说明语料与正文已经漂移。
    """

    primary_assets: list[UploadAsset]
    noise_assets: list[UploadAsset]
    document_file_names: list[str]
    noise_file_names: list[str] = field(default_factory=list)
    expected_unreferenced_attachments: tuple[str, ...] = ()

    @property
    def attachment_assets(self) -> list[UploadAsset]:
        return [asset for asset in self.primary_assets if asset.role == "attachment"]


@dataclass(frozen=True)
class CaseSet:
    """一个数据集加载后的全部内容。

    `cases_path` 是拆分文件（如 `formal.jsonl`），`dataset_dir` 是同目录下的
    标注与语料根；两者分开是为了让 `formal` / `dev` 共用同一套标注。
    """

    schema_version: str
    dataset_dir: Path
    cases_path: Path
    cases: tuple[EvalCase, ...]
    assets: tuple[CorpusAsset, ...] = ()


@dataclass
class CaseResult:
    case_id: str
    question: str
    actual_answer: str
    reference_answer: str
    sources: list[dict[str, Any]]
    deterministic_metrics: dict[str, float | None]
    deepeval_metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    passed: bool | None = False
    evaluation_status: str = "failed"
    evidence_matches: dict[str, Any] = field(default_factory=dict)
    failure_reasons: list[str] = field(default_factory=list)
    bad_case_categories: list[str] = field(default_factory=list)
    # 分组维度：报告按题型与主题分别聚合，避免用总均值掩盖某一类全灭。
    question_type: str = ""
    topic: str = ""
    category: str = ""
    image_requirement: str = "none"
