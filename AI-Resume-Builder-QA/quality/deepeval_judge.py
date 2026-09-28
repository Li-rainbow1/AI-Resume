"""把 `quality/judge.py` 的判分配方接到 DeepEval 的模型接口上。

DeepEval 的指标需要一个 `DeepEvalBaseLLM`；这里只做「协议转换」，不自己做请求——
所有请求都交给 `judge_completion`，所以这一层与确定性层的语义判分器、面试链路
发出的报文完全一致（模型、地址、`temperature=0`、`response_format`、关思考、
超时、重试策略都只有一处定义）。

这个模块会 import `deepeval`，属于 `[eval]` 可选依赖，因此**不要**从
`quality.runner` 的模块顶层 import 它；`deepeval_adapter`
也是在函数内导入，保证没装 `[eval]` 时那条链路仍可 import。
"""

import asyncio
import json
from typing import Any

from deepeval.models import DeepEvalBaseLLM

from quality.judge import JudgeConfig, judge_completion, judge_config_from_environment

INSTRUCTION = "请执行评测要求，仅返回有效 JSON，不添加解释性前缀。"


class DeepEvalJudgeLLM(DeepEvalBaseLLM):
    """唯一给 DeepEval 用的 Judge 适配器（面试链路与质量链路共用这一个）。"""

    def __init__(self, config: JudgeConfig | None = None) -> None:
        self.config = config or judge_config_from_environment()
        super().__init__(model=self.config.model)

    def load_model(self) -> None:
        # 请求由 `judge_completion` 按次创建客户端，这里没有长生命周期的模型对象。
        return None

    def get_model_name(self) -> str:
        return self.name or self.config.model

    def _system_prompt(self, schema: type | None) -> str:
        if schema is None:
            return INSTRUCTION
        return INSTRUCTION + "输出必须满足以下 JSON Schema：" + json.dumps(
            schema.model_json_schema(), ensure_ascii=False
        )

    def generate(self, prompt: str, schema: type | None = None) -> Any:
        """按 schema 返回校验过的对象；结构不合法时重试，最多 `max_attempts` 次。

        只重试「返回了但结构不对」这种情况——网络与鉴权错误直接抛出，重试只会
        把失败面拖长。
        """
        system = self._system_prompt(schema)
        attempts = max(1, self.config.max_attempts)
        for attempt in range(1, attempts + 1):
            content = judge_completion(self.config, system, prompt)
            if schema is None:
                return content
            try:
                return schema.model_validate_json(content)
            except ValueError:
                # JSONDecodeError 与 pydantic.ValidationError 都是 ValueError 子类。
                if attempt == attempts:
                    raise
        raise AssertionError("unreachable")

    async def a_generate(self, prompt: str, schema: type | None = None) -> Any:
        return await asyncio.to_thread(self.generate, prompt, schema)
