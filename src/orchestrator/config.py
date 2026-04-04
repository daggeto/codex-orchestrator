"""Global orchestrator configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_DATA_DIR = Path.home() / ".codex-orchestrator"


@dataclass
class OrchestratorConfig:
    data_dir: Path = field(default_factory=lambda: DEFAULT_DATA_DIR)
    roles_dir: Path | None = None
    workflows_dir: Path | None = None
    base_port: int = 9001
    max_agents: int = 20
    health_check_interval: float = 10.0
    default_model: str = "gpt-5.4"
    default_approval_policy: str = "on-failure"
    default_sandbox: str = "workspace-write"
    api_host: str = "127.0.0.1"
    api_port: int = 8420
    log_level: str = "INFO"

    def __post_init__(self):
        self.data_dir = Path(self.data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "logs").mkdir(exist_ok=True)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "orchestrator.db"

    @property
    def effective_roles_dir(self) -> Path:
        if self.roles_dir:
            return Path(self.roles_dir)
        return self.data_dir / "roles"

    @property
    def effective_workflows_dir(self) -> Path:
        if self.workflows_dir:
            return Path(self.workflows_dir)
        return self.data_dir / "workflows"

    @classmethod
    def load(cls, path: Path | None = None) -> OrchestratorConfig:
        if path and path.exists():
            with open(path, "rb") as f:
                data = tomllib.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        config_path = DEFAULT_DATA_DIR / "config.toml"
        if config_path.exists():
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        return cls()
