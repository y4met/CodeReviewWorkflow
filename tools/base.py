"""工具抽象基类。"""
from __future__ import annotations

import abc


class Tool(abc.ABC):
    name: str = "tool"
    description: str = ""

    @abc.abstractmethod
    def run(self, args: dict) -> str: ...

    def describe(self) -> str:
        return f"- {self.name}: {self.description}"
