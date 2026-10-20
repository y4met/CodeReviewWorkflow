"""WorkflowState：所有节点共享的黑板状态。

- 单一事实来源：code（当前代码）
- 记录每版代码（versions）、审查结论、并行分支结果、事件流（events）
- 可序列化为 JSON 落盘（检查点），并可从 JSON 恢复
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


@dataclass
class CodeVersion:
    round: int
    author: str          # coder / fixer
    code: str
    self_check: str = ""
    note: str = ""
    ts: str = field(default_factory=now_iso)


@dataclass
class Event:
    ts: str
    node: str
    kind: str            # node_start/node_end/branch_start/branch_end/info/error/router
    status: str = ""     # running/success/failed/skipped
    duration_ms: int = 0
    message: str = ""
    data: dict = field(default_factory=dict)


@dataclass
class WorkflowState:
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    requirement: str = ""

    # 产物
    code: str = ""
    self_check: str = ""
    # 审查结果
    static_issues: list = field(default_factory=list)
    test_output: str = ""
    tests_passed: bool = False
    issues: list = field(default_factory=list)
    review_summary: str = ""
    review_passed: bool = False

    # 流程
    round: int = 0
    next_node: str = "coder"     # 检查点恢复时从该节点继续
    status: str = "created"      # created/running/success/failed
    enable_parallel: bool = True
    error: str | None = None

    versions: list = field(default_factory=list)
    events: list = field(default_factory=list)

    # 事件
    def add_event(self, node: str, kind: str, status: str = "",
                  message: str = "", duration_ms: int = 0, data: dict | None = None) -> None:
        self.events.append(asdict(Event(
            ts=now_iso(), node=node, kind=kind, status=status,
            duration_ms=duration_ms, message=message, data=data or {})))

    # 版本
    def add_version(self, author: str, code: str, self_check: str = "", note: str = "") -> None:
        self.versions.append(asdict(CodeVersion(
            round=self.round, author=author, code=code,
            self_check=self_check, note=note)))

    # patch
    def apply(self, patch: dict | None) -> None:
        """节点返回的 dict 合并进状态（仅更新已存在字段，防止拼错键污染状态）。"""
        if not patch:
            return
        for k, v in patch.items():
            if hasattr(self, k):
                setattr(self, k, v)

    # 序列化
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "WorkflowState":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})
