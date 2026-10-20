"""修复 Agent：依据审查 issues 修正代码。"""
from __future__ import annotations

from common.llm import request_json
from workflow.base_node import BaseNode
from workflow.state import WorkflowState
from .prompts import fixer_messages

class FixerAgent(BaseNode):
    name = "fixer"
    label = "修复Agent"

    def __init__(self, llm):
        self._llm = llm

    def run(self, state: WorkflowState) -> dict:
        data = request_json(
            self._llm, fixer_messages(state.code, state.issues, state.round))
        code = data.get("code", "")
        self_check = data.get("self_check", state.self_check)
        if not code:
            raise ValueError("修复 Agent 未返回 code")
        state.add_version("fixer", code, self_check, data.get("note", ""))
        return {"code": code, "self_check": self_check, "review_passed": False}