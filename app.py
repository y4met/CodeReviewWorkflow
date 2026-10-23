"""可视化 Workflow 编排服务（Flask）。

启动: python app.py  → 浏览器打开 http://127.0.0.1:5000

提供：
- 流程图（并行/条件/回环）可视化
- 发起运行、实时轮询节点状态与事件时间线
- 历史运行、断点恢复
- 编排控制：并行开关、最大轮次、离线 demo

并发说明：
- 每次运行使用 dataclasses.replace 生成的独立配置，不修改全局 CONFIG；
- API 读取的是 RunStore 落盘后发布的不可变快照，不与工作线程共享可变 state；
- 运行登记表在结束后清理，避免长期运行导致内存无界增长。
"""
from __future__ import annotations

import threading
from dataclasses import replace

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

_LOCK = threading.Lock()
_RUNNING: dict[str, threading.Thread] = {}
_SNAPSHOTS: dict[str, dict] = {}


class AppRunStore(RunStore):
    """每次落盘后发布一份状态快照，API 只读快照。"""

    def save(self, state: WorkflowState) -> None:
        snap = state.to_dict()      # 在引擎主线程、节点边界执行，状态已稳定
        super().save(state)
        with _LOCK:
            _SNAPSHOTS[state.run_id] = snap


STORE = AppRunStore(CONFIG.runs_dir)


def _start(state: WorkflowState, demo: bool, cfg: WorkflowConfig) -> bool:
    """启动工作线程；同一 run 已在运行时返回 False。"""
    rid = state.run_id

    def target():
        try:
            llm = build_llm(cfg, demo=demo)
            nodes, routing = build_pieces(cfg, llm)
            engine = WorkflowEngine(nodes, routing, cfg, STORE)
            engine.run(state)
        except Exception as e:  # 线程级兜底：任何启动期异常都标记 failed 并落盘
            state.status = "failed"
            state.outcome = "failed"
            state.error = f"{e.__class__.__name__}: {e}"
            state.add_event("workflow", "error", message=state.error)
            STORE.save(state)
        finally:
            with _LOCK:
                _RUNNING.pop(rid, None)
                _SNAPSHOTS.pop(rid, None)   # 完成后只保留磁盘记录

    with _LOCK:
        if rid in _RUNNING:
            return False
        thread = threading.Thread(target=target, daemon=True)
        _RUNNING[rid] = thread
    thread.start()
    return True


def _parse_rounds(value, default: int) -> int:
    try:
        rounds = int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        raise ValueError("max_rounds 必须是整数")
    return max(1, min(rounds, 10))


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/graph")
def graph():
    return jsonify(view_graph())


@app.post("/api/run")
def create_run():
    body = request.get_json(force=True) or {}
    requirement = (body.get("requirement") or "").strip()
    if not requirement:
        return jsonify({"error": "requirement 不能为空"}), 400
    demo = bool(body.get("demo", not CONFIG.has_credentials()))
    parallel = bool(body.get("parallel", CONFIG.enable_parallel))
    try:
        rounds = _parse_rounds(body.get("max_rounds"), CONFIG.max_rounds)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # 只读复制配置，避免并发请求互相覆盖
    run_cfg = replace(CONFIG, max_rounds=rounds, enable_parallel=parallel)
    state = WorkflowState(requirement=requirement, enable_parallel=parallel)
    STORE.save(state)               # 先落初始快照，避免启动瞬间查不到
    _start(state, demo, run_cfg)
    return jsonify({"run_id": state.run_id})


@app.get("/api/run/<rid>")
def get_run(rid):
    with _LOCK:
        snap = _SNAPSHOTS.get(rid)
    if snap is not None:
        return jsonify(snap)
    try:
        state = STORE.load(rid)
    except FileNotFoundError:
        return jsonify({"error": "未找到运行"}), 404
    return jsonify(state.to_dict())


@app.get("/api/runs")
def list_runs():
    return jsonify(STORE.list_runs())


@app.delete("/api/runs")
def clear_runs():
    """清除历史运行；body 可选 {run_ids:[...]}，不传则清除全部。

    正在执行的 run 会保留（skipped），避免删除正在写入的检查点。
    """
    body = request.get_json(silent=True) or {}
    raw_ids = body.get("run_ids")
    if raw_ids is not None and not isinstance(raw_ids, list):
        return jsonify({"error": "run_ids 必须是数组"}), 400

    with _LOCK:
        keep = set(_RUNNING)
        if raw_ids is None:
            result = STORE.clear(keep=keep)
        else:
            result = STORE.delete_many([str(x) for x in raw_ids], keep=keep)
        for rid in result["deleted_ids"]:
            _SNAPSHOTS.pop(rid, None)
    return jsonify(result)


@app.post("/api/run/<rid>/resume")
def resume_run(rid):
    try:
        state = STORE.load(rid)
    except FileNotFoundError:
        return jsonify({"error": "未找到运行"}), 404
    if state.status == "success":
        return jsonify({"error": "该运行已结束"}), 400
    with _LOCK:
        if rid in _RUNNING:
            return jsonify({"error": "该运行正在执行，不能重复恢复"}), 409

    body = request.get_json(silent=True) or {}
    demo = bool(body.get("demo", not CONFIG.has_credentials()))
    run_cfg = replace(CONFIG, enable_parallel=state.enable_parallel)
    STORE.save(state)               # 刷新快照，让前端立即看到状态
    if not _start(state, demo, run_cfg):
        return jsonify({"error": "该运行正在执行，不能重复恢复"}), 409
    return jsonify({"run_id": state.run_id})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=CONFIG.web_port, debug=False, threaded=True)
