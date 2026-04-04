"""
Skill system — composable, reusable agent capabilities.

Skills are loaded from TOML files and define:
- What the skill does
- What role(s) can use it
- Input/output schema
- The prompt template
- Verification criteria
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SkillConfig:
    name: str
    description: str = ""
    roles: list[str] = field(default_factory=list)  # which roles can use this
    prompt_template: str = ""
    input_schema: dict[str, str] = field(default_factory=dict)  # name -> description
    output_expectations: list[str] = field(default_factory=list)
    verification: list[str] = field(default_factory=list)  # verification steps
    max_turns: int = 3  # max back-and-forth before giving up
    tags: list[str] = field(default_factory=list)

    def build_prompt(self, variables: dict[str, Any]) -> str:
        """Build the prompt with variables substituted."""
        prompt = self.prompt_template
        for key, value in variables.items():
            prompt = prompt.replace(f"{{{key}}}", str(value))
        return prompt

    @classmethod
    def from_toml(cls, path: Path) -> SkillConfig:
        with open(path, "rb") as f:
            data = tomllib.load(f)

        skill_data = data.get("skill", data)
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in skill_data.items() if k in known_fields}
        return cls(**filtered)


class SkillRegistry:
    """Loads and manages skills."""

    def __init__(self):
        self._skills: dict[str, SkillConfig] = {}

    def load_dir(self, skills_dir: Path):
        if not skills_dir.exists():
            return
        for p in sorted(skills_dir.glob("*.toml")):
            skill = SkillConfig.from_toml(p)
            self._skills[skill.name] = skill

    def register(self, skill: SkillConfig):
        self._skills[skill.name] = skill

    def get(self, name: str) -> SkillConfig | None:
        return self._skills.get(name)

    def list_for_role(self, role: str) -> list[SkillConfig]:
        return [s for s in self._skills.values() if not s.roles or role in s.roles]

    def list_all(self) -> list[SkillConfig]:
        return list(self._skills.values())


# ── Built-in Skills ────────────────────────────────────────────────────

BUILTIN_SKILLS = [
    SkillConfig(
        name="code-review",
        description="Review code changes for quality, security, and correctness",
        roles=["reviewer"],
        prompt_template=(
            "Review the following code changes:\n\n{diff}\n\n"
            "Check for:\n"
            "1. Correctness — does it do what it's supposed to?\n"
            "2. Security — any vulnerabilities?\n"
            "3. Quality — readable, tested, error handling?\n"
            "4. Scope — only changes what was asked?\n\n"
            "End with APPROVED or CHANGES_REQUESTED + numbered issues."
        ),
        input_schema={"diff": "Code diff or description of changes"},
        output_expectations=["APPROVED or CHANGES_REQUESTED", "numbered issues if rejected"],
        verification=["Response contains APPROVED or CHANGES_REQUESTED"],
    ),
    SkillConfig(
        name="implement-plan",
        description="Implement a specific plan step by step",
        roles=["implementer"],
        prompt_template=(
            "Implement the following plan:\n\n{plan}\n\n"
            "Working directory: {cwd}\n\n"
            "Execute each step. After each step, report:\n"
            "- Files changed\n"
            "- Tests run and results\n"
            "- Any issues encountered"
        ),
        input_schema={"plan": "Implementation plan", "cwd": "Working directory"},
        output_expectations=["file changes", "test results", "commit hashes"],
        verification=["Files actually changed", "Tests pass"],
    ),
    SkillConfig(
        name="investigate-bug",
        description="Investigate a bug and identify root cause",
        roles=["architect", "implementer"],
        prompt_template=(
            "Investigate this bug:\n\n{bug_description}\n\n"
            "Working directory: {cwd}\n\n"
            "Find the root cause. Report:\n"
            "1. Root cause (file, function, line)\n"
            "2. Why it happens\n"
            "3. Proposed fix\n"
            "4. Affected areas (what else might break)"
        ),
        input_schema={"bug_description": "Bug report", "cwd": "Working directory"},
        output_expectations=["root cause with file/line", "proposed fix"],
    ),
    SkillConfig(
        name="decompose-task",
        description="Break a large task into work items",
        roles=["planner", "architect"],
        prompt_template=(
            "Decompose this task into work items:\n\n{task}\n\n"
            "Output structured JSON with:\n"
            "- id, title, description\n"
            "- role assignment\n"
            "- priority and complexity (S/M/L/XL)\n"
            "- dependencies\n"
            "- acceptance criteria"
        ),
        input_schema={"task": "Task description"},
        output_expectations=["JSON work item list"],
    ),
    SkillConfig(
        name="run-tests",
        description="Run test suite and report results",
        roles=["qa", "implementer"],
        prompt_template=(
            "Run the test suite in {cwd}.\n\n"
            "Report:\n"
            "- Total tests: passing, failing, skipped\n"
            "- Any new failures since last run\n"
            "- Coverage summary if available"
        ),
        input_schema={"cwd": "Working directory"},
        output_expectations=["test counts", "failure details"],
        verification=["Test command was actually run"],
    ),
    SkillConfig(
        name="summarize-changes",
        description="Summarize recent code changes",
        roles=["architect", "reviewer"],
        prompt_template=(
            "Summarize the recent changes in {cwd}.\n\n"
            "Check git log and diff. Report:\n"
            "- What changed and why\n"
            "- Files affected\n"
            "- Any risks or concerns"
        ),
        input_schema={"cwd": "Working directory"},
        output_expectations=["change summary", "file list"],
    ),
]


def create_default_registry() -> SkillRegistry:
    registry = SkillRegistry()
    for skill in BUILTIN_SKILLS:
        registry.register(skill)
    return registry
