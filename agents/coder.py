"""编码 Agent：根据需求生成初版代码与自检断言。"""
from __future__ import annotations

from common.llm import request_json
from workflow.base_node import BaseNode
from workflow.state import WorkflowState
from .prompts import coder_messages


class CoderAgent(BaseNode):
    name = "coder"
    label = "编码Agent"

    def __init__(self, llm):
        self._llm = llm

    def run(self, state: WorkflowState) -> dict:
        data = request_json(self._llm, coder_messages(state.requirement))
        code = data.get("code", "")
        self_check = data.get("self_check", "")
        if not code:
            raise ValueError("编码 Agent 未返回 code")
        state.add_version("coder", code, self_check, data.get("note", ""))
        return {"code": code, "self_check": self_check}
