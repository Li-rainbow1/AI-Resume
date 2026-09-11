from typing import Any

from clients.rag import RagClient


REQUIRED_AI_SERVICES = {"chat", "embedding", "vision"}
MOCK_MARKERS = ("mock-ai", "mock_ai", "mock-model", "mock_model")


def _contains_mock_marker(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_mock_marker(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_mock_marker(item) for item in value)
    return any(marker in str(value).lower() for marker in MOCK_MARKERS)


async def real_model_guard_reason(rag_client: RagClient) -> str | None:
    """读取非敏感系统服务摘要，阻止把 Mock AI 当成质量基线。"""
    try:
        services = await rag_client.list_system_services()
    except Exception as exc:
        return f"无法核验 Chat、Embedding、Vision 配置：{type(exc).__name__}"

    configured: set[str] = set()
    mocked: set[str] = set()
    for service in services:
        service_key = str(service.get("serviceKey") or "").lower()
        matched = next((name for name in REQUIRED_AI_SERVICES if name in service_key), None)
        if not matched:
            continue
        configured.add(matched)
        if _contains_mock_marker(service.get("config") or {}):
            mocked.add(matched)

    missing = sorted(REQUIRED_AI_SERVICES - configured)
    if missing:
        return "缺少可核验的真实模型服务配置：" + ", ".join(missing)
    if mocked:
        return "检测到 Mock AI 服务，质量基线拒绝执行：" + ", ".join(sorted(mocked))
    return None
