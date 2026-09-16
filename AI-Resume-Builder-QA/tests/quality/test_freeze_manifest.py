"""冻结清单契约：生成方与校验方共用一套实现，且门禁真的能拦住变化。

为什么这些断言值得写死：

- **清单的字节形状是门禁的一部分**。门禁按 `read_bytes()` 算 sha256，换行风格与末尾
  有无换行都会影响结果。清单必须固定为 CRLF + 无末尾换行，否则用编辑器保存一次就会
  让 `previous_manifest_sha256` 与历史归档对不上。
- **生成方与校验方不能漂移**。两者如果各维护一份「该冻哪些文件」，漂移方向永远是
  「校验比生成宽松」，等于门禁静默失效。这里的往返测试就是钉住这一点：生成器产出的
  清单必须能被门禁逐项核过；数据集/代码被动过一个字节，门禁必须拒绝。
- **清单自己不参与冻结**。否则每写一次清单，上一次的校验就必然失败。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from quality.freeze import (
    FREEZE_HISTORY_DIR,
    FREEZE_MANIFEST_NAME,
    file_hashes,
    iter_code_files,
    iter_dataset_files,
    read_manifest,
    sha256_file,
    verify_files,
    verify_judge,
    verify_services,
    write_manifest,
)

QA_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = QA_ROOT / "scripts" / "freeze_quality_dataset.py"
DATASET = QA_ROOT / "testdata" / "quality" / "interview-notes-v1"

_FAKE_SERVICES = [
    {"serviceKey": "chat", "config": {"model": "deepseek-chat", "temperature": 0.0}},
    {"serviceKey": "embedding", "config": {"model": "bge-m3"}},
    {"serviceKey": "vision", "config": {"model": "qwen-vl-max"}},
]


def _load_generator():
    """按路径加载生成脚本（`scripts/` 不是包，不能直接 import）。"""
    spec = importlib.util.spec_from_file_location("freeze_quality_dataset", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def generator():
    return _load_generator()


def _seed_dataset(root: Path) -> None:
    """造一个最小数据集：两篇正文 + 一张附件，哈希由测试自己算。"""
    (root / "corpus" / "附件").mkdir(parents=True)
    (root / "corpus" / "1-测试.md").write_bytes("正文一\r\n".encode("utf-8"))
    (root / "corpus" / "2-网络.md").write_bytes("正文二\n".encode("utf-8"))
    (root / "corpus" / "附件" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\nA")
    (root / "cases.json").write_bytes("[]".encode("utf-8"))


def _manifest_for(root: Path) -> dict:
    return {
        "files_sha256": file_hashes(root, iter_dataset_files(root)),
        "qa_code_sha256": {},
        "services": {},
    }


# --- 清单的字节形状 ---------------------------------------------------------


def test_write_manifest_is_crlf_without_trailing_newline(tmp_path: Path) -> None:
    payload = {"files_sha256": {"a.md": "0" * 64}, "b": "中文"}
    path = write_manifest(tmp_path, payload)

    raw = path.read_bytes()
    # 只用 CRLF：把 CRLF 摘掉后不应再有裸 LF。
    assert b"\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert not raw.endswith(b"\n"), "末尾换行会让哈希多一个字节"
    assert path.name == FREEZE_MANIFEST_NAME


def test_manifest_round_trip_reads_back_equal(tmp_path: Path) -> None:
    payload = {"files_sha256": {"a.md": "1" * 64}, "judge": {"model": "m"}}
    write_manifest(tmp_path, payload)
    assert read_manifest(tmp_path) == payload


def test_read_manifest_returns_none_when_absent(tmp_path: Path) -> None:
    assert read_manifest(tmp_path) is None


def test_read_manifest_rejects_non_object(tmp_path: Path) -> None:
    (tmp_path / FREEZE_MANIFEST_NAME).write_bytes(b"[1, 2, 3]")
    with pytest.raises(ValueError):
        read_manifest(tmp_path)


# --- 该冻哪些文件 -----------------------------------------------------------


def test_dataset_iteration_excludes_manifest_history_and_pycache(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    write_manifest(tmp_path, {"files_sha256": {}})
    (tmp_path / FREEZE_HISTORY_DIR).mkdir()
    (tmp_path / FREEZE_HISTORY_DIR / "before-abc.json").write_bytes(b"{}")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.pyc").write_bytes(b"x")

    names = {path.relative_to(tmp_path).as_posix() for path in iter_dataset_files(tmp_path)}

    assert names == {"corpus/1-测试.md", "corpus/2-网络.md", "corpus/附件/a.png", "cases.json"}


def test_dataset_iteration_is_sorted_and_keys_are_posix(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    files = iter_dataset_files(tmp_path)
    keys = list(file_hashes(tmp_path, files))
    assert keys == sorted(keys)
    assert all("\\" not in key for key in keys)


def test_code_iteration_stays_inside_the_declared_dirs() -> None:
    files = iter_code_files(QA_ROOT)
    directories = {path.relative_to(QA_ROOT).parts[0] for path in files}
    assert {"quality", "clients", "fixtures"} <= directories
    # 测试代码不属于「评分代码」，不能被卷进冻结（否则写测试就会让门禁失败）。
    assert not any(path.relative_to(QA_ROOT).parts[0] == "tests" for path in files)


# --- 门禁：核 files_sha256 / qa_code_sha256 ---------------------------------


def test_verify_files_accepts_untouched_dataset(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    verify_files(tmp_path, QA_ROOT, _manifest_for(tmp_path))


def test_verify_files_detects_tampering(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    manifest = _manifest_for(tmp_path)
    (tmp_path / "corpus" / "1-测试.md").write_bytes("正文一\r\n改过了\n".encode("utf-8"))

    with pytest.raises(ValueError, match="corpus/1-测试.md"):
        verify_files(tmp_path, QA_ROOT, manifest)


def test_verify_files_rejects_missing_file(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    manifest = _manifest_for(tmp_path)
    (tmp_path / "cases.json").unlink()

    with pytest.raises(ValueError, match="cases.json"):
        verify_files(tmp_path, QA_ROOT, manifest)


def test_verify_files_rejects_path_escape(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    manifest = _manifest_for(tmp_path)
    manifest["files_sha256"]["../outside.md"] = "0" * 64

    with pytest.raises(ValueError, match="路径越界"):
        verify_files(tmp_path, QA_ROOT, manifest)


def test_verify_files_requires_every_block(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    manifest = _manifest_for(tmp_path)
    for key in ("files_sha256", "qa_code_sha256"):
        broken = dict(manifest)
        broken.pop(key)
        with pytest.raises(ValueError, match=key):
            verify_files(tmp_path, QA_ROOT, broken)


def test_verify_files_rejects_non_object_block(tmp_path: Path) -> None:
    _seed_dataset(tmp_path)
    manifest = _manifest_for(tmp_path)
    manifest["qa_code_sha256"] = ["not", "a", "mapping"]
    with pytest.raises(ValueError):
        verify_files(tmp_path, QA_ROOT, manifest)


# --- 门禁：核被测服务配置 ---------------------------------------------------


def test_verify_services_compares_only_declared_keys() -> None:
    manifest = {"services": {"chat": {"model": "deepseek-chat"}}}
    # 清单没点名的键（temperature）不参与比对。
    verify_services(manifest, {"chat": {"model": "deepseek-chat", "temperature": 1.0}})


def test_verify_services_detects_changed_value() -> None:
    manifest = {"services": {"chat": {"model": "deepseek-chat"}}}
    with pytest.raises(ValueError):
        verify_services(manifest, {"chat": {"model": "其他模型"}})


def test_verify_services_detects_missing_service() -> None:
    manifest = {"services": {"chat": {"model": "deepseek-chat"}}}
    with pytest.raises(ValueError):
        verify_services(manifest, {})


def test_verify_services_requires_the_block() -> None:
    with pytest.raises(ValueError, match="services"):
        verify_services({}, {"chat": {}})


# --- 门禁：核判分器 ---------------------------------------------------------


def test_verify_judge_is_skipped_when_round_has_no_judge() -> None:
    # actual 为 None = 本轮不判分，清单里记不记 judge 都不拦。
    verify_judge({"judge": {"model": "m"}}, "judge", None)
    verify_judge({}, "judge", None)


def test_verify_judge_requires_a_recorded_config() -> None:
    with pytest.raises(ValueError, match="judge"):
        verify_judge({}, "judge", {"model": "m"})


def test_verify_judge_requires_full_equality() -> None:
    declared = {"model": "m", "threshold": 0.5, "repeat_count": 1}
    verify_judge({"judge": declared}, "judge", dict(declared))
    # 少一个键也算不一致：判分配置不能靠子集比对蒙过去。
    with pytest.raises(ValueError):
        verify_judge({"judge": declared}, "judge", {"model": "m", "threshold": 0.5})


def test_verify_judge_uses_the_caller_supplied_key() -> None:
    """面试通道记在 `interview_judge` 下，读错键会静默跳过比对。"""
    declared = {"model": "m"}
    verify_judge({"interview_judge": declared}, "interview_judge", dict(declared))
    with pytest.raises(ValueError):
        verify_judge({"judge": declared}, "interview_judge", dict(declared))


# --- 生成器：归档与自校验 ---------------------------------------------------


def test_archive_previous_copies_bytes_and_returns_sha(tmp_path: Path) -> None:
    generator = _load_generator()
    original = b'{\r\n  "a": 1\r\n}'
    path = tmp_path / FREEZE_MANIFEST_NAME
    path.write_bytes(original)
    digest = sha256_file(path)

    assert generator.archive_previous(tmp_path) == digest
    archived = tmp_path / FREEZE_HISTORY_DIR / f"before-{digest[:8]}.json"
    assert archived.read_bytes() == original, "归档必须逐字节复制"
    # 没有上一版时返回 None，且不建历史目录。
    empty = tmp_path / "empty"
    empty.mkdir()
    assert generator.archive_previous(empty) is None
    assert not (empty / FREEZE_HISTORY_DIR).exists()


def test_generator_manifest_satisfies_the_gate(generator) -> None:
    """往返：生成器算出的清单，必须能被门禁逐项核过。"""
    manifest = generator.build_manifest(
        DATASET, QA_ROOT, split=None, services=_FAKE_SERVICES, previous=None, previous_sha256=None
    )

    verify_files(DATASET, QA_ROOT, manifest)
    verify_services(manifest, manifest["services"])

    assert set(manifest["services"]) == {"chat", "embedding", "vision"}
    assert manifest["files_sha256"], "数据集一个文件都没冻上"
    assert manifest["qa_code_sha256"], "评分代码一个文件都没冻上"
    assert manifest["previous_manifest_sha256"] is None
    assert manifest["revision"], "revision 是给历史归档比对用的指纹"
    # revision 是除自身以外全部事实的指纹，同一份输入必须算得一样。
    assert manifest["revision"] == generator.build_manifest(
        DATASET, QA_ROOT, split=None, services=_FAKE_SERVICES, previous=None, previous_sha256=None
    )["revision"]


def test_generator_manifest_catches_dataset_change(generator) -> None:
    """数据集被动过一个字节，门禁必须拒绝（用改声明的哈希来模拟漂移）。"""
    manifest = generator.build_manifest(
        DATASET, QA_ROOT, split=None, services=_FAKE_SERVICES, previous=None, previous_sha256=None
    )
    name = next(iter(manifest["files_sha256"]))
    manifest["files_sha256"][name] = "f" * 64

    with pytest.raises(ValueError, match="不一致"):
        verify_files(DATASET, QA_ROOT, manifest)


def test_generator_records_previous_revision_when_rewriting(generator) -> None:
    previous = {"revision": "abc123"}
    manifest = generator.build_manifest(
        DATASET,
        QA_ROOT,
        split=None,
        services=_FAKE_SERVICES,
        previous=previous,
        previous_sha256="d" * 64,
    )
    assert manifest["previous_manifest_revision"] == "abc123"
    assert manifest["previous_manifest_sha256"] == "d" * 64


def test_generator_refuses_to_freeze_when_a_service_is_absent(generator) -> None:
    with pytest.raises(SystemExit, match="embedding"):
        generator.build_manifest(
            DATASET,
            QA_ROOT,
            split=None,
            services=[item for item in _FAKE_SERVICES if item["serviceKey"] != "embedding"],
            previous=None,
            previous_sha256=None,
        )


def test_generator_loads_services_from_array_or_wrapped_object(tmp_path: Path, generator) -> None:
    array = tmp_path / "array.json"
    array.write_text(json.dumps(_FAKE_SERVICES), encoding="utf-8")
    assert generator.load_services_from_file(array) == _FAKE_SERVICES

    wrapped = tmp_path / "wrapped.json"
    wrapped.write_text(json.dumps({"items": _FAKE_SERVICES, "total": 3}), encoding="utf-8")
    assert generator.load_services_from_file(wrapped) == _FAKE_SERVICES


def test_generator_rejects_unusable_services_json(tmp_path: Path, generator) -> None:
    """空对象糊不过去：抓不到配置就必须报错，不能让这层校验静默失效。"""
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"total": 3}), encoding="utf-8")
    with pytest.raises(SystemExit):
        generator.load_services_from_file(bad)
