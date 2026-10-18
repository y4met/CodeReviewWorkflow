"""工具：安全读取工作目录内文件（防路径逃逸）。"""
from __future__ import annotations

from pathlib import Path
from .base import Tool

MAX_BYTES = 64 * 1024


class FileReader(Tool):
    name = "read_file"
    description = "读取工作目录中的文本文件。参数: path (str)。"

    def __init__(self, workspace: Path | None = None):
        self._workspace = (workspace or Path(".")).resolve()

    def run(self, args: dict) -> str:
        rel = args.get("path")
        if not isinstance(rel, str) or not rel:
            return "Error: 缺少参数 path。"
        target = (self._workspace / rel).resolve()
        try:
            target.relative_to(self._workspace)
        except ValueError:
            return f"Error: 禁止访问工作目录之外的路径: {rel}"
        if not target.exists():
            return f"Error: 文件不存在: {rel}"
        if target.is_dir():
            return f"Error: {rel} 是目录。"
        data = target.read_bytes()[:MAX_BYTES]
        return f"[文件 {rel}，{len(data)} 字节]\n{data.decode('utf-8', errors='replace')}"
