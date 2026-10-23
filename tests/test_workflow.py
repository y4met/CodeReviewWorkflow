"""端到端测试：离线验证并行、回环、检查点恢复、并发配置隔离与边界情况。

运行: .venv\\Scripts\\python.exe tests\\test_workflow.py
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.config import WorkflowConfig
from common.llm import MockLLM
from tools import build_tools
from workflow.engine import WorkflowEngine
from workflow.review_flow import build_pieces, view_graph
from workflow.state import WorkflowState
from workflow.trace import RunStore

FIXED_CODE = (
    "def fib(n):\n"
    "    if n <= 1:\n"
    "        return n\n"
    "    a, b = 0, 1\n"
    "    for _ in range(n):\n"
    "        a, b = b, a + b\n"
    "    return a\n"
)
SELF_CHECK = (
    "assert fib(0) == 0\n"
    "assert fib(1) == 1\n"
    "assert fib(10) == 55, fib(10)\n"
    "print('all tests passed')\n"
)


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
    assert state.outcome == "approved", state.outcome
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
    assert state.outcome == "approved"
    print("[PASS] 关闭并行（顺序执行）同样跑通")


def test_max_rounds_graceful_stop(tmp: Path):
    # 最大轮次=1：首轮未通过即应优雅结束，passed=False，不无限循环
    cfg = make_config(tmp, max_rounds=1)
    state = make_engine(cfg).run(WorkflowState(requirement="实现 fib(n)"))
    assert state.status == "success"          # 流程本身正常结束
    assert state.review_passed is False       # 但未通过审查
    assert state.outcome == "max_rounds"      # 交付结论区分「未通过」
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
    assert recovered.outcome == "approved"
    print("[PASS] 检查点落盘 -> resume 从 fixer 继续并成功")


def test_tool_runtime_catches_error(tmp: Path):
    cfg = make_config(tmp)
    tools = build_tools(cfg)
    out = tools["run_python"].run({"code": "raise ValueError('x')"})
    assert "ValueError" in out
    print("[PASS] 工具捕获运行时错误")


def test_runtime_uses_exit_code(tmp: Path):
    """通过判定以退出码为准，不再被输出里的 Error/失败 等字样误伤。"""
    from agents.reviewer import RuntimeTestBranch

    cfg = make_config(tmp)
    runner = build_tools(cfg)["run_python"]
    branch = RuntimeTestBranch(runner)

    with_marker = branch.run(WorkflowState(
        code="print('Error: 这只是日志')\n",
        self_check="print('all tests passed')"))
    assert with_marker["tests_passed"] is True, with_marker["test_output"]

    failed = branch.run(WorkflowState(
        code="assert False\n",
        self_check="print('all tests passed')"))
    assert failed["tests_passed"] is False

    # 没有 self_check 时只看退出码
    no_check = branch.run(WorkflowState(code="x = 1\n", self_check=""))
    assert no_check["tests_passed"] is True

    res = runner.run_result({"code": "print('Error: x'); print('all tests passed')"})
    assert res.ok and res.returncode == 0
    print("[PASS] 动态测试以退出码为准（含 Error 文本不再误判）")


def test_static_review_failure_is_fail_closed(tmp: Path):
    """静态审查分支异常时，即使动态测试通过也不能判 review_passed。"""
    from agents.reviewer import StaticReviewBranch

    cfg = make_config(tmp, max_rounds=2)
    original = StaticReviewBranch.run

    def boom(self, state):
        raise RuntimeError("llm down")

    StaticReviewBranch.run = boom
    try:
        nodes, routing = build_pieces(cfg, MockLLM())
        engine = WorkflowEngine(nodes, routing, cfg, RunStore(cfg.runs_dir))
        state = WorkflowState(
            requirement="实现 fib(n)",
            code=FIXED_CODE,
            self_check=SELF_CHECK,
            round=0,
            next_node="review",
            status="failed",
        )
        state = engine.run(state)
    finally:
        StaticReviewBranch.run = original

    assert state.status == "success"
    assert state.review_passed is False
    assert state.outcome == "max_rounds", state.outcome
    assert any("fail-closed" in i.get("desc", "") for i in state.static_issues)
    print("[PASS] 静态审查异常 -> fail-closed，不被动态测试通过掩盖")


def test_list_runs_sorted_by_time(tmp: Path):
    cfg = make_config(tmp)
    store = RunStore(cfg.runs_dir)
    first = WorkflowState(requirement="旧运行")
    store.save(first)
    second = WorkflowState(requirement="新运行")
    store.save(second)

    now = time.time()
    os.utime(cfg.runs_dir / first.run_id / "checkpoint.json", (now - 60, now - 60))
    os.utime(cfg.runs_dir / second.run_id / "checkpoint.json", (now, now))

    runs = store.list_runs()
    assert [r["run_id"] for r in runs] == [second.run_id, first.run_id], runs
    # 原子写入不应留下临时文件
    assert not list(cfg.runs_dir.rglob("*.tmp"))
    print("[PASS] 历史运行按 checkpoint 时间倒序")


def test_clear_runs_and_path_safety(tmp: Path):
    cfg = make_config(tmp)
    store = RunStore(cfg.runs_dir)
    keep = WorkflowState(requirement="运行中")
    gone = WorkflowState(requirement="已结束")
    store.save(keep)
    store.save(gone)

    # keep 中的记录（运行中）保留，其余清除
    result = store.clear(keep={keep.run_id})
    assert result["deleted"] == 1, result
    assert result["deleted_ids"] == [gone.run_id]
    assert result["skipped"] == [keep.run_id]
    assert (cfg.runs_dir / keep.run_id / "checkpoint.json").is_file()
    assert not (cfg.runs_dir / gone.run_id).exists()

    # 加载不存在的运行不应顺手创建空目录
    before = {p.name for p in cfg.runs_dir.iterdir()}
    try:
        store.load("deadbeef0000")
        raise AssertionError("应当抛出 FileNotFoundError")
    except FileNotFoundError:
        pass
    assert {p.name for p in cfg.runs_dir.iterdir()} == before

    # 路径逃逸必须被拒绝
    escaped = store.delete_many(["../escape"])
    assert escaped["deleted"] == 0 and escaped["failed"], escaped
    assert not (cfg.runs_dir.parent / "escape").exists()

    # 指定 run_id 删除
    deleted = store.delete_many([keep.run_id])
    assert deleted["deleted_ids"] == [keep.run_id]
    assert store.list_runs() == []
    print("[PASS] 清除历史：保留运行中记录、路径逃逸防护、按 run_id 删除")


def test_cli_exit_code_semantics():
    from run import exit_code

    assert exit_code(WorkflowState(status="success", review_passed=True)) == 0
    assert exit_code(WorkflowState(
        status="success", review_passed=False, outcome="max_rounds")) == 1
    assert exit_code(WorkflowState(status="failed", review_passed=False)) == 1
    print("[PASS] CLI 退出码区分「通过」与「达到上限未通过」")


def test_app_per_run_config_isolated(tmp: Path):
    """Web 请求参数不应修改全局 CONFIG；接口返回 outcome。"""
    import app as web

    cfg = make_config(tmp, max_rounds=3, enable_parallel=True)
    web.CONFIG = cfg
    web.STORE = web.AppRunStore(cfg.runs_dir)
    client = web.app.test_client()

    resp = client.post("/api/run", json={
        "requirement": "实现 fib(n)", "demo": True,
        "parallel": True, "max_rounds": 1,
    })
    assert resp.status_code == 200, resp.get_data(as_text=True)
    rid = resp.get_json()["run_id"]

    data = {}
    deadline = time.time() + 10
    while time.time() < deadline:
        data = client.get(f"/api/run/{rid}").get_json()
        if data.get("status") in ("success", "failed"):
            break
        time.sleep(0.05)

    assert cfg.max_rounds == 3, "全局 CONFIG.max_rounds 不应被请求覆盖"
    assert data.get("outcome") == "max_rounds", data
    assert data.get("review_passed") is False

    # 等线程彻底退出后再清理
    for _ in range(100):
        with web._LOCK:
            if rid not in web._RUNNING:
                break
        time.sleep(0.02)

    resp = client.delete("/api/runs")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    cleared = resp.get_json()
    assert rid in cleared["deleted_ids"], cleared
    assert client.get("/api/runs").get_json() == []
    assert client.get(f"/api/run/{rid}").status_code == 404
    print("[PASS] Web 每次运行配置隔离，且历史清除接口生效")


if __name__ == "__main__":
    # 临时目录放在仓库根目录，且用普通 mkdir 创建：
    base = Path(__file__).resolve().parent.parent / ".test-tmp"
    shutil.rmtree(base, ignore_errors=True)
    base.mkdir(parents=True, exist_ok=True)
    tmp = base / f"case-{os.getpid()}-{int(time.time() * 1000)}"
    tmp.mkdir()
    try:
        test_view_graph_shape()
        test_full_workflow_parallel(tmp / "full")
        test_sequential_mode(tmp / "sequential")
        test_max_rounds_graceful_stop(tmp / "max_rounds")
        test_checkpoint_resume(tmp / "resume")
        test_tool_runtime_catches_error(tmp / "tool_error")
        test_runtime_uses_exit_code(tmp / "runtime")
        test_static_review_failure_is_fail_closed(tmp / "fail_closed")
        test_list_runs_sorted_by_time(tmp / "list_runs")
        test_clear_runs_and_path_safety(tmp / "clear")
        test_cli_exit_code_semantics()
        test_app_per_run_config_isolated(tmp / "web")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print("\n全部测试通过")
