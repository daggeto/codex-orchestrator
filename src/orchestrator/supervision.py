"""
Supervision protocol — prevents agents from blabbing without producing.

Based on the pi-ult supervise skill pattern: never trust messages, always verify externally.
Agents must produce deliverables (commits, test results, file changes), not acknowledgements.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from orchestrator.agents.base import Agent, TurnResult
from orchestrator.events.bus import EventBus

logger = logging.getLogger(__name__)


class DeliverableType(str, Enum):
    COMMIT = "commit"
    TEST_RESULT = "test_result"
    FILE_CHANGE = "file_change"
    ANALYSIS = "analysis"
    PLAN = "plan"
    REVIEW_DECISION = "review_decision"
    CODE_DIFF = "code_diff"
    NONE = "none"


@dataclass
class Deliverable:
    dtype: DeliverableType
    evidence: str
    verified: bool = False
    verification_detail: str = ""


@dataclass
class SupervisionResult:
    """Result of supervising a turn."""
    accepted: bool
    deliverables: list[Deliverable]
    raw_response: str
    issues: list[str] = field(default_factory=list)
    noise_ratio: float = 0.0  # 0.0 = all signal, 1.0 = all noise


# ── Noise Detection ────────────────────────────────────────────────────

NOISE_PATTERNS = [
    r"(?i)^(acknowledged|understood|got it|will do|sure|okay|on it)",
    r"(?i)^(I'll|I will|I'm going to|Let me|I can|I'd be happy to)",
    r"(?i)^(proceeding|continuing|moving on|working on)",
    r"(?i)(sounds good|makes sense|that's correct|absolutely|definitely)",
    r"(?i)(I'll get started|I'll begin|I'll take care of)",
    r"(?i)^(Great|Perfect|Excellent|Wonderful)[\s!.,]",
]

DELIVERABLE_PATTERNS = {
    DeliverableType.COMMIT: [
        r"[0-9a-f]{7,40}",  # git hash
        r"commit\s+[0-9a-f]{7,40}",
    ],
    DeliverableType.TEST_RESULT: [
        r"\d+\s+(tests?|specs?)\s+(pass|fail|running)",
        r"(PASS|FAIL|ERROR)\s+\S+\.test\.",
        r"Tests?:\s+\d+\s+(passed|failed)",
    ],
    DeliverableType.FILE_CHANGE: [
        r"(created|modified|deleted|updated)\s+`?[\w./\\-]+`?",
        r"`[\w./\\-]+\.(py|ts|js|rs|go|toml|json|yaml|md)`",
    ],
    DeliverableType.REVIEW_DECISION: [
        r"\b(APPROVED|CHANGES_REQUESTED|REJECTED)\b",
    ],
    DeliverableType.CODE_DIFF: [
        r"^[+-]{3}\s+[ab]/",
        r"^@@\s+-\d+,\d+\s+\+\d+,\d+\s+@@",
    ],
    DeliverableType.PLAN: [
        r"^\d+\.\s+",  # numbered list
        r"^[-*]\s+\*\*",  # bold bullet points
        r"(?i)(step|phase)\s+\d+",
    ],
}


def detect_noise_ratio(text: str) -> float:
    """Estimate what fraction of the response is noise vs signal."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if not lines:
        return 1.0

    noise_lines = 0
    for line in lines:
        is_noise = any(re.search(p, line) for p in NOISE_PATTERNS)
        is_signal = any(
            re.search(pat, line)
            for patterns in DELIVERABLE_PATTERNS.values()
            for pat in patterns
        )
        if is_noise and not is_signal:
            noise_lines += 1

    return noise_lines / len(lines)


def extract_deliverables(text: str, turn_result: TurnResult) -> list[Deliverable]:
    """Extract concrete deliverables from a turn result."""
    deliverables: list[Deliverable] = []

    # From file changes reported by the agent backend
    for f in turn_result.files_changed:
        deliverables.append(Deliverable(
            dtype=DeliverableType.FILE_CHANGE,
            evidence=f,
            verified=True,  # came from the server, not the agent's text
            verification_detail="Reported by app-server",
        ))

    # From commands run
    for cmd in turn_result.commands_run:
        if "test" in cmd.lower() or "pytest" in cmd.lower() or "npm test" in cmd.lower():
            deliverables.append(Deliverable(
                dtype=DeliverableType.TEST_RESULT,
                evidence=cmd,
            ))

    # From text patterns
    for dtype, patterns in DELIVERABLE_PATTERNS.items():
        for pat in patterns:
            matches = re.findall(pat, text, re.MULTILINE)
            if matches:
                for match in matches[:5]:  # cap at 5 per type
                    evidence = match if isinstance(match, str) else match[0]
                    deliverables.append(Deliverable(dtype=dtype, evidence=evidence))
                break  # one pattern match per type is enough

    return deliverables


