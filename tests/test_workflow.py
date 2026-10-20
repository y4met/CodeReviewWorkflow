"""端到端测试：离线验证并行、回环、检查点恢复、边界情况。

运行: .venv\\Scripts\\python.exe tests\\test_workflow.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import WorkflowConfig
from common.llm import MockLLM
from tools import build_tools
from workflow.engine import WorkflowEngine
from workflow.review_flow import build_pieces, view_graph
from workflow.state import WorkflowState
from workflow.trace import RunStore


def make_config(tmp: Path, **kw) -> WorkflowConfig:
    cfg = WorkflowConfig.load()
    cfg.workspace = tmp / "sandbox"
    cfg.runs_dir = tmp / "runs"
    cfg.ensure_dirs()
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg


def make_engine(cfg) -> WorkflowEngine:
    nodes, routing = build_pieces(cfg, MockLLM())
    return WorkflowEngine(nodes, routing, cfg, RunStore(cfg.runs_dir))


def test_view_graph_shape():
    g = view_graph()
    ids = {n["id"] for n in g["nodes"]}
    assert {"coder", "static_review", "runtime_test", "decision", "fixer", "end"} <= ids
    kinds = {e["kind"] for e in g["edges"]}
    assert {"fork", "join", "success", "fail", "loop"} <= kinds
    print("[PASS] 视图含并行/条件/回环结构")


def test_full_workflow_parallel(tmp: Path):
    cfg = make_config(tmp)
    state = make_engine(cfg).run(
        WorkflowState(requirement="实现 fib(n)", enable_parallel=True))
    assert state.status == "success"
    assert state.review_passed is True
    assert state.round == 2, f"应两轮通过，实际 {state.round}"
    # 两个并行分支都执行过
    nodes_seen = {e["node"] for e in state.events}
    assert "static_review" in nodes_seen and "runtime_test" in nodes_seen
    # 发生过回环：fixer 被执行
    assert any(e["node"] == "fixer" and e["kind"] == "node_end" for e in state.events)
    # 至少两个代码版本（coder + fixer）
    authors = {v["author"] for v in state.versions}
    assert {"coder", "fixer"} <= authors
    print("[PASS] 完整并行流程：失败->修复->复审通过（2轮）")


def test_sequential_mode(tmp: Path):
    cfg = make_config(tmp, enable_parallel=False)
    state = make_engine(cfg).run(
        WorkflowState(requirement="实现 fib(n)", enable_parallel=False))
    assert state.status == "success" and state.review_passed
    print("[PASS] 关闭并行（顺序执行）同样跑通")


def test_max_rounds_graceful_stop(tmp: Path):
    # 最大轮次=1：首轮未通过即应优雅结束，passed=False，不无限循环
    cfg = make_config(tmp, max_rounds=1)
    state = make_engine(cfg).run(WorkflowState(requirement="实现 fib(n)"))
    assert state.status == "success"          # 流程本身正常结束
    assert state.review_passed is False       # 但未通过审查
    assert state.round == 1
    print("[PASS] 达到最大轮次优雅终止（防死循环）")


def test_checkpoint_resume(tmp: Path):
    cfg = make_config(tmp)
    store = RunStore(cfg.runs_dir)
    engine = make_engine(cfg)

    # 手工构造状态（模拟 coder + 首轮 review 后中断在 fixer 之前）
    state = WorkflowState(
        requirement="实现 fib(n)",
        code="def fib(n):\n    return n\n",  # 占位
        self_check="assert fib(10)==55\nprint('all tests passed')\n",
        round=1, next_node="fixer", status="failed",
        enable_parallel=True,
        issues=[{"severity": "major", "desc": "实现错误"}],
    )
    store.save(state)

    # 从检查点恢复
    loaded = store.load(state.run_id)
    assert loaded.next_node == "fixer" and loaded.status == "failed"
    recovered = engine.run(loaded)
    assert recovered.status == "success" and recovered.review_passed is True
    print("[PASS] 检查点落盘 -> resume 从 fixer 继续并成功")


def test_tool_runtime_catches_error(tmp: Path):
    cfg = make_config(tmp)
    tools = build_tools(cfg)
    out = tools["run_python"].run({"code": "raise ValueError('x')"})
    assert "ValueError" in out
    print("[PASS] 工具捕获运行时错误")


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        test_view_graph_shape()
        test_full_workflow_parallel(tmp)
        test_sequential_mode(tmp)
        test_max_rounds_graceful_stop(tmp)
        test_checkpoint_resume(tmp)
        test_tool_runtime_catches_error(tmp)
    print("\n全部测试通过")
