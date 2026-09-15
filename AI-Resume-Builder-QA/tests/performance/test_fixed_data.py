import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
from PIL import Image

from tests.performance.locustfiles.common import data_factory
from tests.performance.locustfiles.common.data_factory import PERFORMANCE_DATA, PerformanceDataFactory


def _factory(root: Path, run_id: str) -> PerformanceDataFactory:
    # 离线检查使用 pytest 临时目录，不创建任何服务端资源。
    factory = object.__new__(PerformanceDataFactory)
    factory.root = root
    factory.run_id = run_id
    root.mkdir(parents=True)
    return factory


def test_fixed_image_manifest() -> None:
    root = PERFORMANCE_DATA / "image_parser"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["imageCount"] == manifest["pageCount"] == 5
    assert manifest["sourcePdf"]["selectedPages"] == [4, 5, 6, 9]
    assert [item["sourcePage"] for item in manifest["images"]] == [4, 5, 5, 6, 9]
    assert Counter(item["kind"] for item in manifest["images"]) == {"real_embedded_image": 5}
    assert len({item["sha256"] for item in manifest["images"]}) == 5
    for entry in manifest["images"]:
        path = root / entry["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry["sha256"]
        assert path.stat().st_size == entry["bytes"]
        with Image.open(path) as image:
            assert image.size == (entry["width"], entry["height"])
            assert image.width > 0 and image.height > 0
    pdf = root / manifest["pdf"]["path"]
    assert hashlib.sha256(pdf.read_bytes()).hexdigest() == manifest["pdf"]["sha256"]
    assert pdf.stat().st_size < 10 * 1024 * 1024
    assert "interview" not in manifest


def test_each_run_uses_same_five_images_with_distinct_names(tmp_path: Path) -> None:
    first = _factory(tmp_path / "first", "offline-c1")
    second = _factory(tmp_path / "second", "offline-c3")
    left = first.document("pdf", image_count=5)
    right = second.document("pdf", image_count=5)
    assert left.file_name != right.file_name
    assert left.marker != right.marker
    left_images = sorted((first.root / "assets").glob("*"))
    right_images = sorted((second.root / "assets").glob("*"))
    assert len(left_images) == len(right_images) == 5
    assert left.assets[0].path.stat().st_size < 10 * 1024 * 1024
    assert [path.read_bytes() for path in left_images] == [path.read_bytes() for path in right_images]
    assert left.assets[0].path.read_bytes().startswith(b"%PDF")
    assert right.assets[0].path.read_bytes().startswith(b"%PDF")


def test_modified_image_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "data" / "image_parser"
    root.mkdir(parents=True)
    (root / "changed.png").write_bytes(b"changed")
    (root / "manifest.json").write_text(json.dumps({"images": [{"path": "changed.png", "sha256": "wrong"}]}), encoding="utf-8")
    monkeypatch.setattr(data_factory, "PERFORMANCE_DATA", root.parent)
    factory = _factory(tmp_path / "output", "offline-tamper")
    with pytest.raises(ValueError, match="固定性能图片内容变化"):
        factory.document("pdf", image_count=5)


def test_more_than_five_images_is_rejected(tmp_path: Path) -> None:
    factory = _factory(tmp_path / "overflow", "offline-overflow")
    with pytest.raises(ValueError, match="仅包含5张"):
        factory.document("pdf", image_count=6)
