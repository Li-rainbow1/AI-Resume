"""通过 OpenAI 兼容接口调用配置的 Judge，并校验 DeepEval 请求的 JSON 结构。"""

import asyncio
import json
import os

from deepeval.models import DeepEvalBaseLLM
from openai import OpenAI


class InterviewJudge(DeepEvalBaseLLM):
    def __init__(self):
        self.client = OpenAI(api_key=os.environ["DEEPEVAL_JUDGE_API_KEY"],
                             base_url=os.environ["DEEPEVAL_JUDGE_BASE_URL"], timeout=60, max_retries=0)
        super().__init__(model=os.environ["DEEPEVAL_JUDGE_MODEL"])

    def load_model(self):
        return self.client

    def get_model_name(self):
        return self.name

    def generate(self, prompt, schema=None):
        instruction = "请执行评测要求，仅返回有效 JSON，不添加解释性前缀。"
        if schema is not None:
            instruction += "输出必须满足以下 JSON Schema：" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        response = self.client.chat.completions.create(
            model=self.name, temperature=0, response_format={"type": "json_object"},
            extra_body={"enable_thinking": False},
            messages=[{"role": "system", "content": instruction}, {"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content or ""
        return schema.model_validate_json(content) if schema is not None else content

    async def a_generate(self, prompt, schema=None):
        return await asyncio.to_thread(self.generate, prompt, schema)
