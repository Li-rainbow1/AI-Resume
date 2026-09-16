r"""生成数据集的冻结清单（`freeze-manifest.json`）。

「冻结」= 跑正式评测之前，把会影响分数的输入钉死成一份字节级哈希清单，运行时逐项比对，
任一项对不上就拒绝开跑。目的是让报告里的数字**可归因**：数据集、评分代码、被测模型配置、
判分器任意一项在无人察觉时变了，分数差异就没法归因到任何一项。

清单的四类内容与校验逻辑都在 `quality/freeze.py`，本脚本只负责**生成**——生成方与
校验方共用同一套「该冻哪些文件、怎么算哈希」，避免两边漂移成「校验比生成宽松」。

用法（在 QA 仓库根目录执行）：

    # 预览，不落盘
    .\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --dry-run

    # 完整冻结：services 从在线的隔离栈抓（需要 QA_BASE_URL / QA_ADMIN_* 已配好）
    .\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py

    # 离线冻结：services 用事先抓好的接口响应
    .\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --services-json captured\system-services.json

    # 换数据集 / 指定拆分
    .\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --dataset testdata\quality\interview-notes-v1 --split formal

    # 只冻确定性轮（明确放弃判分轮）
    .\.venv\Scripts\python.exe scripts\freeze_quality_dataset.py --deterministic-only

注意事项：

- 清单按**约定**写：CRLF + 无末尾换行。门禁是字节级读取，用编辑器改这份文件会让
  `revision` 与历史归档对不上。
- 已有清单会被**逐字节**归档到 `<数据集>/freeze-history/before-<sha8>.json`，并把它的
  哈希记进新清单的 `previous_manifest_sha256`；改动数据集/代码后必须重新冻结。
- `services` 只能来自被测系统本身。抓不到就**不要**用空对象糊过去——空对象会让这层
  校验静默失效。
- 判分环境变量没配全时**默认直接失败**，不再默默写 `judge: null`：那种清单在判分轮会被
  门禁拒绝（找不到 `judge` 就报错），却仍能跑通确定性轮，最容易让人误以为清单可用。
  确实只想冻确定性轮时加 `--deterministic-only` 显式声明。
- ⚠️ 冻结覆盖面的行尾由 `AI-Resume-Builder-QA/.gitattributes` 钉成 LF。改动这批文件后
  先确认工作区已是 LF（`git status` 干净）再冻，否则冻下来的是「本机当前检出状态」的哈希。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clients.auth import AuthClient  # noqa: E402
from clients.rag import RagClient  # noqa: E402
from fixtures.config import QaSettings  # noqa: E402
from quality.deepeval_adapter import judge_config_summary  # noqa: E402
from quality.freeze import (  # noqa: E402
    FREEZE_HISTORY_DIR,
    FREEZE_MANIFEST_NAME,
    file_hashes,
    iter_code_files,
    iter_dataset_files,
    read_manifest,
    sha256_file,
    verify_files,
    verify_services,
    write_manifest,
)
from quality.loaders import available_splits, load_case_set  # noqa: E402
from quality.runner import DEFAULT_DATASET_DIR  # noqa: E402

# 参与冻结的被测服务：检索、回答与图片解析各一项，判分器另由 `judge` 记录。
FROZEN_SERVICES = ("chat", "embedding", "vision")


async def fetch_services(base_url: str, username: str, password: str) -> list[dict]:
    """从在线的隔离栈抓系统服务配置；这是唯一能拿到真实配置的途径。"""
    import httpx

    if not username or not password:
        raise SystemExit("缺少 QA_ADMIN_USERNAME / QA_ADMIN_PASSWORD，无法抓取被测服务配置")
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        session = await AuthClient(client).login(username, password)
        client.headers["Authorization"] = "Bearer " + session.access_token
        return await RagClient(client).list_system_services()


def load_services_from_file(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        payload = payload["items"]
    if not isinstance(payload, list):
        raise SystemExit(f"services JSON 必须是服务数组或含 items 的对象：{path}")
    return [item for item in payload if isinstance(item, dict)]


def resolve_judge(*, deterministic_only: bool) -> dict | None:
    """解析判分配置；缺配置时按调用方的**显式**选择处理。

    缺 Judge 时默默写 `judge: null` 是个陷阱：`verify_judge` 在判分轮找不到 `judge` 会
    直接拒绝，所以这份清单跑不了 DeepEval 基线，却能跑通确定性轮——看起来「冻结成功了」，
    等到要出分数才发现白冻一次。因此默认失败，只有显式 `--deterministic-only` 才允许
    写成 `None`，并且把后果打在警告里。
    """
    try:
        return judge_config_summary()
    except ValueError as exc:
        if deterministic_only:
            print("警告：按 --deterministic-only 冻结，不记录 judge；"
                  f"这份清单**不能**用于判分轮，DeepEval 与面试评测都会被门禁拒绝（{exc}）。",
                  file=sys.stderr)
            return None
        raise SystemExit(
            f"判分环境变量未配全，无法冻结判分口径：{exc}\n"
            "先配好 DEEPEVAL_JUDGE_MODEL / DEEPEVAL_JUDGE_BASE_URL / DEEPEVAL_JUDGE_API_KEY，"
            "或加 --deterministic-only 明确只冻确定性轮。"
        ) from exc


def build_manifest(
    dataset_dir: Path,
    qa_root: Path,
    *,
    split: str | None,
    services: list[dict],
    previous: dict | None,
    previous_sha256: str | None,
    deterministic_only: bool = False,
) -> dict:
    case_set = load_case_set(dataset_dir, split)
    declared_services: dict[str, dict] = {}
    for key in FROZEN_SERVICES:
        item = next((item for item in services if item.get("serviceKey") == key), None)
        if item is None:
            raise SystemExit(f"被测系统没有返回 {key} 服务配置，无法冻结")
        declared_services[key] = item.get("config") or {}

    judge = resolve_judge(deterministic_only=deterministic_only)

    files_sha256 = file_hashes(dataset_dir, iter_dataset_files(dataset_dir))
    qa_code_sha256 = file_hashes(qa_root, iter_code_files(qa_root))
    body = {
        "dataset_version": case_set.schema_version,
        "schema_version": case_set.schema_version,
        "splits": sorted(available_splits(dataset_dir)),
        "case_count": len(case_set.cases),
        "asset_count": len(case_set.assets),
        "created_on": date.today().isoformat(),
        "previous_manifest_sha256": previous_sha256,
        "files_sha256": files_sha256,
        "qa_code_sha256": qa_code_sha256,
        "services": declared_services,
        "judge": judge,
    }
    if previous is not None:
        # 清单没有旧的 `revision` 字段时也不强求：只为交代「上一版是什么」。
        body["previous_manifest_revision"] = previous.get("revision")
    # `revision` 是上面全部事实的指纹，不含自身；写进文件后可用它比对历史归档。
    body["revision"] = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return body


def archive_previous(dataset_dir: Path) -> str | None:
    """把已有清单逐字节归档，返回它的 sha256（没有则返回 None）。"""
    path = dataset_dir / FREEZE_MANIFEST_NAME
    if not path.is_file():
        return None
    digest = sha256_file(path)
    history = dataset_dir / FREEZE_HISTORY_DIR
    history.mkdir(parents=True, exist_ok=True)
    (history / f"before-{digest[:8]}.json").write_bytes(path.read_bytes())
    return digest


def main() -> None:
    parser = argparse.ArgumentParser(description="生成数据集冻结清单")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR, help="数据集目录")
    parser.add_argument("--split", default=None, help="拆分名（默认 formal）")
    parser.add_argument("--services-json", type=Path, default=None, help="离线的系统服务响应")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不落盘")
    parser.add_argument(
        "--deterministic-only",
        action="store_true",
        help="不要求判分口径：判分环境变量缺失时不失败，清单里记 judge=null（该清单只能跑确定性轮）",
    )
    args = parser.parse_args()

    dataset_dir = args.dataset.resolve()
    if not dataset_dir.is_dir():
        raise SystemExit(f"数据集目录不存在：{dataset_dir}")

    if args.services_json:
        services = load_services_from_file(args.services_json)
        source = f"文件 {args.services_json}"
    else:
        settings = QaSettings.from_environment()
        services = asyncio.run(
            fetch_services(settings.base_url, settings.admin_username, settings.admin_password)
        )
        source = f"在线 {settings.base_url}"
    print(f"被测服务配置来源：{source}（{len(services)} 项）")

    previous = read_manifest(dataset_dir)
    previous_sha256 = sha256_file(dataset_dir / FREEZE_MANIFEST_NAME) if previous else None
    manifest = build_manifest(
        dataset_dir,
        ROOT,
        split=args.split,
        services=services,
        previous=previous,
        previous_sha256=previous_sha256,
        deterministic_only=args.deterministic_only,
    )

    # 自校验：用与门禁完全相同的实现核一遍刚生成的清单，防止生成不该冻的文件。
    verify_files(dataset_dir, ROOT, manifest)
    verify_services(manifest, manifest["services"])
    print(f"数据集文件 {len(manifest['files_sha256'])} 个、评测代码 {len(manifest['qa_code_sha256'])} 个")
    print(f"随机抽取核对通过；revision={manifest['revision'][:16]}…")
    print(f"judge={'已记录' if manifest['judge'] else '未记录（--deterministic-only，该清单不能用于判分轮）'}")
    print(f"上一版：{previous_sha256 or '（无）'}")

    if args.dry_run:
        print("dry-run：未写入任何文件")
        return
    archived = archive_previous(dataset_dir)
    path = write_manifest(dataset_dir, manifest)
    print(f"已写入 {path}")
    print(f"清单自身 sha256={sha256_file(path)}")
    if archived:
        print(f"上一版已归档到 {dataset_dir / FREEZE_HISTORY_DIR}（sha256={archived}）")


if __name__ == "__main__":
    main()
