"""Ordered, additive migrations. Historical records without evidence stay unowned."""
from __future__ import annotations

VERSION = 7
MIGRATIONS = {2: """
CREATE TABLE repositories (
 repository_id TEXT PRIMARY KEY, name TEXT NOT NULL, main_path TEXT NOT NULL UNIQUE,
 common_dir TEXT NOT NULL UNIQUE, available INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE workspaces (
 workspace_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL REFERENCES repositories(repository_id),
 kind TEXT NOT NULL CHECK(kind IN ('main','worktree')), path TEXT NOT NULL UNIQUE,
 branch TEXT, base_commit TEXT, status TEXT NOT NULL DEFAULT 'active',
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX repository_main ON workspaces(repository_id) WHERE kind='main';
CREATE TABLE tasks (
 task_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
 title TEXT NOT NULL, prompt TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('agent','workflow')),
 status TEXT NOT NULL DEFAULT 'queued', phase TEXT NOT NULL DEFAULT 'Queued',
 settings_json TEXT NOT NULL, idempotency_key TEXT UNIQUE, fingerprint TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
ALTER TABLE agents ADD COLUMN task_id TEXT REFERENCES tasks(task_id);
ALTER TABLE agents ADD COLUMN parent_agent_id TEXT REFERENCES agents(agent_id);
ALTER TABLE agents ADD COLUMN retired_at TEXT;
ALTER TABLE threads ADD COLUMN task_id TEXT REFERENCES tasks(task_id);
ALTER TABLE threads ADD COLUMN step_execution_id TEXT;
ALTER TABLE workflow_runs ADD COLUMN task_id TEXT REFERENCES tasks(task_id);
ALTER TABLE work_items ADD COLUMN task_id TEXT REFERENCES tasks(task_id);
CREATE TABLE step_executions (
 execution_id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(task_id),
 run_id TEXT NOT NULL REFERENCES workflow_runs(run_id), step_id TEXT NOT NULL,
 attempt INTEGER NOT NULL, parent_id TEXT REFERENCES step_executions(execution_id),
 dependencies_json TEXT NOT NULL DEFAULT '[]', agent_id TEXT REFERENCES agents(agent_id),
 thread_id TEXT REFERENCES threads(thread_id), status TEXT NOT NULL,
 output_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE task_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT REFERENCES tasks(task_id),
 agent_id TEXT REFERENCES agents(agent_id), thread_id TEXT, turn_id TEXT, item_id TEXT,
 kind TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE input_requests (
 request_id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(task_id), agent_id TEXT REFERENCES agents(agent_id),
 execution_id TEXT REFERENCES step_executions(execution_id), kind TEXT NOT NULL,
 prompt TEXT NOT NULL, options_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'pending',
 response_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE archives (
 archive_id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
 path TEXT, checksum TEXT, stage TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE workspace_leases (
 workspace_id TEXT PRIMARY KEY REFERENCES workspaces(workspace_id), task_id TEXT NOT NULL REFERENCES tasks(task_id)
);
CREATE INDEX tasks_workspace ON tasks(workspace_id, created_at);
CREATE INDEX agents_task ON agents(task_id);
CREATE INDEX events_task_cursor ON task_events(task_id,id);
CREATE INDEX events_agent_cursor ON task_events(agent_id,id);
CREATE INDEX steps_run ON step_executions(run_id,created_at);
CREATE INDEX input_task ON input_requests(task_id,status);
""", 3: """
ALTER TABLE workspace_leases ADD COLUMN owner_pid INTEGER;
ALTER TABLE workspace_leases ADD COLUMN owner_started TEXT;
ALTER TABLE tasks ADD COLUMN owner_pid INTEGER;
ALTER TABLE tasks ADD COLUMN owner_started TEXT;
ALTER TABLE agents ADD COLUMN owner_pid INTEGER;
ALTER TABLE agents ADD COLUMN owner_started TEXT;
ALTER TABLE archives ADD COLUMN owner_pid INTEGER;
ALTER TABLE archives ADD COLUMN owner_started TEXT;
""", 4: """
ALTER TABLE tasks ADD COLUMN last_turn_json TEXT;
CREATE TABLE task_agents (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), agent_id TEXT NOT NULL REFERENCES agents(agent_id),
 PRIMARY KEY (task_id,agent_id)
);
CREATE TABLE task_threads (
 task_id TEXT NOT NULL REFERENCES tasks(task_id), thread_id TEXT NOT NULL REFERENCES threads(thread_id),
 PRIMARY KEY (task_id,thread_id)
);
INSERT OR IGNORE INTO task_agents SELECT task_id,agent_id FROM agents WHERE task_id IS NOT NULL;
INSERT OR IGNORE INTO task_agents SELECT task_id,agent_id FROM threads WHERE task_id IS NOT NULL;
INSERT OR IGNORE INTO task_agents SELECT e.task_id,e.agent_id FROM task_events e JOIN agents a USING(agent_id) WHERE e.task_id IS NOT NULL;
INSERT OR IGNORE INTO task_agents SELECT t.task_id,a.agent_id FROM tasks t JOIN agents a ON a.agent_id=json_extract(t.settings_json,'$.legacy_agent_id');
INSERT OR IGNORE INTO task_threads SELECT task_id,thread_id FROM threads WHERE task_id IS NOT NULL;
INSERT OR IGNORE INTO task_threads SELECT e.task_id,e.thread_id FROM task_events e JOIN threads t USING(thread_id) WHERE e.task_id IS NOT NULL;
""", 5: """
CREATE TABLE workspace_merges (
 operation_id TEXT PRIMARY KEY, repository_id TEXT NOT NULL REFERENCES repositories(repository_id),
 source_workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
 destination_workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
 source_branch TEXT NOT NULL, source_sha TEXT NOT NULL, destination_path TEXT NOT NULL,
 destination_sha TEXT NOT NULL, fingerprint TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('accepted','running','merged','already_integrated','failed','recovery_required')),
 resulting_sha TEXT, error_code TEXT, error_message TEXT, diagnostics TEXT,
 conflict_paths_json TEXT NOT NULL DEFAULT '[]',
 owner_pid INTEGER NOT NULL, owner_started TEXT NOT NULL, git_pid INTEGER, git_started TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE INDEX workspace_merge_latest ON workspace_merges(source_workspace_id,created_at DESC);
CREATE INDEX workspace_merge_destination ON workspace_merges(destination_workspace_id,created_at DESC);
CREATE UNIQUE INDEX repository_merge_pending ON workspace_merges(repository_id)
 WHERE state IN ('accepted','running','recovery_required');
""", 6: """
ALTER TABLE tasks ADD COLUMN archived_at TEXT DEFAULT NULL;
CREATE INDEX tasks_visible_workspace ON tasks(workspace_id,task_id DESC) WHERE archived_at IS NULL;
CREATE INDEX tasks_archived_cursor ON tasks(task_id DESC) WHERE archived_at IS NOT NULL;
""", 7: """
ALTER TABLE workspace_merges ADD COLUMN commit_changes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workspace_merges ADD COLUMN changes_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE workspace_merges ADD COLUMN commits_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE workspace_merges ADD COLUMN phase TEXT NOT NULL DEFAULT 'merge';
"""}


async def migrate(db):
    row = await (await db.execute("SELECT value FROM meta WHERE key='schema_version'")).fetchone()
    version = int(row[0]) if row else 1
    if version > VERSION:
        raise RuntimeError(f"Database schema {version} is newer than supported {VERSION}")
    for target in range(version + 1, VERSION + 1):
        await db.execute('BEGIN IMMEDIATE')
        try:
            for statement in MIGRATIONS[target].split(';'):
                if statement.strip():
                    await db.execute(statement)
            await db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(target),))
            await db.commit()
        except BaseException:
            await db.rollback()
            raise
