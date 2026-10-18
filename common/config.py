"""配置加载：从 .env / 环境变量读取全部参数。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*_a, **_k):  # type: ignore
        return False

ROOT_DIR = Path(__file__).resolve().parent.parent


def _as_path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (ROOT_DIR / p).resolve()


@dataclass
class WorkflowConfig:
    # LLM
    api_key: str = ""
    base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4o-mini"

    # Workflow
    max_rounds: int = 3
    node_retries: int = 2
    max_retries: int = 3
    code_timeout: int = 10
    enable_parallel: bool = True

    # 路径
    workspace: Path = field(default_factory=lambda: ROOT_DIR / "sandbox")
    runs_dir: Path = field(default_factory=lambda: ROOT_DIR / "runs")
    web_port: int = 5000

    @classmethod
    def load(cls, env_file: str | Path | None = None) -> "WorkflowConfig":
        env_path = Path(env_file) if env_file else ROOT_DIR / ".env"
        if env_path.exists():
            load_dotenv(env_path)

        return cls(
            api_key=os.getenv("OPENAI_API_KEY", "").strip(),
            base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip(),
            model_name=os.getenv("MODEL_NAME", "gpt-4o-mini").strip(),
            max_rounds=int(os.getenv("MAX_ROUNDS", "3")),
            node_retries=int(os.getenv("NODE_RETRIES", "2")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            code_timeout=int(os.getenv("CODE_TIMEOUT", "10")),
            enable_parallel=os.getenv("ENABLE_PARALLEL", "true").lower() in ("1", "true", "yes"),
            workspace=_as_path(os.getenv("WORKSPACE", str(ROOT_DIR / "sandbox"))),
            runs_dir=_as_path(os.getenv("RUNS_DIR", str(ROOT_DIR / "runs"))),
            web_port=int(os.getenv("WEB_PORT", "5000")),
        )

    def ensure_dirs(self) -> None:
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)

    def has_credentials(self) -> bool:
        return bool(self.api_key)
