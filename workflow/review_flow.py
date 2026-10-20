"""代码审查流程定义：执行节点、条件路由、回环，以及可视化视图布局。"""
from __future__ import annotations

from agents import CoderAgent, ReviewerAgent, FixerAgent
from tools import build_tools
from .engine import END
from .trace import RunStore

def build_pieces(config, llm):
    """返回 (nodes, routing) 供引擎使用。"""
    tools = build_tools(config)

    nodes = {
        "coder": CoderAgent(llm),
        "review": ReviewerAgent(llm, tools["run_python"]),
        "fixer": FixerAgent(llm),
    }

    def review_router(state) -> str:
        if state.review_passed:
            return END
        if state.round >= config.max_rounds:
            # 达到轮次上限仍未通过也结束（避免无限循环），结论标记未通过
            return END
        return "fixer"

    routing = {
        "coder": "review",
        "review": review_router,
        "fixer": "review",
    }
    return nodes, routing

def view_graph() -> dict:
    """给前端可视化用的图结构（含并行分支、条件分支、回环）与坐标布局。"""
    nodes = [
        {"id": "coder", "label": "编码Agent", "type": "task", "x": 40, "y": 175},
        {"id": "static_review", "label": "静态审查(LLM)", "type": "branch", "x": 285, "y": 70},
        {"id": "runtime_test", "label": "动态运行测试", "type": "branch", "x": 285, "y": 280},
        {"id": "decision", "label": "审查通过?", "type": "decision", "x": 530, "y": 175},
        {"id": "fixer", "label": "修复Agent", "type": "task", "x": 530, "y": 330},
        {"id": "end", "label": "交付代码", "type": "end", "x": 780, "y": 175},
    ]
    edges = [
        {"from": "coder", "to": "static_review", "kind": "fork"},
        {"from": "coder", "to": "runtime_test", "kind": "fork"},
        {"from": "static_review", "to": "decision", "kind": "join"},
        {"from": "runtime_test", "to": "decision", "kind": "join"},
        {"from": "decision", "to": "end", "label": "通过", "kind": "success"},
        {"from": "decision", "to": "fixer", "label": "不通过", "kind": "fail"},
        {"from": "fixer", "to": "static_review", "label": "重新审查", "kind": "loop"},
        {"from": "fixer", "to": "runtime_test", "kind": "loop"},
    ]
    return {"nodes": nodes, "edges": edges}
