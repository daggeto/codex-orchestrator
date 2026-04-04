"""
Audit trail — logs every agent interaction for review and quality analysis.

Records:
- Every message sent to an agent and the full response
- Token usage and cost per turn
- Timing data (latency, duration)
- Deliverables extracted by supervision
- Workflow step outcomes
- Approval decisions

Everything goes to SQLite so it survives restarts and can be queried later.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

AUDIT_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL,
    thread_id TEXT NOT NULL,
    message TEXT NOT NULL,
    response TEXT NOT NULL,
    response_length INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cached_tokens INTEGER,
    total_tokens INTEGER,
    model TEXT,
    files_changed_json TEXT NOT NULL DEFAULT '[]',
    commands_run_json TEXT NOT NULL DEFAULT '[]',
    error TEXT,
    supervision_accepted INTEGER,
    supervision_noise_ratio REAL,
    supervision_issues_json TEXT,
    deliverables_json TEXT,
    workflow_run_id TEXT,
    workflow_step_id TEXT,
    worktree_id TEXT,
    cwd TEXT
);

CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    topic TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    agent_id TEXT,
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS audit_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    agent_id TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cached_tokens INTEGER NOT NULL DEFAULT 0,
    estimated_cost_usd REAL,
    turn_id INTEGER,
    workflow_run_id TEXT
);

CREATE TABLE IF NOT EXISTS audit_sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    agents_json TEXT NOT NULL DEFAULT '[]',
    total_turns INTEGER NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    total_estimated_cost REAL NOT NULL DEFAULT 0.0,
    summary TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_turns_agent ON audit_turns(agent_id);
CREATE INDEX IF NOT EXISTS idx_audit_turns_thread ON audit_turns(thread_id);
CREATE INDEX IF NOT EXISTS idx_audit_turns_time ON audit_turns(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_turns_workflow ON audit_turns(workflow_run_id);
CREATE INDEX IF NOT EXISTS idx_audit_events_topic ON audit_events(topic);
CREATE INDEX IF NOT EXISTS idx_audit_costs_agent ON audit_costs(agent_id);
"""

# Rough cost estimates per 1M tokens (as of mid-2026, adjust as needed)
MODEL_COSTS = {
    "gpt-5.4":       {"input": 2.50, "output": 10.00, "cached": 1.25},
    "gpt-5.1-codex": {"input": 1.50, "output": 6.00,  "cached": 0.75},
    "gpt-4.1":       {"input": 2.00, "output": 8.00,  "cached": 0.50},
    "o3":            {"input": 2.00, "output": 8.00,  "cached": 1.00},
    "o4-mini":       {"input": 0.50, "output": 2.00,  "cached": 0.25},
}

DEFAULT_COST = {"input": 2.00, "output": 8.00, "cached": 1.00}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def estimate_cost(model: str | None, input_tokens: int, output_tokens: int, cached_tokens: int = 0) -> float:
    rates = MODEL_COSTS.get(model or "", DEFAULT_COST)
    cost = (
        (input_tokens - cached_tokens) * rates["input"] / 1_000_000
        + cached_tokens * rates["cached"] / 1_000_000
        + output_tokens * rates["output"] / 1_000_000
    )
    return round(cost, 6)


@dataclass
class TurnRecord:
    agent_id: str
    role: str
    thread_id: str
    message: str
    response: str
    duration_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    total_tokens: int | None = None
    model: str | None = None
    files_changed: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    error: str | None = None
    supervision_accepted: bool | None = None
    supervision_noise_ratio: float | None = None
    supervision_issues: list[str] | None = None
    deliverables: list[dict] | None = None
    workflow_run_id: str | None = None
    workflow_step_id: str | None = None
    worktree_id: str | None = None
    cwd: str | None = None


