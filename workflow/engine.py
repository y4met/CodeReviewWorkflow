"""WorkflowEngine：通用编排引擎。

能力：
- 顺序执行：节点 -> 边 -> 下一个节点
- 条件分支/循环：routing 表中可放路由函数，返回下一节点名或 END
- 节点级重试：失败按指数退避重试 node_retries 次
- 检查点：每个节点完成并路由后落盘，next_node 指向下一节点
- 恢复：传入已加载的 state 即从 next_node 继续
"""
from __future__ import annotations

import time
from typing import Callable

from .state import WorkflowState
from .trace import RunStore

END = "__END__"


class WorkflowEngine:
    def __init__(self, nodes: dict, routing: dict, config, store: RunStore):
        self._nodes = nodes
        self._routing = routing
        self._cfg = config
        self._store = store

    def _route(self, current: str, state: WorkflowState) -> str:
        r = self._routing.get(current, END)
        return r(state) if callable(r) else r

    def _execute_with_retry(self, node, state: WorkflowState) -> dict | None:
        attempts = self._cfg.node_retries + 1
        last_err = ""
        for attempt in range(1, attempts + 1):
            state.add_event(node.name, "node_start", "running",
                            message=f"{node.label} 开始（第 {attempt} 次尝试）")
            t0 = time.time()
            try:
                patch = node.run(state)
                dt = int((time.time() - t0) * 1000)
                state.add_event(node.name, "node_end", "success",
                                message=f"{node.label} 完成", duration_ms=dt)
                return patch
            except Exception as e:
                dt = int((time.time() - t0) * 1000)
                last_err = f"{e.__class__.__name__}: {e}"
                state.add_event(node.name, "node_end", "failed",
                                message=last_err, duration_ms=dt)
                if attempt < attempts:
                    wait = 2 ** (attempt - 1)
                    state.add_event(node.name, "info",
                                    message=f"{wait}s 后重试节点...")
                    time.sleep(wait)
        return None  # 重试耗尽

    def run(self, state: WorkflowState) -> WorkflowState:
        state.status = "running"
        state.error = None
        current = state.next_node
        self._store.save(state)

        while current != END:
            node = self._nodes.get(current)
            if node is None:
                state.status = "failed"
                state.outcome = "failed"
                state.error = f"未知节点: {current}"
                state.add_event(current, "error", message=state.error)
                self._store.save(state)
                return state

            patch = self._execute_with_retry(node, state)
            if patch is None:
                state.status = "failed"
                state.outcome = "failed"
                state.error = f"节点 {current} 重试 {self._cfg.node_retries} 次后仍失败，已停止（可 resume 恢复）"
                state.add_event(current, "error", message=state.error)
                state.next_node = current
                self._store.save(state)
                return state

            state.apply(patch)

            nxt = self._route(current, state)
            state.next_node = nxt
            state.add_event(current, "router", message=f"路由 -> {nxt}")
            self._store.save(state)  # 检查点：完成节点 + 确定下一节点
            current = nxt

        state.status = "success"
        state.add_event("workflow", "info", message="流程成功结束")
        self._store.save(state)
        return state
