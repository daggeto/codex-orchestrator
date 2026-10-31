"""Durable task ownership operations shared by HTTP, CLI and execution services."""
from __future__ import annotations

import json
from datetime import datetime, timezone


def now():
    return datetime.now(timezone.utc).isoformat()


class OwnershipStore:
    async def rows(self, sql, params=()):
        async with self._db.execute(sql, params) as cursor:
            result = []
            for row in await cursor.fetchall():
                value = dict(row)
                for key in list(value):
                    if key.endswith('_json'):
                        value[key[:-5]] = json.loads(value.pop(key)) if value[key] is not None else None
                result.append(value)
            return result

    async def one(self, sql, params=()):
        rows = await self.rows(sql, params)
        return rows[0] if rows else None

    async def write(self, sql, params=()):
        cursor = await self._db.execute(sql, params)
        await self._db.commit()
        return cursor.rowcount

    async def repositories(self):
        return await self.rows('SELECT * FROM repositories ORDER BY name')

    async def workspace_record(self, workspace_id):
        return await self.one('SELECT * FROM workspaces WHERE workspace_id=?', (workspace_id,))

    async def workspaces(self, repository_id):
        return await self.rows("SELECT * FROM workspaces WHERE repository_id=? ORDER BY kind,path", (repository_id,))

    async def task(self, task_id):
        return await self.one('SELECT * FROM tasks WHERE task_id=?', (task_id,))

    async def tasks(self, workspace_id, before=None, limit=50):
        return await self.rows('SELECT * FROM tasks WHERE workspace_id=? AND (? IS NULL OR task_id < ?) ORDER BY task_id DESC LIMIT ?',
                               (workspace_id, before, before, max(1, min(limit, 100))))

    async def task_status(self, task_id, status, phase):
        await self.write('UPDATE tasks SET status=?,phase=?,updated_at=?,completed_at=? WHERE task_id=?',
                         (status, phase, now(), now() if status in {'completed','failed','cancelled'} else None, task_id))

    async def append_events(self, events):
        await self._db.executemany('INSERT INTO task_events (task_id,agent_id,thread_id,turn_id,item_id,kind,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)', events)
        await self._db.commit()

    async def events(self, agent_id, after=0, limit=200):
        return await self.rows('SELECT * FROM task_events WHERE agent_id=? AND id>? ORDER BY id LIMIT ?',
                               (agent_id, after, max(1, min(limit, 500))))

    async def pending_inputs(self, task_id):
        return await self.rows("SELECT * FROM input_requests WHERE task_id=? AND status='pending' ORDER BY created_at", (task_id,))

    async def create_input(self, request_id, task_id, prompt, kind='text', agent_id=None, execution_id=None, options=None):
        await self.write('INSERT INTO input_requests (request_id,task_id,prompt,kind,agent_id,execution_id,options_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                         (request_id, task_id, prompt, kind, agent_id, execution_id, json.dumps(options or []), now(), now()))
        if task_id:
            await self.task_status(task_id, 'waiting_input', 'Waiting for your input')

    async def resolve_input(self, request_id, response):
        return await self.write("UPDATE input_requests SET status='resolved',response_json=?,updated_at=? WHERE request_id=? AND status='pending'",
                                (json.dumps(response), now(), request_id)) == 1
