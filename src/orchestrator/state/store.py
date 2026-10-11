"""SQLite-backed persistent state for agents, threads, and work items."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    agent_id TEXT PRIMARY KEY,
    role TEXT NOT NULL,
    backend TEXT NOT NULL,          -- 'codex' or 'claude'
    port INTEGER,
    pid INTEGER,
    ws_url TEXT,
    status TEXT NOT NULL DEFAULT 'stopped',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS threads (
    thread_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    cwd TEXT,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (agent_id) REFERENCES agents(agent_id)
);

CREATE TABLE IF NOT EXISTS work_items (
    item_id TEXT PRIMARY KEY,
    workflow_id TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    priority INTEGER NOT NULL DEFAULT 0,
    assigned_agent TEXT,
    assigned_thread TEXT,
    parent_item TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    output_json TEXT NOT NULL DEFAULT '{}',
    history_json TEXT NOT NULL DEFAULT '[]',
    tags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (assigned_agent) REFERENCES agents(agent_id),
    FOREIGN KEY (assigned_thread) REFERENCES threads(thread_id)
);

CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    workflow_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    config_json TEXT NOT NULL DEFAULT '{}',
    state_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    topic TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_threads_agent ON threads(agent_id);
CREATE INDEX IF NOT EXISTS idx_work_items_status ON work_items(status);
CREATE INDEX IF NOT EXISTS idx_work_items_agent ON work_items(assigned_agent);
CREATE INDEX IF NOT EXISTS idx_work_items_workflow ON work_items(workflow_id);
CREATE INDEX IF NOT EXISTS idx_event_log_topic ON event_log(topic);
CREATE INDEX IF NOT EXISTS idx_event_log_time ON event_log(timestamp);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkItemStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    CHANGES_REQUESTED = "changes_requested"
    APPROVED = "approved"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class AgentRecord:
    agent_id: str
    role: str
    backend: str
    port: int | None = None
    pid: int | None = None
    ws_url: str | None = None
    status: str = "stopped"
    config: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class ThreadRecord:
    thread_id: str
    agent_id: str
    cwd: str | None = None
    model: str | None = None
    status: str = "active"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)


@dataclass
class WorkItemRecord:
    item_id: str
    title: str
    description: str = ""
    workflow_id: str | None = None
    status: str = "pending"
    priority: int = 0
    assigned_agent: str | None = None
    assigned_thread: str | None = None
    parent_item: str | None = None
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: dict[str, Any] = field(default_factory=dict)
    history: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    completed_at: str | None = None


@dataclass
class WorkflowRunRecord:
    run_id: str
    workflow_name: str
    status: str = "pending"
    config: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    completed_at: str | None = None


class StateStore:
    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        self._db = await aiosqlite.connect(self._db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            ("schema_version", str(SCHEMA_VERSION)),
        )
        await self._db.commit()

    async def close(self):
        if self._db:
            await self._db.close()
            self._db = None

    # ── Agents ──────────────────────────────────────────────────────────

    async def save_agent(self, rec: AgentRecord):
        await self._db.execute(
            """INSERT OR REPLACE INTO agents
               (agent_id, role, backend, port, pid, ws_url, status, config_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rec.agent_id, rec.role, rec.backend, rec.port, rec.pid,
                rec.ws_url, rec.status, json.dumps(rec.config),
                rec.created_at, _now(),
            ),
        )
        await self._db.commit()

    async def get_agent(self, agent_id: str) -> AgentRecord | None:
        async with self._db.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return AgentRecord(
                agent_id=row["agent_id"], role=row["role"], backend=row["backend"],
                port=row["port"], pid=row["pid"], ws_url=row["ws_url"],
                status=row["status"], config=json.loads(row["config_json"]),
                created_at=row["created_at"], updated_at=row["updated_at"],
            )

    async def list_agents(self, status: str | None = None) -> list[AgentRecord]:
        if status:
            sql = "SELECT * FROM agents WHERE status = ? ORDER BY created_at"
            params = (status,)
        else:
            sql = "SELECT * FROM agents ORDER BY created_at"
            params = ()
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            AgentRecord(
                agent_id=r["agent_id"], role=r["role"], backend=r["backend"],
                port=r["port"], pid=r["pid"], ws_url=r["ws_url"],
                status=r["status"], config=json.loads(r["config_json"]),
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    async def update_agent_status(self, agent_id: str, status: str, **kwargs):
        sets = ["status = ?", "updated_at = ?"]
        vals: list[Any] = [status, _now()]
        for k, v in kwargs.items():
            if k in ("port", "pid", "ws_url"):
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.append(agent_id)
        await self._db.execute(
            f"UPDATE agents SET {', '.join(sets)} WHERE agent_id = ?", vals
        )
        await self._db.commit()

    async def delete_agent(self, agent_id: str):
        await self._db.execute("DELETE FROM threads WHERE agent_id = ?", (agent_id,))
        await self._db.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        await self._db.commit()

    # ── Threads ─────────────────────────────────────────────────────────

    async def save_thread(self, rec: ThreadRecord):
        await self._db.execute(
            """INSERT OR REPLACE INTO threads
               (thread_id, agent_id, cwd, model, status, metadata_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rec.thread_id, rec.agent_id, rec.cwd, rec.model,
                rec.status, json.dumps(rec.metadata), rec.created_at, _now(),
            ),
        )
        await self._db.commit()

    async def get_thread(self, thread_id: str) -> ThreadRecord | None:
        async with self._db.execute(
            "SELECT * FROM threads WHERE thread_id = ?", (thread_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return ThreadRecord(
                thread_id=row["thread_id"], agent_id=row["agent_id"],
                cwd=row["cwd"], model=row["model"], status=row["status"],
                metadata=json.loads(row["metadata_json"]),
                created_at=row["created_at"], updated_at=row["updated_at"],
            )

    async def list_threads(self, agent_id: str | None = None) -> list[ThreadRecord]:
        if agent_id:
            sql = "SELECT * FROM threads WHERE agent_id = ? ORDER BY created_at"
            params = (agent_id,)
        else:
            sql = "SELECT * FROM threads ORDER BY created_at"
            params = ()
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            ThreadRecord(
                thread_id=r["thread_id"], agent_id=r["agent_id"],
                cwd=r["cwd"], model=r["model"], status=r["status"],
                metadata=json.loads(r["metadata_json"]),
                created_at=r["created_at"], updated_at=r["updated_at"],
            )
            for r in rows
        ]

    # ── Work Items ──────────────────────────────────────────────────────

    async def save_work_item(self, rec: WorkItemRecord):
        await self._db.execute(
            """INSERT OR REPLACE INTO work_items
               (item_id, workflow_id, title, description, status, priority,
                assigned_agent, assigned_thread, parent_item,
                input_json, output_json, history_json, tags_json,
                created_at, updated_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rec.item_id, rec.workflow_id, rec.title, rec.description,
                rec.status, rec.priority, rec.assigned_agent, rec.assigned_thread,
                rec.parent_item, json.dumps(rec.input_data), json.dumps(rec.output_data),
                json.dumps(rec.history), json.dumps(rec.tags),
                rec.created_at, _now(), rec.completed_at,
            ),
        )
        await self._db.commit()

    async def get_work_item(self, item_id: str) -> WorkItemRecord | None:
        async with self._db.execute(
            "SELECT * FROM work_items WHERE item_id = ?", (item_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return self._row_to_work_item(row)

    async def list_work_items(
        self, status: str | None = None, agent_id: str | None = None,
        workflow_id: str | None = None,
    ) -> list[WorkItemRecord]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if agent_id:
            clauses.append("assigned_agent = ?")
            params.append(agent_id)
        if workflow_id:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        where = " AND ".join(clauses) if clauses else "1=1"
        sql = f"SELECT * FROM work_items WHERE {where} ORDER BY priority DESC, created_at"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [self._row_to_work_item(r) for r in rows]

    async def update_work_item_status(
        self, item_id: str, status: str,
        output_data: dict[str, Any] | None = None,
        history_entry: dict[str, Any] | None = None,
    ):
        item = await self.get_work_item(item_id)
        if not item:
            return
        if output_data:
            item.output_data.update(output_data)
        if history_entry:
            history_entry.setdefault("timestamp", _now())
            item.history.append(history_entry)
        completed_at = _now() if status in ("completed", "failed", "cancelled") else item.completed_at
        await self._db.execute(
            """UPDATE work_items SET status=?, output_json=?, history_json=?,
               updated_at=?, completed_at=? WHERE item_id=?""",
            (status, json.dumps(item.output_data), json.dumps(item.history),
             _now(), completed_at, item_id),
        )
        await self._db.commit()

    def _row_to_work_item(self, row) -> WorkItemRecord:
        return WorkItemRecord(
            item_id=row["item_id"], workflow_id=row["workflow_id"],
            title=row["title"], description=row["description"],
            status=row["status"], priority=row["priority"],
            assigned_agent=row["assigned_agent"], assigned_thread=row["assigned_thread"],
            parent_item=row["parent_item"],
            input_data=json.loads(row["input_json"]),
            output_data=json.loads(row["output_json"]),
            history=json.loads(row["history_json"]),
            tags=json.loads(row["tags_json"]),
            created_at=row["created_at"], updated_at=row["updated_at"],
            completed_at=row["completed_at"],
        )

    # ── Workflow Runs ───────────────────────────────────────────────────

    async def save_workflow_run(self, rec: WorkflowRunRecord):
        await self._db.execute(
            """INSERT OR REPLACE INTO workflow_runs
               (run_id, workflow_name, status, config_json, state_json, created_at, updated_at, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                rec.run_id, rec.workflow_name, rec.status,
                json.dumps(rec.config), json.dumps(rec.state),
                rec.created_at, _now(), rec.completed_at,
            ),
        )
        await self._db.commit()

    async def get_workflow_run(self, run_id: str) -> WorkflowRunRecord | None:
        async with self._db.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return WorkflowRunRecord(
                run_id=row["run_id"], workflow_name=row["workflow_name"],
                status=row["status"], config=json.loads(row["config_json"]),
                state=json.loads(row["state_json"]),
                created_at=row["created_at"], updated_at=row["updated_at"],
                completed_at=row["completed_at"],
            )

    async def list_workflow_runs(self, recent_limit: int = 50) -> list[WorkflowRunRecord]:
        """All unfinished runs plus a bounded recent terminal history."""
        async with self._db.execute(
            """SELECT * FROM workflow_runs
               WHERE status NOT IN (?, ?, ?)
                  OR run_id IN (SELECT run_id FROM workflow_runs
                      WHERE status IN (?, ?, ?)
                      ORDER BY updated_at DESC, run_id DESC LIMIT ?)
               ORDER BY updated_at DESC, run_id DESC""",
            ("completed", "failed", "cancelled", "completed", "failed", "cancelled",
             max(0, min(recent_limit, 200))),
        ) as cur:
            rows = await cur.fetchall()
        return [WorkflowRunRecord(
            run_id=row["run_id"], workflow_name=row["workflow_name"],
            status=row["status"], config=json.loads(row["config_json"]),
            state=json.loads(row["state_json"]), created_at=row["created_at"],
            updated_at=row["updated_at"], completed_at=row["completed_at"],
        ) for row in rows]

    # ── Event Log ───────────────────────────────────────────────────────

    async def log_event(self, topic: str, payload: dict[str, Any], source: str = ""):
        await self._db.execute(
            "INSERT INTO event_log (timestamp, topic, source, payload_json) VALUES (?, ?, ?, ?)",
            (_now(), topic, source, json.dumps(payload)),
        )
        await self._db.commit()

    async def query_events(
        self, topic_pattern: str = "%", limit: int = 100, since: str | None = None
    ) -> list[dict[str, Any]]:
        clauses = ["topic LIKE ?"]
        params: list[Any] = [topic_pattern]
        if since:
            clauses.append("timestamp >= ?")
            params.append(since)
        where = " AND ".join(clauses)
        sql = f"SELECT * FROM event_log WHERE {where} ORDER BY id DESC LIMIT ?"
        params.append(limit)
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r["id"], "timestamp": r["timestamp"], "topic": r["topic"],
             "source": r["source"], "payload": json.loads(r["payload_json"])}
            for r in rows
        ]