class AuditLog:
    """Persistent audit trail for all agent interactions."""

    def __init__(self, db: aiosqlite.Connection):
        self._db = db
        self._session_id: str | None = None
        self._session_totals = {
            "turns": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
        }

    async def init(self):
        await self._db.executescript(AUDIT_SCHEMA)
        await self._db.commit()

    # ── Sessions ────────────────────────────────────────────────────────

    async def start_session(self, session_id: str, agents: list[dict[str, str]]):
        self._session_id = session_id
        self._session_totals = {"turns": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0}
        await self._db.execute(
            "INSERT OR REPLACE INTO audit_sessions (session_id, started_at, agents_json) VALUES (?, ?, ?)",
            (session_id, _now(), json.dumps(agents)),
        )
        await self._db.commit()

    async def end_session(self, summary: str = ""):
        if not self._session_id:
            return
        await self._db.execute(
            """UPDATE audit_sessions SET ended_at=?, total_turns=?, total_input_tokens=?,
               total_output_tokens=?, total_estimated_cost=?, summary=? WHERE session_id=?""",
            (_now(), self._session_totals["turns"], self._session_totals["input_tokens"],
             self._session_totals["output_tokens"], self._session_totals["cost"],
             summary, self._session_id),
        )
        await self._db.commit()

    # ── Turns ───────────────────────────────────────────────────────────

    async def log_turn(self, rec: TurnRecord) -> int:
        cost = estimate_cost(rec.model, rec.input_tokens or 0, rec.output_tokens or 0, rec.cached_tokens or 0)

        cursor = await self._db.execute(
            """INSERT INTO audit_turns
               (timestamp, agent_id, role, thread_id, message, response, response_length,
                duration_ms, input_tokens, output_tokens, cached_tokens, total_tokens, model,
                files_changed_json, commands_run_json, error,
                supervision_accepted, supervision_noise_ratio, supervision_issues_json,
                deliverables_json, workflow_run_id, workflow_step_id, worktree_id, cwd)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now(), rec.agent_id, rec.role, rec.thread_id,
                rec.message, rec.response, len(rec.response),
                rec.duration_ms, rec.input_tokens, rec.output_tokens,
                rec.cached_tokens, rec.total_tokens, rec.model,
                json.dumps(rec.files_changed), json.dumps(rec.commands_run),
                rec.error,
                1 if rec.supervision_accepted else (0 if rec.supervision_accepted is not None else None),
                rec.supervision_noise_ratio,
                json.dumps(rec.supervision_issues) if rec.supervision_issues else None,
                json.dumps(rec.deliverables) if rec.deliverables else None,
                rec.workflow_run_id, rec.workflow_step_id, rec.worktree_id, rec.cwd,
            ),
        )
        await self._db.commit()
        turn_id = cursor.lastrowid

        # Track costs
        if rec.input_tokens or rec.output_tokens:
            await self._db.execute(
                """INSERT INTO audit_costs
                   (timestamp, agent_id, role, model, input_tokens, output_tokens,
                    cached_tokens, estimated_cost_usd, turn_id, workflow_run_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (_now(), rec.agent_id, rec.role, rec.model,
                 rec.input_tokens or 0, rec.output_tokens or 0, rec.cached_tokens or 0,
                 cost, turn_id, rec.workflow_run_id),
            )
            await self._db.commit()

        # Update session totals
        self._session_totals["turns"] += 1
        self._session_totals["input_tokens"] += rec.input_tokens or 0
        self._session_totals["output_tokens"] += rec.output_tokens or 0
        self._session_totals["cost"] += cost

        return turn_id

    # ── Events ──────────────────────────────────────────────────────────

    async def log_event(self, topic: str, payload: dict[str, Any], source: str = "", agent_id: str | None = None):
        await self._db.execute(
            "INSERT INTO audit_events (timestamp, topic, source, agent_id, payload_json) VALUES (?, ?, ?, ?, ?)",
            (_now(), topic, source, agent_id, json.dumps(payload, default=str)),
        )
        await self._db.commit()

    # ── Queries ─────────────────────────────────────────────────────────

    async def get_turns(
        self,
        agent_id: str | None = None,
        role: str | None = None,
        thread_id: str | None = None,
        workflow_run_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if agent_id:
            clauses.append("agent_id = ?"); params.append(agent_id)
        if role:
            clauses.append("role = ?"); params.append(role)
        if thread_id:
            clauses.append("thread_id = ?"); params.append(thread_id)
        if workflow_run_id:
            clauses.append("workflow_run_id = ?"); params.append(workflow_run_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)

        async with self._db.execute(
            f"SELECT * FROM audit_turns WHERE {where} ORDER BY id DESC LIMIT ?", params
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    async def get_session_summary(self, session_id: str | None = None) -> dict[str, Any] | None:
        sid = session_id or self._session_id
        if not sid:
            return None
        async with self._db.execute("SELECT * FROM audit_sessions WHERE session_id = ?", (sid,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    async def get_cost_summary(
        self,
        agent_id: str | None = None,
        role: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        clauses, params = [], []
        if agent_id:
            clauses.append("agent_id = ?"); params.append(agent_id)
        if role:
            clauses.append("role = ?"); params.append(role)
        if since:
            clauses.append("timestamp >= ?"); params.append(since)
        where = " AND ".join(clauses) if clauses else "1=1"

        async with self._db.execute(
            f"""SELECT
                count(*) as turns,
                sum(input_tokens) as total_input,
                sum(output_tokens) as total_output,
                sum(cached_tokens) as total_cached,
                sum(estimated_cost_usd) as total_cost,
                avg(estimated_cost_usd) as avg_cost_per_turn,
                max(estimated_cost_usd) as max_cost_turn
            FROM audit_costs WHERE {where}""",
            params,
        ) as cur:
            row = await cur.fetchone()
            cols = [d[0] for d in cur.description]
            return dict(zip(cols, row))

    async def get_quality_report(self, role: str | None = None, limit: int = 100) -> dict[str, Any]:
        """Generate a quality report: noise ratios, acceptance rates, deliverable counts."""
        clauses, params = ["supervision_accepted IS NOT NULL"], []
        if role:
            clauses.append("role = ?"); params.append(role)
        where = " AND ".join(clauses)
        params.append(limit)

        async with self._db.execute(
            f"""SELECT
                count(*) as total_supervised,
                sum(supervision_accepted) as accepted,
                avg(supervision_noise_ratio) as avg_noise_ratio,
                avg(response_length) as avg_response_length,
                avg(duration_ms) as avg_duration_ms
            FROM audit_turns WHERE {where} ORDER BY id DESC LIMIT ?""",
            params,
        ) as cur:
            row = await cur.fetchone()
            cols = [d[0] for d in cur.description]
            summary = dict(zip(cols, row))

        total = summary.get("total_supervised") or 0
        accepted = summary.get("accepted") or 0
        summary["acceptance_rate"] = accepted / total if total else 0
        summary["rejection_rate"] = 1 - summary["acceptance_rate"] if total else 0
        return summary

    async def get_recent_timeline(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get a timeline of recent turns with key metrics."""
        async with self._db.execute(
            """SELECT id, timestamp, role, thread_id,
                      substr(message, 1, 100) as message_preview,
                      response_length, duration_ms,
                      input_tokens, output_tokens, model,
                      supervision_accepted, supervision_noise_ratio, error,
                      files_changed_json, cwd
               FROM audit_turns ORDER BY id DESC LIMIT ?""",
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]
