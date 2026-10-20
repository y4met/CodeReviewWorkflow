"""工具集合。"""
from .base import Tool
from .python_runner import PythonRunner
from .file_reader import FileReader
from .code_search import CodeSearch


def build_tools(config):
    return {
        "run_python": PythonRunner(timeout=config.code_timeout, workspace=config.workspace),
        "read_file": FileReader(workspace=config.workspace),
        "search_code": CodeSearch(workspace=config.workspace),
    }


__all__ = ["Tool", "PythonRunner", "FileReader", "CodeSearch", "build_tools"]
