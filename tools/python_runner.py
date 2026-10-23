"""工具：子进程执行Python，捕获stdout/stderr，带超时与工作目录隔离。

除原有的字符串接口 `run()` 外，额外提供 `run_result()`：
以子进程退出码作为唯一成功判据，避免用输出文本里的关键字猜测成败。
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .base import Tool


@dataclass
class ExecResult:
    """一次子进程执行的完整结果。"""

    ok: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str = ""          # 超时/启动失败等基础设施错误，不属于被测代码输出

    def as_text(self) -> str:
        parts = []
        if self.stdout:
            parts.append(f"[stdout]\n{self.stdout.rstrip()}")
        if self.stderr:
            parts.append(f"[stderr]\n{self.stderr.rstrip()}")
        if self.error:
            parts.append(f"[error]\n{self.error}")
        if not parts:
            parts.append("(无输出)")
        if self.returncode not in (None, 0):
            parts.append(f"(退出码 {self.returncode}，失败)")
        return "\n".join(parts)


class PythonRunner(Tool):
    name = "run_python"
    description = "执行 Python 代码并返回 stdout/stderr。参数: code (str)。"

    def __init__(self, timeout: int = 10, workspace: Path | None = None):
        self._timeout = timeout
        self._workspace = workspace or Path(".")

    def run_result(self, args: dict) -> ExecResult:
        """执行代码，返回结构化结果（退出码为准）。"""
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return ExecResult(False, error="缺少参数 code（非空字符串）。")
        try:
            proc = subprocess.run(
                # -I 隔离会同时忽略 PYTHON* 环境变量，因此显式用 -X utf8
                # 统一子进程 stdio 编码，避免 Windows 中文输出解码失败。
                [sys.executable, "-I", "-X", "utf8", "-c", code],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=self._timeout, cwd=str(self._workspace),
            )
        except subprocess.TimeoutExpired:
            return ExecResult(
                False, error=f"代码执行超过 {self._timeout}s 被终止，可能存在死循环。")
        except Exception as e:
            return ExecResult(False, error=f"执行失败: {e}")

        return ExecResult(
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )

    def run(self, args: dict) -> str:
        """原字符串接口，保持向后兼容。"""
        return self.run_result(args).as_text()
