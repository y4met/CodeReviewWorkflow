"""RunStore：检查点与执行追踪的持久化。

每次运行对应目录 runs/<run_id>/：
  checkpoint.json   最新完整状态（用于断点恢复）
  trace.json        事件流（执行日志/状态追踪）
"""
from __future__ import annotations

import json
from pathlib import Path

from .state import WorkflowState

class RunStore:
    def __init__(self, runs_dir: Path):
        self._root = Path(runs_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _dir(self, run_id: str) -> Path:
        d = self._root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save(self, state: WorkflowState) -> None:
        d = self._dir(state.run_id)
        (d / "checkpoint.json").write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        (d / "trace.json").write_text(
            json.dumps(state.events, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, run_id: str) -> WorkflowState:
        f = self._dir(run_id) / "checkpoint.json"
        if not f.exists():
            raise FileNotFoundError(f"未找到运行记录: {run_id}")
        return WorkflowState.from_dict(json.loads(f.read_text(encoding="utf-8")))

    def list_runs(self) -> list[dict]:
        out = []
        for d in sorted(self._root.iterdir(), reverse=True):
            cf = d / "checkpoint.json"
            if cf.exists():
                try:
                    s = json.loads(cf.read_text(encoding="utf-8"))
                    out.append({"run_id": s.get("run_id"), "status": s.get("status"),
                                "round": s.get("round"),
                                "requirement": (s.get("requirement") or "")[:60]})
                except Exception:
                    continue
        return out
