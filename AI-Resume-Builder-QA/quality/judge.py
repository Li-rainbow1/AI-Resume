"""判分配置与请求配方的唯一来源。

这个模块只回答两件事：**用哪个端点、发什么样的请求**。三条判分链路
（确定性层的语义判分器、DeepEval 四项指标、面试八股链路）都必须经由这里的
`judge_completion` 与 `judge_config_from_environment`，否则配方会再次分叉——
历史上它就分叉过三次，同一个 Judge 模型收到过三种请求（一家发
`response_format` + 关思考，一家两样都不发）。

只依赖 `quality.models` 以外什么也不依赖：`openai` 在真正判分时才导入，因此
离线单测与旧正则集在没装 `[eval]` 可选依赖时也能 import 本模块。

`temperature=0`、`response_format={"type": "json_object"}`、思考参数、`max_retries=0`、
`timeout` 这几项是**共同约定**，改动即等于改变三条链路的历史分数，必须一起改、并同步
`JudgeConfig.summary()`（报告里记的就是它）。

**唯一不改变判分口径、因此可以单独加的，是传输层重试**（2026-09-17 加）：一次断连
不该等于「这题判分失败」，更不该中断整轮 50 题。重试只针对传输类异常，重试的是
**同一条报文**，模型看到的输入没有变，所以分数含义不变；次数与既有的 `max_attempts`
同口径，策略照原样记进 `summary()`。

思考参数只有一套：判分器是**智谱原生端点**（`open.bigmodel.cn/api/paas/v4`）上的
`glm-5.3-flash`，恒开思考，写法为 `extra_body={"thinking": {"type": "enabled"}}`。

**故意不保留 dashscope `compatible-mode` 的 `enable_thinking` 兼容分支**：两套字段名并存
就得在每次判分时判断"该发哪一组"，而端点只有一个。真要换回去，改 `JUDGE_EXTRA_BODY`
一处即可——显式改动比隐式回落更好查。
"""

import json
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

# 环境变量优先读 QUALITY_JUDGE_*，缺省回落 DEEPEVAL_JUDGE_*。默认不建议单独配
# QUALITY_JUDGE_*：让它三条链路共用同一个 Judge，既保持口径一致，也避免把判分器
# 换成待测 Chat 模型自己（自评）。
JUDGE_ENV_PREFIXES = ("QUALITY_JUDGE", "DEEPEVAL_JUDGE")
JUDGE_ENV_KEYS = ("MODEL", "BASE_URL", "API_KEY")

# 判分请求的固定配方；仅用于记录，实际取值在 `judge_completion` 里。
JUDGE_TEMPERATURE = 0
JUDGE_RESPONSE_FORMAT = "json_object"
JUDGE_SDK_MAX_RETRIES = 0

# 思考参数：智谱原生端点的写法，**恒开思考**。整组就是 `extra_body`，不再有第二种风格。
JUDGE_EXTRA_BODY: dict[str, Any] = {
    "thinking": {"type": "enabled"},
    "reasoning_effort": "low",
}

# 传输层重试的退避间隔（秒）。次数不另设旋钮：与既有的 `max_attempts` 同一个口径。
JUDGE_TRANSPORT_BACKOFF_SECONDS = 2.0


@dataclass(frozen=True)
class JudgeConfig:
    """判分通道配置；`summary()` 去掉凭证，可直接写进报告。"""

    model: str
    base_url: str
    api_key: str
    timeout: float = 60.0
    # 重试上限，两处共用一个口径：① 调用方对「返回了但结构不合法」的重试；② `judge_completion`
    # 对传输类异常（断连/超时/限流/5xx）的重试。两者都不会改变发给模型的报文内容。
    max_attempts: int = 3
    # 这组配置来自哪个环境变量前缀（`QUALITY_JUDGE` / `DEEPEVAL_JUDGE`），
    # 用于报告里交代「这份分数是谁配的」。
    prefix: str = ""

    def summary(self) -> dict[str, Any]:
        endpoint = urlsplit(self.base_url)
        host = endpoint.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if endpoint.port:
            host += f":{endpoint.port}"
        return {
            "model": self.model,
            "base_url": urlunsplit((endpoint.scheme, host, endpoint.path, "", "")),
            "env_prefix": self.prefix,
            "temperature": JUDGE_TEMPERATURE,
            "response_format": JUDGE_RESPONSE_FORMAT,
            "thinking": dict(JUDGE_EXTRA_BODY),
            "sdk_max_retries": JUDGE_SDK_MAX_RETRIES,
            "max_attempts": self.max_attempts,
            # SDK 不重试，但 `judge_completion` 自己在传输层重试；次数不写出来的话，
            # 报告里会看起来像「一次断连就判失败」。
            "transport_retry_attempts": self.max_attempts,
            "transport_retry_backoff_seconds": JUDGE_TRANSPORT_BACKOFF_SECONDS,
            "request_timeout_seconds": self.timeout,
        }


