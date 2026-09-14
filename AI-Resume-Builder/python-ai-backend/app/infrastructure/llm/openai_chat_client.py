import json
import urllib.error
import urllib.request
from collections.abc import Iterator


def _normalize_chat_completions_path(path: str | None) -> str:
    normalized = (path or "").strip() or "/v1/chat/completions"
    return normalized if normalized.startswith("/") else f"/{normalized}"


def _build_chat_url(base_url: str, completions_path: str | None = None) -> str:
    normalized_base = (base_url or "").strip().rstrip("/") or "https://api.openai.com"
    path = _normalize_chat_completions_path(completions_path)

    if normalized_base.endswith(path):
        return normalized_base
    if normalized_base.endswith("/v1") and path.startswith("/v1/"):
        return normalized_base + path[3:]
    return f"{normalized_base}{path}"


def _extract_content(payload: dict) -> str:
    def to_text(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            texts = [to_text(item) for item in value]
            return "".join(item for item in texts if item)
        if isinstance(value, dict):
            for key in ("text", "output_text", "content", "value"):
                if key in value:
                    candidate = to_text(value.get(key))
                    if candidate:
                        return candidate
        return ""

    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        choices = []

    first = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = first.get("message") if isinstance(first, dict) else {}
    if isinstance(message, dict):
        content = to_text(message.get("content"))
        if content:
            return content
        refusal = to_text(message.get("refusal"))
        if refusal:
            return refusal

    direct_text = to_text(first.get("text")) if isinstance(first, dict) else ""
    if direct_text:
        return direct_text

    outputs = payload.get("output")
    if isinstance(outputs, list):
        output_text = to_text(outputs)
        if output_text:
            return output_text

    error = payload.get("error")
    if isinstance(error, dict):
        error_message = to_text(error.get("message"))
        if error_message:
            return error_message
    elif isinstance(error, str) and error.strip():
        return error.strip()

    return ""


def _raise_if_provider_error(payload: dict) -> None:
    error = payload.get("error")
    if not error:
        return
    if isinstance(error, dict):
        message = str(error.get("message") or "模型服务返回错误").strip()
    else:
        message = str(error).strip() or "模型服务返回错误"
    raise RuntimeError(f"模型服务返回错误: {message[:300]}")


def _validate_finish_reason(raw_reason: object) -> str:
    reason = str(raw_reason or "").strip().lower()
    if reason in {"", "stop"}:
        return reason
    if reason in {"length", "max_tokens"}:
        raise RuntimeError("模型输出达到长度上限，面试回答未完整生成")
    raise RuntimeError(f"模型输出异常结束: {reason[:40]}")


class OpenAIChatClient:
    def __init__(
        self,
        model_name: str,
        base_url: str,
        api_key: str,
        completions_path: str | None = None,
        timeout_seconds: float = 25.0,
    ) -> None:
        self.model_name = (model_name or "").strip() or "gpt-5.4"
        self.base_url = _build_chat_url(base_url, completions_path)
        self.api_key = (api_key or "").strip()
        self.timeout_seconds = max(3.0, float(timeout_seconds or 25.0))

    def _build_payload(self, message: str, system_prompt: str | None = None) -> dict:
        prompt = (system_prompt or "").strip() or "你是一个有帮助的助手。"
        return {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": message},
            ],
        }

    def _post(self, payload: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")

        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                return parsed if isinstance(parsed, dict) else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI API connection failed: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAI API returned invalid JSON") from exc

    def chat(self, message: str, system_prompt: str | None = None) -> str:
        safe_message = (message or "").strip()
        if not safe_message:
            return ""

        payload = self._build_payload(safe_message, system_prompt)
        parsed = self._post(payload)
        _raise_if_provider_error(parsed)
        choices = parsed.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            _validate_finish_reason(choices[0].get("finish_reason"))
        content = _extract_content(parsed).strip()
        return content or "No answer generated by model."

    def stream_chat(self, message: str, system_prompt: str | None = None) -> Iterator[str]:
        safe_message = (message or "").strip()
        if not safe_message:
            return

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing")

        payload = self._build_payload(safe_message, system_prompt)
        payload["stream"] = True
        request = urllib.request.Request(
            self.base_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            method="POST",
        )

        saw_done_marker = False
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                for raw in response:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        saw_done_marker = True
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        raise RuntimeError("模型流返回了无效事件")

                    _raise_if_provider_error(event)

                    choices = event.get("choices")
                    if not isinstance(choices, list) or not choices:
                        # 兼容只携带 usage 等非内容事件；真正的服务错误已在上面抛出。
                        continue
                    choice0 = choices[0] if isinstance(choices[0], dict) else {}
                    finish_reason = _validate_finish_reason(choice0.get("finish_reason"))
                    delta = choice0.get("delta")
                    if not isinstance(delta, dict):
                        # 某些兼容网关在结束帧不会再带 delta，但会给 finish_reason。
                        continue
                    token = delta.get("content")
                    if isinstance(token, list):
                        token = "".join(
                            str(item.get("text") or item.get("content") or "")
                            for item in token
                            if isinstance(item, dict)
                        )
                    if isinstance(token, str) and token:
                        yield token
                if not saw_done_marker:
                    raise RuntimeError("模型流连接提前结束，未收到正常结束标记")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"OpenAI API HTTP {exc.code}: {detail[:300]}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI API connection failed: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:
            raise RuntimeError("模型流连接提前结束，请重试") from exc
