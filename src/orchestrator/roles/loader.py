"""Role configuration loading from TOML files."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RoleConfig:
    name: str
    backend: str = "codex"  # 'codex' or 'claude'
    description: str = ""
    system_prompt: str = ""
    model: str | None = None
    sandbox: str = "workspace-write"
    approval_policy: str = "on-failure"
    max_threads: int = 5
    features: dict[str, bool] = field(default_factory=dict)
    config_overrides: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    # Claude-specific
    claude_flags: list[str] = field(default_factory=list)
    mcp_config: str | None = None
    # Resource limits
    max_turn_seconds: int = 600
    max_tokens_per_turn: int | None = None

    @classmethod
    def from_toml(cls, path: Path) -> RoleConfig:
        with open(path, "rb") as f:
            data = tomllib.load(f)

        role_data = data.get("role", data)

        features = role_data.pop("features", {})
        config_overrides = role_data.pop("config", {})
        claude_flags = role_data.pop("claude_flags", [])
        tags = role_data.pop("tags", [])

        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in role_data.items() if k in known_fields}

        return cls(
            **filtered,
            features=features,
            config_overrides=config_overrides,
            claude_flags=claude_flags,
            tags=tags,
        )


def load_role(path: Path) -> RoleConfig:
    return RoleConfig.from_toml(path)


def load_all_roles(roles_dir: Path) -> dict[str, RoleConfig]:
    roles = {}
    if not roles_dir.exists():
        return roles
    for p in sorted(roles_dir.glob("*.toml")):
        role = RoleConfig.from_toml(p)
        roles[role.name] = role
    return roles