def judge_config_from_environment(prefixes: Iterable[str] = JUDGE_ENV_PREFIXES) -> JudgeConfig:
    """按前缀顺序取第一组配齐的判分配置；缺哪个变量就报哪个变量名。"""
    for prefix in prefixes:
        names = [f"{prefix}_{key}" for key in JUDGE_ENV_KEYS]
        values = [os.getenv(name, "").strip() for name in names]
        if all(values):
            model, base_url, api_key = values
            return JudgeConfig(
                model=model,
                base_url=base_url,
                api_key=api_key,
                timeout=float(os.getenv(f"{prefix}_TIMEOUT_SECONDS", "60")),
                max_attempts=max(1, int(os.getenv(f"{prefix}_MAX_ATTEMPTS", "3"))),
                prefix=prefix,
            )
        if any(values):
            missing = [name for name, value in zip(names, values, strict=True) if not value]
            raise ValueError("判分配置不完整，缺少环境变量：" + ", ".join(missing))
    raise ValueError(
        "判分链路需要判分模型配置，请设置 "
        + " 或 ".join(f"{prefix}_MODEL/{prefix}_BASE_URL/{prefix}_API_KEY" for prefix in prefixes)
    )


def judge_knob(config: JudgeConfig, name: str, default: str) -> str:
    """指标级参数跟着生效的前缀走；该前缀没配就回落 `DEEPEVAL_JUDGE_*`。

    阈值与重复次数只影响 DeepEval 层，不属于通道本身；但「配置来自哪个前缀，
    它的参数也从哪个前缀找」这条规则要一致，否则设了 `QUALITY_JUDGE_*` 之后
    调不出对应的阈值。
    """
    for candidate in dict.fromkeys((f"{config.prefix}_{name}", f"DEEPEVAL_JUDGE_{name}")):
        value = os.getenv(candidate, "").strip()
        if value:
            return value
    return default


def judge_completion(config: JudgeConfig, system: str, user: str) -> str:
    """**唯一的判分请求出口**：返回模型正文，不解析。

    三条链路都走这里，所以「同一套 Judge」的请求配方只有这一份，**传输层重试也只有
    这一份**——放在这里而不是各调用方，是因为同一个断连在确定性层与 DeepEval 层必须
    有同一个结局。2026-09-17 实测：端点偶发 `APIConnectionError` 时，确定性层的证据
    判分没有任何保护，一个断连把整轮 50 题（已跑 19 分钟）全废。

    只重试传输类异常，且重试的是**同一条报文**——模型输入未变，分数含义不变。
    鉴权、400 这类重试没有意义（同一份报文再发一次还是错），直接抛。
    """
    from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError

    client = OpenAI(
        api_key=config.api_key,
        base_url=config.base_url,
        timeout=config.timeout,
        max_retries=JUDGE_SDK_MAX_RETRIES,
    )
    # `APITimeoutError` 是 `APIConnectionError` 的子类，不必单列；5xx 与 429 由
    # SDK 归成 `InternalServerError` / `RateLimitError`。
    retryable = (APIConnectionError, RateLimitError, InternalServerError)
    attempts = max(1, config.max_attempts)
    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(
                model=config.model,
                temperature=JUDGE_TEMPERATURE,
                response_format={"type": JUDGE_RESPONSE_FORMAT},
                extra_body=dict(JUDGE_EXTRA_BODY),
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            )
        except retryable:
            if attempt == attempts:
                raise
            time.sleep(JUDGE_TRANSPORT_BACKOFF_SECONDS * attempt)
            continue
        return response.choices[0].message.content or ""
    raise AssertionError("unreachable")


class JudgeChannel:
    """判分通道协议：给一段提示，回一个 JSON 对象。"""

    def answer_json(self, system: str, user: str) -> dict[str, Any]:
        raise NotImplementedError

    def summary(self) -> dict[str, Any]:
        return {}


class OpenAICompatibleJudge(JudgeChannel):
    """OpenAI 兼容接口的 JSON 判分通道（确定性层用）。"""

    def __init__(self, config: JudgeConfig) -> None:
        self.config = config

    def summary(self) -> dict[str, Any]:
        return self.config.summary()

    def answer_json(self, system: str, user: str) -> dict[str, Any]:
        payload = json.loads(judge_completion(self.config, system, user))
        if not isinstance(payload, dict):
            raise ValueError("判分模型未返回 JSON 对象")
        return payload


@lru_cache(maxsize=1)
def judge_channel_from_environment() -> JudgeChannel:
    """全进程共用一份判分通道，避免每处判分各读一次环境。"""
    return OpenAICompatibleJudge(judge_config_from_environment())
