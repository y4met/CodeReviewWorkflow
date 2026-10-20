"""极简彩色日志（Windows 10+ 终端支持 ANSI）。"""
from __future__ import annotations

import sys

_COLORS = {
    "reset": "\033[0m",
    "gray": "\033[90m",
    "green": "\033[92m",
    "yellow": "\033[93m",
    "red": "\033[91m",
    "cyan": "\033[96m",
    "magenta": "\033[95m",
}


def cprint(text: str, color: str = "", file=None) -> None:
    if color and sys.stdout.isatty():
        print(f"{_COLORS.get(color,'')}{text}{_COLORS['reset']}", file=file)
    else:
        print(text, file=file)


def banner(text: str, color: str = "cyan") -> None:
    line = "─" * max(8, len(text) + 4)
    cprint(line, color)
    cprint(f"  {text}", color)
    cprint(line, color)
