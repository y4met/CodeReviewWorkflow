"""工具：子进程执行 Python，捕获 stdout/stderr，带超时与工作目录隔离。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .base import Tool


class PythonRunner(Tool):
    name = "run_python"
    description = "执行 Python 代码并返回 stdout/stderr。参数: code (str)。"

    def __init__(self, timeout: int = 10, workspace: Path | None = None):
        self._timeout = timeout
        self._workspace = workspace or Path(".")

    def run(self, args: dict) -> str:
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return "Error: 缺少参数 code（非空字符串）。"
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", code],
                capture_output=True, text=True,
                timeout=self._timeout, cwd=str(self._workspace),
            )
        except subprocess.TimeoutExpired:
            return f"Error: 代码执行超过 {self._timeout}s 被终止，可能存在死循环。"
        except Exception as e:
            return f"Error: 执行失败: {e}"

        parts = []
        if proc.stdout:
            parts.append(f"[stdout]\n{proc.stdout.rstrip()}")
        if proc.stderr:
            parts.append(f"[stderr]\n{proc.stderr.rstrip()}")
        if not parts:
            parts.append("(无输出)")
        if proc.returncode != 0:
            parts.append(f"(退出码 {proc.returncode}，失败)")
        return "\n".join(parts)
