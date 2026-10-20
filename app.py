"""可视化 Workflow 编排服务（Flask）。

启动: python app.py  → 浏览器打开 http://127.0.0.1:5000

提供：
- 流程图（并行/条件/回环）可视化
- 发起运行、实时轮询节点状态与事件时间线
- 历史运行、断点恢复
- 编排控制：并行开关、最大轮次、离线 demo
"""
from __future__ import annotations

import threading

from flask import Flask, jsonify, render_template, request

from common.config import WorkflowConfig
from common.llm import build_llm
from workflow.engine import WorkflowEngine
from workflow.review_flow import build_pieces, view_graph
from workflow.state import WorkflowState
from workflow.trace import RunStore

app = Flask(__name__)
CONFIG = WorkflowConfig.load()
CONFIG.ensure_dirs()
STORE = RunStore(CONFIG.runs_dir)

# 内存中的活动/最近状态：run_id -> state
_ACTIVE: dict[str, WorkflowState] = {}
_LOCK = threading.Lock()


def _start(state: WorkflowState, demo: bool) -> None:
    def target():
        try:
            llm = build_llm(CONFIG, demo=demo)
            nodes, routing = build_pieces(CONFIG, llm)
            engine = WorkflowEngine(nodes, routing, CONFIG, STORE)
            engine.run(state)
        except Exception as e:  # 线程级兜底：任何启动期异常都标记 failed 并落盘
            state.status = "failed"
            state.error = f"{e.__class__.__name__}: {e}"
            state.add_event("workflow", "error", message=state.error)
            STORE.save(state)
    threading.Thread(target=target, daemon=True).start()


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/graph")
def graph():
    return jsonify(view_graph())


@app.post("/api/run")
def create_run():
    body = request.get_json(force=True)
    requirement = (body.get("requirement") or "").strip()
    if not requirement:
        return jsonify({"error": "requirement 不能为空"}), 400
    demo = bool(body.get("demo", not CONFIG.has_credentials()))
    parallel = bool(body.get("parallel", CONFIG.enable_parallel))
    if body.get("max_rounds"):
        CONFIG.max_rounds = int(body["max_rounds"])
    CONFIG.enable_parallel = parallel

    state = WorkflowState(requirement=requirement, enable_parallel=parallel)
    with _LOCK:
        _ACTIVE[state.run_id] = state
    _start(state, demo)
    return jsonify({"run_id": state.run_id})


@app.get("/api/run/<rid>")
def get_run(rid):
    with _LOCK:
        state = _ACTIVE.get(rid)
    if state is None:
        try:
            state = STORE.load(rid)
        except FileNotFoundError:
            return jsonify({"error": "未找到运行"}), 404
    return jsonify(state.to_dict())


@app.get("/api/runs")
def list_runs():
    return jsonify(STORE.list_runs())


@app.post("/api/run/<rid>/resume")
def resume_run(rid):
    try:
        state = STORE.load(rid)
    except FileNotFoundError:
        return jsonify({"error": "未找到运行"}), 404
    if state.status == "success":
        return jsonify({"error": "该运行已成功"}), 400
    body = request.get_json(silent=True) or {}
    demo = bool(body.get("demo", not CONFIG.has_credentials()))
    with _LOCK:
        _ACTIVE[state.run_id] = state
    _start(state, demo)
    return jsonify({"run_id": state.run_id})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=CONFIG.web_port, debug=False, threaded=True)
