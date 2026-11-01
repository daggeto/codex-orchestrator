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
                        raw = value.pop(key)
                        value[key[:-5]] = json.loads(raw) if raw is not None else None
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

    async def tasks(self, workspace_id, before=None, limit=50, archived='all'):
        return await self.query_tasks(workspace_id=workspace_id, before=before, limit=limit, archived=archived)

    async def query_tasks(self, workspace_id=None, repository_id=None, status=None,
                          archived='exclude', before=None, limit=50):
        if archived not in {'exclude', 'only', 'all'}:
            raise ValueError('archived must be exclude, only, or all')
        clauses, params = [], []
        for column, value in [('t.workspace_id', workspace_id), ('w.repository_id', repository_id)]:
            if value:
                clauses.append(column + '=?')
                params.append(value)
        if status == 'open':
            clauses.append("t.status NOT IN ('completed','failed','cancelled')")
        elif status:
            clauses.append('t.status=?')
            params.append(status)
        if archived != 'all':
            clauses.append('t.archived_at IS ' + ('NOT NULL' if archived == 'only' else 'NULL'))
        if before:
            clauses.append('t.task_id<?')
            params.append(before)
        sql = 'SELECT t.* FROM tasks t JOIN workspaces w USING(workspace_id)'
        if clauses:
            sql += ' WHERE ' + ' AND '.join(clauses)
        return await self.rows(sql + ' ORDER BY t.task_id DESC LIMIT ?', (*params, max(1, min(limit, 101))))

    async def task_page(self, limit=50, **filters):
        limit = max(1, min(limit, 100))
        rows = await self.query_tasks(limit=limit + 1, **filters)
        return {'tasks': rows[:limit], 'next_cursor': rows[limit - 1]['task_id'] if len(rows) > limit else None}

    async def set_task_archived(self, task_id, archived):
        return await self.write(
            "UPDATE tasks SET archived_at=CASE WHEN ? THEN COALESCE(archived_at,?) ELSE NULL END "
            "WHERE task_id=? AND (?=0 OR status IN ('completed','failed','cancelled'))",
            (archived, now(), task_id, archived))

    async def participate(self, task_id, agent_id, thread_id=None):
        if not task_id:
            return
        await self.write('INSERT OR IGNORE INTO task_agents VALUES (?,?)', (task_id, agent_id))
        if thread_id:
            await self.write('INSERT OR IGNORE INTO task_threads VALUES (?,?)', (task_id, thread_id))

    async def task_agents(self, task_id):
        return await self.rows('SELECT DISTINCT a.* FROM agents a LEFT JOIN task_agents p USING(agent_id) WHERE a.task_id=? OR p.task_id=? ORDER BY a.created_at', (task_id, task_id))

    async def record_turn(self, task_id, result):
        outcome = {'ok': result.ok, 'error': result.error, 'full_text': result.full_text,
                   'files_changed': result.files_changed}
        await self.write('UPDATE tasks SET last_turn_json=?,updated_at=? WHERE task_id=?',
                         (json.dumps(outcome), now(), task_id))

    async def task_status(self, task_id, status, phase, expected=None):
        sql = 'UPDATE tasks SET status=?,phase=?,updated_at=?,completed_at=? WHERE task_id=?'
        params = (status, phase, now(), now() if status in {'completed','failed','cancelled'} else None, task_id)
        if expected:
            sql += ' AND status IN (' + ','.join('?' for _ in expected) + ')'
            params += tuple(expected)
        if not await self.write(sql, params):
            return False
        await self.append_events([(task_id, None, None, None, None, 'task/status', json.dumps({'status': status, 'phase': phase}), now())])
        return True

    async def append_events(self, events):
        await self._db.executemany('INSERT INTO task_events (task_id,agent_id,thread_id,turn_id,item_id,kind,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?)', events)
        await self._db.commit()

    async def events(self, agent_id, after=0, limit=200, task_id=None):
        return await self.rows('SELECT * FROM task_events WHERE agent_id=? AND id>? AND (? IS NULL OR task_id=?) ORDER BY id LIMIT ?',
                               (agent_id, after, task_id, task_id, max(1, min(limit, 500))))

    async def pending_inputs(self, task_id):
        return await self.rows("SELECT * FROM input_requests WHERE task_id=? AND status='pending' ORDER BY created_at", (task_id,))

    async def create_input(self, request_id, task_id, prompt, kind='text', agent_id=None, execution_id=None, options=None):
        await self.write('INSERT INTO input_requests (request_id,task_id,prompt,kind,agent_id,execution_id,options_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                         (request_id, task_id, prompt, kind, agent_id, execution_id, json.dumps(options or []), now(), now()))
        if task_id:
            await self.task_status(task_id, 'waiting_input', 'Waiting for your input',
                                   expected=('queued', 'active', 'waiting_input'))

    async def resolve_input(self, request_id, response):
        return await self.write("UPDATE input_requests SET status='resolved',response_json=?,updated_at=? WHERE request_id=? AND status='pending'",
                                (json.dumps(response), now(), request_id)) == 1

    async def merge_operation(self, operation_id):
        return await self.one('SELECT * FROM workspace_merges WHERE operation_id=?', (operation_id,))

    async def reserve_merge(self, operation):
        """A dedicated connection isolates the reservation from shared writes."""
        import aiosqlite
        from orchestrator.tasks import Conflict
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute('PRAGMA foreign_keys=ON')
            await db.execute('BEGIN IMMEDIATE')
            try:
                previous = await (await db.execute('SELECT * FROM workspace_merges WHERE operation_id=?',
                                                  (operation['operation_id'],))).fetchone()
                if previous:
                    if previous['fingerprint'] != operation['fingerprint']:
                        raise Conflict('Operation ID belongs to a different merge request')
                    return False
                for wid in (operation['source_workspace_id'], operation['destination_workspace_id']):
                    row = await (await db.execute('SELECT status FROM workspaces WHERE workspace_id=?', (wid,))).fetchone()
                    if not row or row['status'] != 'active':
                        raise Conflict('Workspace is reserved for merging, recovery, or archiving')
                    lease = await (await db.execute('SELECT 1 FROM workspace_leases WHERE workspace_id=?', (wid,))).fetchone()
                    if lease:
                        raise Conflict('Workspace is executing a task')
                columns = list(operation)
                await db.execute('INSERT INTO workspace_merges (' + ','.join(columns) + ') VALUES (' +
                                 ','.join('?' for _ in columns) + ')', tuple(operation.values()))
                await db.execute("UPDATE workspaces SET status='merging',updated_at=? WHERE workspace_id IN (?,?)",
                                 (now(), operation['source_workspace_id'], operation['destination_workspace_id']))
                await db.commit()
                return True
            except aiosqlite.IntegrityError as exc:
                await db.rollback()
                raise Conflict('Repository already has an unresolved merge operation') from exc
            except BaseException:
                await db.rollback()
                raise

    async def finish_merge(self, operation_id, state, resulting_sha=None, error_code=None,
                           error_message=None, diagnostics='', conflict_paths=None):
        import aiosqlite
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute('BEGIN IMMEDIATE')
            try:
                current = await (await db.execute('SELECT state FROM workspace_merges WHERE operation_id=?', (operation_id,))).fetchone()
                if not current or current[0] not in {'accepted', 'running', 'recovery_required'}:
                    return
                await db.execute("""UPDATE workspace_merges SET state=?,resulting_sha=?,error_code=?,
                    error_message=?,diagnostics=?,conflict_paths_json=?,updated_at=?,completed_at=?
                    WHERE operation_id=?""", (state, resulting_sha, error_code, error_message,
                    diagnostics[-16000:], json.dumps((conflict_paths or [])[:200]), now(),
                    None if state == 'recovery_required' else now(), operation_id))
                await db.execute("""UPDATE workspaces SET status=?,updated_at=? WHERE workspace_id IN (
                    SELECT source_workspace_id FROM workspace_merges WHERE operation_id=? UNION
                    SELECT destination_workspace_id FROM workspace_merges WHERE operation_id=?)
                    AND status IN ('merging','merge_recovery_required')""",
                    ('merge_recovery_required' if state == 'recovery_required' else 'active', now(), operation_id, operation_id))
                await db.commit()
            except BaseException:
                await db.rollback()
                raise
