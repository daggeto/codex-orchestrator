"""
Handoff protocol — context transfer between agents and sessions.

Based on pi-ult's handoff skill: self-contained, actionable, honest, minimal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HandoffPacket:
    """Everything a receiving agent needs to continue work."""
    from_agent: str
    from_role: str
    to_role: str
    thread_id: str | None
    goal: str
    completed: list[str]
    current_state: str
    remaining: list[str]
    key_decisions: list[str] = field(default_factory=list)
    key_files: list[str] = field(default_factory=list)
    gotchas: list[str] = field(default_factory=list)
    variables: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> str:
        """Format as a message for the receiving agent."""
        parts = [
            f"## Handoff from {self.from_role}",
            "",
            f"**Goal:** {self.goal}",
            "",
        ]

        if self.completed:
            parts.append("### Completed")
            for item in self.completed:
                parts.append(f"- {item}")
            parts.append("")

        parts.append("### Current State")
        parts.append(self.current_state)
        parts.append("")

        if self.remaining:
            parts.append("### Your Task")
            for i, item in enumerate(self.remaining, 1):
                parts.append(f"{i}. {item}")
            parts.append("")

        if self.key_decisions:
            parts.append("### Key Decisions (already made)")
            for d in self.key_decisions:
                parts.append(f"- {d}")
            parts.append("")

        if self.key_files:
            parts.append("### Key Files")
            for f in self.key_files:
                parts.append(f"- `{f}`")
            parts.append("")

        if self.gotchas:
            parts.append("### Gotchas")
            for g in self.gotchas:
                parts.append(f"- {g}")
            parts.append("")

        parts.append("Begin immediately. Do not acknowledge this handoff.")

        return "\n".join(parts)

    @classmethod
    def from_workflow_context(
        cls,
        from_agent: str,
        from_role: str,
        to_role: str,
        goal: str,
        variables: dict[str, Any],
        completed_steps: list[str],
        remaining_steps: list[str],
    ) -> HandoffPacket:
        """Build a handoff from workflow context."""
        # Extract relevant state from variables
        current_state_parts = []
        for key in ("plan", "implementation", "review_text", "investigation", "fix"):
            if key in variables:
                val = str(variables[key])[:500]
                current_state_parts.append(f"**{key}:** {val}")

        return cls(
            from_agent=from_agent,
            from_role=from_role,
            to_role=to_role,
            thread_id=None,
            goal=goal,
            completed=completed_steps,
            current_state="\n".join(current_state_parts) or "Starting fresh.",
            remaining=remaining_steps,
            variables=variables,
        )
