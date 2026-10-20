"""节点抽象：BaseNode 与 ParallelNode（并行 fan-out + 容错合并）"""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .state import WorkflowState

class BaseNode:
    name: str = "node"
    label: str = "节点"

    def run(self, state: WorkflowState) -> dict:
        """读取 state，返回需要更新的 patch（dict）。"""
        raise NotImplementedError

class ParallelNode(BaseNode):
    """并行执行多个分支：

    - 分支必须把 state 当只读，通过返回 patch 通信；
    - 任一分支异常不影响其它分支（容错 fan-out），错误随结果交给 merge；
    - 用锁保证多线程写 events 安全。
    """

    def __init__(self, name: str, branches: list[BaseNode], merge, label: str = "并行节点"):
        self.name = name
        self.label = label
        self._branches = branches
        self._merge = merge

    def run(self, state: WorkflowState) -> dict:
        results: dict[str, tuple] = {}
        lock = threading.Lock()

        def work(branch: BaseNode):
            with lock:
                state.add_event(branch.name, "branch_start", "running",
                                message=f"分支开始: {branch.label}")
            t0 = time.time()
            patch, status, err = None, "success", None
            try:
                patch = branch.run(state)
            except Exception as e:  # 分支级容错：错误捕获后仍让其它分支完成
                status, err = "failed", f"{e.__class__.__name__}: {e}"
            dt = int((time.time() - t0) * 1000)
            with lock:
                state.add_event(branch.name, "branch_end", status,
                                message=err or "分支完成", duration_ms=dt)
            return branch.name, patch, status, err

        with ThreadPoolExecutor(max_workers=len(self._branches)) as ex:
            futs = [ex.submit(work, b) for b in self._branches]
            for f in futs:
                name, patch, status, err = f.result()
                results[name] = (patch, status, err)

        return self._merge(results, state)

    def run_sequential(self, state: WorkflowState) -> dict:
        """关闭并行时的顺序执行兜底。"""
        results: dict[str, tuple] = {}
        for branch in self._branches:
            state.add_event(branch.name, "branch_start", "running",
                            message=f"分支开始(顺序): {branch.label}")
            t0 = time.time()
            patch, status, err = None, "success", None
            try:
                patch = branch.run(state)
            except Exception as e:
                status, err = "failed", f"{e.__class__.__name__}: {e}"
            dt = int((time.time() - t0) * 1000)
            state.add_event(branch.name, "branch_end", status,
                            message=err or "分支完成", duration_ms=dt)
            results[branch.name] = (patch, status, err)
        return self._merge(results, state)
