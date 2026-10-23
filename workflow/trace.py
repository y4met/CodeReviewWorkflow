"""RunStore：检查点与执行追踪的持久化。

每次运行对应目录 runs/<run_id>/：
  checkpoint.json   最新完整状态（用于断点恢复）
  trace.json        事件流（执行日志/状态追踪）

写入采用「临时文件 + 原子替换」，避免进程中断留下半个 JSON。
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from .state import WorkflowState


def _write_atomic(path: Path, data) -> None:
    """同目录临时文件写完后原子替换，保证读者永远看到完整文件。"""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


class RunStore:
    def __init__(self, runs_dir: Path):
        self._root = Path(runs_dir)
        self._root.mkdir(parents=True, exist_ok=True)

    def _dir(self, run_id: str) -> Path:
        d = self._root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _run_dir(self, run_id: str) -> Path:
        """解析并校验运行目录：拒绝空值、路径分隔符与 .. 逃逸。"""
        rid = str(run_id or "")
        root = self._root.resolve()
        d = (self._root / rid).resolve()
        if not rid or d.parent != root:
            raise ValueError(f"非法 run_id: {run_id!r}")
        return d

    def save(self, state: WorkflowState) -> None:
        d = self._dir(state.run_id)
        # 先写事件流，最后写检查点：检查点是恢复源，必须最后落盘。
        _write_atomic(d / "trace.json", state.events)
        _write_atomic(d / "checkpoint.json", state.to_dict())

    def load(self, run_id: str) -> WorkflowState:
        f = self._run_dir(run_id) / "checkpoint.json"
        if not f.is_file():
            raise FileNotFoundError(f"未找到运行记录: {run_id}")
        return WorkflowState.from_dict(json.loads(f.read_text(encoding="utf-8")))

    def delete_many(self, run_ids: list[str], keep: set[str] | None = None) -> dict:
        """删除指定运行目录；`keep` 中的 run_id 会跳过（用于保护运行中的任务）。"""
        keep_set = set(keep or ())
        deleted: list[str] = []
        skipped: list[str] = []
        failed: list[dict] = []
        seen: set[str] = set()

        for raw in run_ids:
            rid = str(raw)
            if rid in seen:
                continue
            seen.add(rid)
            if rid in keep_set:
                skipped.append(rid)
                continue
            try:
                d = self._run_dir(rid)
                if not d.is_dir():
                    failed.append({"run_id": rid, "error": "未找到"})
                    continue
                shutil.rmtree(d)
                deleted.append(rid)
            except Exception as e:
                failed.append({"run_id": rid,
                               "error": f"{e.__class__.__name__}: {e}"})

        return {
            "deleted": len(deleted),
            "deleted_ids": deleted,
            "skipped": skipped,
            "failed": failed,
        }

    def clear(self, keep: set[str] | None = None) -> dict:
        """清除全部历史运行；`keep` 中的记录（如运行中的任务）保留。"""
        ids = [
            d.name for d in self._root.iterdir()
            if d.is_dir() and ((d / "checkpoint.json").is_file()
                               or (d / "trace.json").is_file())
        ]
        return self.delete_many(ids, keep=keep)

    def list_runs(self) -> list[dict]:
        """按 checkpoint 修改时间倒序（run_id 是随机 hex，不能按目录名排序）。"""
        items: list[tuple[float, dict]] = []
        for d in self._root.iterdir():
            cf = d / "checkpoint.json"
            if not cf.is_file():
                continue
            try:
                s = json.loads(cf.read_text(encoding="utf-8"))
                mtime = cf.stat().st_mtime
            except Exception:
                continue
            items.append((mtime, {
                "run_id": s.get("run_id") or d.name,
                "status": s.get("status"),
                "round": s.get("round"),
                "requirement": (s.get("requirement") or "")[:60],
            }))
        items.sort(key=lambda it: it[0], reverse=True)
        return [r for _, r in items]
