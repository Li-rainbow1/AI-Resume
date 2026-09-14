from app.infrastructure.config.settings import Settings, get_settings
from app.infrastructure.llm.openai_chat_adapter import OpenAIChatAdapter
from app.infrastructure.llm.openai_realtime_adapter import OpenAIRealtimeAdapter


def create_chat_client(settings: Settings | None = None) -> OpenAIChatAdapter:
    resolved = settings or get_settings()
    return OpenAIChatAdapter(
        model_name=resolved.openai_chat_model,
        base_url=resolved.openai_base_url,
        api_key=resolved.openai_api_key,
        completions_path=resolved.openai_chat_completions_path,
        timeout_seconds=resolved.openai_chat_timeout_seconds,
    )


def create_realtime_client(settings: Settings | None = None) -> OpenAIRealtimeAdapter:
    resolved = settings or get_settings()
    return OpenAIRealtimeAdapter(
        base_url=resolved.openai_realtime_base_url,
        api_key=resolved.openai_realtime_api_key,
        client_secrets_path=resolved.openai_realtime_client_secrets_path,
        realtime_calls_path=resolved.openai_realtime_calls_path,
        default_model=resolved.openai_realtime_transcription_model,
        default_language=resolved.openai_realtime_language,
    )
