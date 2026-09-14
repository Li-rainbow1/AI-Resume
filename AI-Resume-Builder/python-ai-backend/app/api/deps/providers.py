from app.bootstrap.container import (
    build_chat_client,
    build_rag_retriever,
    build_realtime_client,
    resolve_settings,
)


def get_settings_dependency():
    return resolve_settings()


def get_chat_client():
    return build_chat_client()


def get_realtime_client():
    return build_realtime_client()


def get_rag_retriever():
    return build_rag_retriever()