# ── Supervisor ─────────────────────────────────────────────────────────

@dataclass
class SupervisorConfig:
    """Configuration for how strictly to supervise agents."""
    require_deliverables: bool = True
    max_noise_ratio: float = 0.5  # reject if >50% noise
    max_retries: int = 2
    verify_commits: bool = True
    verify_tests: bool = True
    strict_roles: dict[str, bool] = field(default_factory=lambda: {
        "implementer": True,  # must produce file changes or commits
        "reviewer": True,     # must produce APPROVED/CHANGES_REQUESTED
        "qa": True,           # must produce test results
        "architect": False,   # analysis is acceptable
        "planner": False,     # plans are acceptable
    })


class Supervisor:
    """
    Supervises agent turns — ensures they produce real work, not noise.

    Inspired by pi-ult's supervise skill:
    - Never trust a message, always verify externally
    - Only respond to deliverables
    - Call out acknowledgement-without-execution directly
    """

    def __init__(self, config: SupervisorConfig | None = None, bus: EventBus | None = None):
        self.config = config or SupervisorConfig()
        self._bus = bus

    async def supervise_turn(
        self,
        agent: Agent,
        thread_id: str,
        message: str,
        cwd: str | None = None,
    ) -> SupervisionResult:
        """Send a message and supervise the response."""
        result = await agent.send_message(thread_id, message)
        return await self.evaluate(agent, result)

    async def evaluate(self, agent: Agent, result: TurnResult) -> SupervisionResult:
        """Evaluate a turn result for deliverables and noise."""
        text = result.full_text
        deliverables = extract_deliverables(text, result)
        noise_ratio = detect_noise_ratio(text)
        issues: list[str] = []

        role_name = agent.role.name
        is_strict = self.config.strict_roles.get(role_name, False)

        # Check noise ratio
        if noise_ratio > self.config.max_noise_ratio:
            issues.append(
                f"Response is {noise_ratio:.0%} noise. "
                "Agent acknowledged without producing concrete output."
            )

        # Check for deliverables based on role
        if is_strict and self.config.require_deliverables:
            if role_name == "implementer" and not any(
                d.dtype in (DeliverableType.FILE_CHANGE, DeliverableType.COMMIT, DeliverableType.CODE_DIFF)
                for d in deliverables
            ):
                issues.append("Implementer produced no file changes or commits.")

            elif role_name == "reviewer" and not any(
                d.dtype == DeliverableType.REVIEW_DECISION for d in deliverables
            ):
                issues.append(
                    "Reviewer did not produce a clear APPROVED or CHANGES_REQUESTED decision."
                )

            elif role_name == "qa" and not any(
                d.dtype == DeliverableType.TEST_RESULT for d in deliverables
            ):
                issues.append("QA did not produce test results.")

        accepted = len(issues) == 0

        if self._bus and issues:
            await self._bus.publish("supervision.issues", {
                "agent_id": agent.agent_id,
                "role": role_name,
                "issues": issues,
                "noise_ratio": noise_ratio,
            }, source="supervisor")

        return SupervisionResult(
            accepted=accepted,
            deliverables=deliverables,
            raw_response=text,
            issues=issues,
            noise_ratio=noise_ratio,
        )

    def build_correction(self, result: SupervisionResult) -> str:
        """Build a direct correction message for a non-accepted turn."""
        parts = [
            "Your previous response was not accepted. Issues:",
        ]
        for issue in result.issues:
            parts.append(f"- {issue}")
        parts.append("")
        parts.append(
            "Do not acknowledge, confirm, or explain — produce the actual deliverable. "
            "Report back only with concrete output: commit hashes, test counts, "
            "file paths changed, or a clear APPROVED/CHANGES_REQUESTED decision."
        )
        return "\n".join(parts)

    async def supervised_send(
        self,
        agent: Agent,
        thread_id: str,
        message: str,
        max_retries: int | None = None,
    ) -> SupervisionResult:
        """
        Send a message with automatic retry on noise/non-deliverable responses.

        If the agent blabs, sends a correction and retries up to max_retries times.
        """
        retries = max_retries if max_retries is not None else self.config.max_retries

        result = await agent.send_message(thread_id, message)
        sr = await self.evaluate(agent, result)

        attempt = 0
        while not sr.accepted and attempt < retries:
            attempt += 1
            logger.warning(
                "Supervisor: agent %s failed supervision (attempt %d/%d): %s",
                agent.name, attempt, retries, sr.issues,
            )
            correction = self.build_correction(sr)
            result = await agent.send_message(thread_id, correction)
            sr = await self.evaluate(agent, result)

        if not sr.accepted:
            logger.error(
                "Supervisor: agent %s failed supervision after %d retries",
                agent.name, retries,
            )

        return sr
