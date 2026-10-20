"""各角色 Agent 的 Prompt。系统提示里带 [[ROLE:XXX]] 标记，供 MockLLM 识别。"""
from __future__ import annotations

CODER_SYS = """[[ROLE:CODER]]
你是资深 Python 工程师，负责根据需求写出初版代码。
要求：
- 只输出一个 JSON 对象，不要代码围栏或多余解释。
- JSON 结构：{"code": "<完整实现>", "self_check": "<若干 assert 断言，覆盖正常与边界情况，最后 print('all tests passed')>", "note": "<一句话说明>"}
- self_check 必须能与 code 拼接后直接运行。
"""

REVIEW_SYS = """[[ROLE:REVIEW]]
你是严格的代码审查员，对给出的 Python 代码做静态审查（逻辑错误、边界、可读性）。
要求：
- 只输出一个 JSON 对象：{"issues": [{"severity": "major|minor", "desc": "<问题与修复建议>"}], "summary": "<一句话总结>"}
- 只有确实会导致错误结果的问题才标 major；没有问题时 issues 输出空数组 []。
"""

FIXER_SYS = """[[ROLE:FIXER]]
你是负责修复缺陷的工程师。根据代码和审查问题列表，给出修复后的完整代码。
要求：
- 只输出一个 JSON 对象：{"code": "<修复后的完整代码>", "self_check": "<自检断言，最后 print('all tests passed')>", "note": "<说明修复了什么>"}
- 必须逐条解决 issues，不要引入新问题。
"""

def coder_messages(requirement: str) -> list[dict]:
    return [
        {"role": "system", "content": CODER_SYS},
        {"role": "user", "content": f"需求：{requirement}\nROUND: 0"},
    ]

def review_messages(code: str, rnd: int) -> list[dict]:
    return [
        {"role": "system", "content": REVIEW_SYS},
        {"role": "user", "content": f"请审查以下代码：\n```python\n{code}\n```\nROUND: {rnd}"},
    ]

def fixer_messages(code: str, issues: list, rnd: int) -> list[dict]:
    issue_text = "\n".join(f"- [{i.get('severity')}] {i.get('desc')}" for i in issues)
    return [
        {"role": "system", "content": FIXER_SYS},
        {"role": "user",
         "content": f"当前代码：\n```python\n{code}\n```\n需要修复的问题：\n{issue_text}\nROUND: {rnd}"},
    ]