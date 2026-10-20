"""LLM 抽象层

- BaseLLM.chat(messages) -> str
- OpenAILLM: 任意 OpenAI 兼容服务 + 指数退避重试
- MockLLM: 离线演示，按角色(ROLE)与轮次(ROUND)产出确定性 JSON，跑通含并行与回环的完整流程
- request_json: 向 LLM 请求 JSON，解析失败自动纠错重试
"""
from __future__ import annotations

import abc
import json
import re
import time
from typing import List, Dict

Message = Dict[str, str]

_ROLE_RE = re.compile(r"\[\[ROLE:([A-Z]+)\]\]")
_ROUND_RE = re.compile(r"ROUND:\s*(\d+)")
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_BRACE_RE = re.compile(r"\{.*\}", re.S)


class BaseLLM(abc.ABC):
    @abc.abstractmethod
    def chat(self, messages: List[Message]) -> str: ...


class OpenAILLM(BaseLLM):
    def __init__(self, config):
        if not config.has_credentials():
            raise ValueError(
                "未检测到 OPENAI_API_KEY。请复制 .env.example 为 .env 并填写密钥，"
                "或使用 --demo 运行离线演示。"
            )
        from openai import OpenAI
        self._client = OpenAI(api_key=config.api_key, base_url=config.base_url)
        self._model = config.model_name
        self._max_retries = config.max_retries

    def chat(self, messages: List[Message]) -> str:
        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model, messages=messages, temperature=0.2,  # type: ignore
                )
                return (resp.choices[0].message.content or "").strip()
            except Exception as e:
                last_err = e
                wait = 2 ** (attempt - 1)
                print(f"  [LLM] 第 {attempt} 次调用失败({e.__class__.__name__})，{wait}s 后重试...")
                time.sleep(wait)
        raise RuntimeError(f"LLM 连续失败 {self._max_retries} 次: {last_err}")


# 离线 Mock 剧情 
# 有缺陷的 fib（经典的缺少临时变量 / 覆盖错误）
_BUGGY_CODE = (
    "def fib(n):\n"
    "    if n <= 1:\n"
    "        return n\n"
    "    a, b = 0, 1\n"
    "    for _ in range(n):\n"
    "        a = b\n"
    "        b = a + b\n"
    "    return a\n"
)
_FIXED_CODE = (
    "def fib(n):\n"
    "    if n <= 1:\n"
    "        return n\n"
    "    a, b = 0, 1\n"
    "    for _ in range(n):\n"
    "        a, b = b, a + b\n"
    "    return a\n"
)
_SELF_CHECK = (
    "assert fib(0) == 0\n"
    "assert fib(1) == 1\n"
    "assert fib(10) == 55, fib(10)\n"
    "print('all tests passed')\n"
)


class MockLLM(BaseLLM):
    def chat(self, messages: List[Message]) -> str:
        sys_text = next((m["content"] for m in messages if m["role"] == "system"), "")
        user_text = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        role_m = _ROLE_RE.search(sys_text)
        role = role_m.group(1) if role_m else "UNKNOWN"
        round_m = _ROUND_RE.search(user_text)
        rnd = int(round_m.group(1)) if round_m else 0

        if role == "CODER":
            return json.dumps(
                {"code": _BUGGY_CODE, "self_check": _SELF_CHECK,
                 "note": "初版实现，待审查验证。"}, ensure_ascii=False)

        if role == "REVIEW":
            # 仅第 1 次审查（state.round==0）报告缺陷；修复后的后续轮次均通过
            if rnd == 0:
                return json.dumps(
                    {"issues": [{"severity": "major",
                                 "desc": "循环内先 a=b 再 b=a+b，a 已被覆盖导致 b 计算错误，应使用元组交换 a,b=b,a+b。"}],
                     "summary": "静态审查发现 1 个严重逻辑错误。"}, ensure_ascii=False)
            return json.dumps(
                {"issues": [], "summary": "静态审查未发现逻辑或风格问题。"},
                ensure_ascii=False)

        if role == "FIXER":
            return json.dumps(
                {"code": _FIXED_CODE, "self_check": _SELF_CHECK,
                 "note": "已修复变量覆盖问题，改用元组交换。"}, ensure_ascii=False)

        return json.dumps({"note": "MockLLM 未识别角色。"}, ensure_ascii=False)


def build_llm(config, demo: bool = False) -> BaseLLM:
    return MockLLM() if demo else OpenAILLM(config)


# JSON 请求 + 纠错
def extract_json(text: str) -> dict:
    """从可能带围栏/说明文字的模型输出中抽取 JSON 对象。"""
    m = _FENCE_RE.search(text)
    candidates = []
    if m:
        candidates.append(m.group(1).strip())
    bm = _BRACE_RE.search(text)
    if bm:
        candidates.append(bm.group(0).strip())
    for c in candidates:
        return json.loads(c)
    raise ValueError("输出中未找到 JSON 对象")


def request_json(llm: BaseLLM, messages: List[Message], max_repair: int = 2) -> dict:
    """请求 LLM 返回 JSON；解析失败则把错误回传，要求重写（最多 max_repair 次）。"""
    msgs = list(messages)
    last_err = ""
    for _ in range(max_repair + 1):
        raw = llm.chat(msgs)
        try:
            return extract_json(raw)
        except Exception as e:
            last_err = str(e)
            msgs.append({"role": "assistant", "content": raw})
            msgs.append({"role": "user",
                         "content": f"你刚才的输出无法解析为 JSON（{e}）。请只输出一个合法 JSON 对象，不要加代码围栏或多余文字。"})
    raise ValueError(f"JSON 纠错 {max_repair} 次后仍失败: {last_err}")
