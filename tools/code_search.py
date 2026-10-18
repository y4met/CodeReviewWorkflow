"""工具：在工作目录按正则/子串搜索代码。"""
from __future__ import annotations

import re
from pathlib import Path
from .base import Tool

_IGNORE_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv"}
_TEXT_EXTS = {".py", ".md", ".txt", ".js", ".ts", ".java", ".c", ".cpp", ".h", ".json", ".yml", ".yaml"}
MAX_MATCHES = 30


class CodeSearch(Tool):
    name = "search_code"
    description = "搜索包含关键字/正则的代码行。参数: query (str)，可选 file_ext (str)。"

    def __init__(self, workspace: Path | None = None):
        self._workspace = (workspace or Path(".")).resolve()

    def run(self, args: dict) -> str:
        query = args.get("query")
        if not isinstance(query, str) or not query:
            return "Error: 缺少参数 query。"
        ext = args.get("file_ext")
        try:
            pattern = re.compile(query, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(query), re.IGNORECASE)

        matches: list[str] = []
        for p in self._workspace.rglob("*"):
            if len(matches) >= MAX_MATCHES:
                break
            if not p.is_file() or any(part in _IGNORE_DIRS for part in p.parts):
                continue
            if ext and p.suffix != ext:
                continue
            if p.suffix not in _TEXT_EXTS:
                continue
            try:
                for lineno, line in enumerate(
                    p.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if pattern.search(line):
                        matches.append(f"{p.relative_to(self._workspace)}:{lineno}: {line.strip()[:200]}")
                        if len(matches) >= MAX_MATCHES:
                            break
            except Exception:
                continue
        if not matches:
            return f"未找到匹配 '{query}' 的内容。"
        return f"找到 {len(matches)} 处匹配:\n" + "\n".join(matches)
