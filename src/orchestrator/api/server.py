"""HTTP/WebSocket API for the orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from aiohttp import web

from orchestrator.core import Orchestrator
from orchestrator.api import projects, submissions, tasks, workspaces, filesystem, attachments
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


@web.middleware
async def local_mutations(request, handler):
    if request.method not in {'GET', 'HEAD', 'OPTIONS'}:
        host = urlsplit('http://' + request.host).hostname
        if host not in {'localhost', '127.0.0.1', '::1'}:
            raise web.HTTPForbidden(text='Local Host required')
        origin = request.headers.get('Origin')
        allowed = set(getattr(getattr(request.app['orch'], 'config', None), 'browser_origins', []))
        allowed.add(f'{request.scheme}://{request.host}')
        if origin and origin not in allowed:
            raise web.HTTPForbidden(text='Untrusted browser Origin')
    return await handler(request)


def create_app(orch: Orchestrator) -> web.Application:
    app = web.Application(middlewares=[tasks.errors, local_mutations])
    app["orch"] = orch
    app["submitted_tasks"] = set()
    app["submitted_run_ids"] = set()
    app.on_startup.append(submissions.recover_submissions)
    app.on_shutdown.append(_stop_submitted_tasks)

    attachments.register(app)
    tasks.register(app)
    workspaces.register(app)
    app.router.add_get('/api/filesystem/directories', filesystem.directories)
    app.router.add_get("/health", health)
    app.router.add_get("/api/session", session_info)
    app.router.add_get("/api/projects", projects.list_projects)
    app.router.add_post("/api/projects", projects.register_project)
    app.router.add_get("/api/agents", list_agents)
    app.router.add_post("/api/agents", spawn_agent)
    app.router.add_post("/api/agents/{agent_id}/stop", stop_agent)
    app.router.add_delete("/api/agents/{agent_id}", destroy_agent)
    app.router.add_post("/api/agents/{agent_id}/message", send_message)
    app.router.add_get("/api/agents/{agent_id}/threads", list_threads)
    app.router.add_post("/api/agents/{agent_id}/threads", create_thread)
    app.router.add_get("/api/roles", list_roles)
    app.router.add_post("/api/team", spawn_team)
    app.router.add_get("/api/work", list_work_items)
    app.router.add_get("/api/work/{item_id}", get_work_item)
    app.router.add_post("/api/work", create_work_item)
    app.router.add_post("/api/work/{item_id}/assign", assign_work_item)
    app.router.add_get("/api/workflows", list_workflows)
    app.router.add_get("/api/workflows/runs", list_workflow_runs)
    app.router.add_get("/api/workflows/runs/{run_id}", submissions.get_run)
    app.router.add_post("/api/workflows/runs/{run_id}/continue", submissions.continue_run)
    app.router.add_post("/api/workflows/{name}/submit", submissions.submit)
    app.router.add_post("/api/workflows/{name}/run", run_workflow)
    app.router.add_get("/api/events", event_stream)
    app.router.add_post("/api/send/{role}", send_to_role)

    # Supervised interaction
    app.router.add_post("/api/supervised/{role}", supervised_send)

    # Skills
    app.router.add_get("/api/skills", list_skills)
    app.router.add_post("/api/skills/{name}/run", run_skill)

    # HITL
    app.router.add_get("/api/hitl/pending", hitl_pending)
    app.router.add_post("/api/hitl/approvals/{request_id}", resolve_approval)
    app.router.add_post("/api/hitl/escalations/{escalation_id}", resolve_escalation)

    # Audit
    app.router.add_get("/api/audit/turns", audit_turns)
    app.router.add_get("/api/audit/costs", audit_costs)
    app.router.add_get("/api/audit/quality", audit_quality)
    app.router.add_get("/api/audit/timeline", audit_timeline)

    # Workspaces
    app.router.add_get("/api/workspaces", list_workspaces)
    app.router.add_post("/api/workspaces", create_workspace)
    app.router.add_get("/api/workspaces/{wt_id}/diff", workspace_diff)
    app.router.add_get("/api/workspaces/{wt_id}/log", workspace_log)

    return app


async def _stop_submitted_tasks(app: web.Application):
    service = getattr(app['orch'], 'tasks', None)
    if service:
        await service.shutdown()
    # Acceptance can enqueue execution during shutdown. Drain newly owned tasks
    # too, before the orchestrator closes its store.
    while app["submitted_tasks"]:
        tasks = list(app["submitted_tasks"])
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    await submissions.interrupt_unfinished(app, "Service stopped; inspect saved progress before submitting a new run")


async def _service_owned(request: web.Request, operation):
    """Keep submitted work alive if its HTTP client disconnects."""
    tasks = request.app["submitted_tasks"]
    task = asyncio.create_task(operation)
    tasks.add(task)

    def finished(done):
        tasks.discard(done)
        if not done.cancelled():
            # Retrieve exceptions even when the requesting client has gone away.
            done.exception()

    task.add_done_callback(finished)
    return await asyncio.shield(task)


async def session_info(request: web.Request) -> web.Response:
    orch = request.app["orch"]
    return _json({"project_dir": str(orch.config.effective_project_dir)})


def _project_directory(orch, supplied=None) -> str:
    path = Path(supplied or orch.config.effective_project_dir).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Project directory does not exist: {path}")
    return str(path)


def _json(data: Any, status: int = 200) -> web.Response:
    return web.json_response(data, status=status)


def _error(msg: str, status: int = 400) -> web.Response:
    return web.json_response({"error": msg}, status=status)


# ── Health ──────────────────────────────────────────────────────────────

async def health(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    agents = orch.registry.get_running_agents()
    return _json({
        "status": "ok",
        "agents_running": len(agents),
        "roles_loaded": len(orch.roles),
        "workflows_available": len(orch.workflows),
    })


# ── Agents ──────────────────────────────────────────────────────────────

async def list_agents(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    agents = []
    for aid, agent in orch.registry.agents.items():
        agents.append({
            "agent_id": aid,
            "role": agent.role.name,
            "backend": agent.role.backend,
            "status": agent.status.value,
            "name": agent.name,
            "activity": agent.activity,
            "port": getattr(agent, "port", None),
            "ws_url": getattr(agent, "ws_url", None),
        })
    return _json({"agents": agents})


async def spawn_agent(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    body = await request.json()
    role_name = body.get("role")
    if not role_name:
        return _error("'role' is required")
    try:
        agent_id = await orch.spawn_agent(role_name, agent_id=body.get("agent_id"))
        return _json({"agent_id": agent_id, "role": role_name, "status": "running"}, status=201)
    except Exception as e:
        return _error(str(e))


async def stop_agent(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    agent_id = request.match_info["agent_id"]
    try:
        await orch.registry.stop_agent(agent_id)
        return _json({"agent_id": agent_id, "status": "stopped"})
    except KeyError:
        return _error(f"Agent {agent_id} not found", 404)


async def destroy_agent(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    agent_id = request.match_info["agent_id"]
    try:
        await orch.registry.destroy_agent(agent_id)
        return _json({"agent_id": agent_id, "status": "destroyed"})
    except Exception as e:
        return _error(str(e))


async def send_message(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    agent_id = request.match_info["agent_id"]
    record = await orch.store.one('SELECT task_id FROM agents WHERE agent_id=?', (agent_id,))
    if record and record['task_id']:
        return _error('Use the owning task messages endpoint to preserve its lifecycle and workspace lease', 409)
    body = await request.json()
    message = body.get("message")
    if not message:
        return _error("'message' is required")
    try:
        response = await _service_owned(request, orch.send_to_agent(
            agent_id, message,
            thread_id=body.get("thread_id"),
            cwd=body.get("cwd"),
        ))
        return _json({"response": response})
    except Exception as e:
        return _error(str(e))


async def create_thread(request: web.Request) -> web.Response:
    orch = request.app["orch"]
    agent = orch.registry.get_agent(request.match_info["agent_id"])
    if not agent:
        return _error("Agent not found", 404)
    if getattr(agent, 'task_id', None):
        return _error('Task-owned threads are managed by the task service', 409)
    try:
        body = await request.json()
        if not isinstance(body, dict):
            return _error("Expected a thread object")
        cwd = projects.normalize_directory(body.get("cwd"))

        async def create():
            info = await agent.start_thread(cwd=cwd)
            await orch._persist_thread(info)
            return _json({"thread_id": info.thread_id, "cwd": cwd}, status=201)

        return await _service_owned(request, create())
    except Exception as exc:
        return _error(str(exc))


async def list_threads(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    agent_id = request.match_info["agent_id"]
    agent = orch.registry.get_agent(agent_id)
    if not agent:
        return _error(f"Agent {agent_id} not found", 404)
    threads = await agent.list_threads()
    return _json({
        "threads": [
            {"thread_id": t.thread_id, "cwd": t.cwd, "model": t.model, "status": t.status}
            for t in threads
        ]
    })


# ── Roles ───────────────────────────────────────────────────────────────

async def list_roles(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    roles = []
    for name, role in orch.roles.items():
        roles.append({
            "name": name,
            "backend": role.backend,
            "description": role.description,
            "model": role.configured_model,
            "reasoning_effort": role.reasoning_effort,
            "sandbox": role.sandbox,
        })
    return _json({"roles": roles})


# ── Team ────────────────────────────────────────────────────────────────

async def spawn_team(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    body = await request.json()
    role_names = body.get("roles")
    try:
        result = await orch.spawn_team(role_names)
        return _json({"team": result}, status=201)
    except Exception as e:
        return _error(str(e))


# ── Work Items ──────────────────────────────────────────────────────────

async def list_work_items(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    status = request.query.get("status")
    items = await orch.store.list_work_items(status=status)
    return _json({
        "items": [
            {
                "item_id": i.item_id, "title": i.title, "status": i.status,
                "priority": i.priority, "assigned_agent": i.assigned_agent,
                "tags": i.tags,
                "history": next(
                    ([entry] for entry in reversed(i.history) if entry.get("action") == "assigned"),
                    [],
                ),
                "created_at": i.created_at, "updated_at": i.updated_at,
                "completed_at": i.completed_at,
            }
            for i in items
        ]
    })


async def get_work_item(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    item = await orch.store.get_work_item(request.match_info["item_id"])
    if item is None:
        return _error("Work item not found", 404)
    return _json({"item": asdict(item)})


async def list_workflow_runs(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    runs = await orch.store.list_workflow_runs()
    return _json({"runs": [asdict(run) for run in runs]})


async def create_work_item(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    body = await request.json()
    title = body.get("title")
    if not title:
        return _error("'title' is required")
    item_id = await orch.create_work_item(
        title=title,
        description=body.get("description", ""),
        priority=body.get("priority", 0),
        tags=body.get("tags"),
        input_data=body.get("input"),
    )
    return _json({"item_id": item_id}, status=201)


async def assign_work_item(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    item_id = request.match_info["item_id"]
    body = await request.json()
    role = body.get("role")
    if not role:
        return _error("'role' is required")
    try:
        response = await orch.assign_work(item_id, role, message=body.get("message"))
        return _json({"item_id": item_id, "response": response})
    except Exception as e:
        return _error(str(e))


# ── Workflows ──────────────────────────────────────────���────────────────

async def list_workflows(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    wfs = []
    for name, wf in orch.workflows.items():
        wfs.append({
            "name": name,
            "description": wf.description,
            "steps": [s.step_id for s in wf.steps],
            "required_roles": wf.required_roles,
            "publication_policy": wf.publication_policy,
        })
    return _json({"workflows": wfs})


async def run_workflow(request: web.Request) -> web.Response:
    orch = request.app['orch']
    body = await tasks.body(request)
    variables = body.get('variables', {})
    run_id = body.get('run_id') or uuid.uuid4().hex[:16]
    if not isinstance(variables, dict) or not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', run_id):
        return _error('Expected workflow variables and a valid run ID')
    async def execute():
        result = await orch.run_workflow(request.match_info['name'], variables, run_id)
        return _json({'run_id': result.run_id, 'success': result.success,
                      'outputs': {k: str(v)[:2000] for k,v in result.outputs.items()}, 'error': result.error})
    return await _service_owned(request, execute())


async def send_to_role(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    role = request.match_info["role"]
    body = await request.json()
    if not isinstance(body, dict):
        return _error("Expected a message object")
    message = body.get("message")
    if not isinstance(message, str) or not message.strip():
        return _error("'message' is required")
    try:
        cwd = _project_directory(orch, body.get("cwd"))
        response = await _service_owned(request, orch.send_to_role(role, message, cwd=cwd))
        return _json({"response": response})
    except Exception as e:
        return _error(str(e))


# ── SSE Event Stream ───────────────────────────────────────────────────

async def event_stream(request: web.Request) -> web.StreamResponse:
    """Server-Sent Events stream of orchestrator events."""
    orch: Orchestrator = request.app["orch"]
    pattern = request.query.get("pattern", "*")

    response = web.StreamResponse(
        status=200,
        reason="OK",
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
    await response.prepare(request)

    queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    async def handler(event):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    sub = await orch.bus.subscribe(pattern, handler)

    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                data = json.dumps({
                    "topic": event.topic,
                    "source": event.source,
                    "payload": event.payload,
                    "timestamp": event.timestamp.isoformat(),
                })
                await response.write(f"data: {data}\n\n".encode())
            except TimeoutError:
                await response.write(b": keepalive\n\n")
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        await orch.bus.unsubscribe(sub)

    return response


# ── Supervised Send ─────────────────────────────────────────────────────

async def supervised_send(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    role = request.match_info["role"]
    body = await request.json()
    message = body.get("message")
    if not message:
        return _error("'message' is required")
    try:
        result = await orch.supervised_send(role, message, cwd=body.get("cwd"))
        return _json({
            "accepted": result.accepted,
            "response": result.raw_response,
            "noise_ratio": result.noise_ratio,
            "issues": result.issues,
            "deliverables": [
                {"type": d.dtype.value, "evidence": d.evidence, "verified": d.verified}
                for d in result.deliverables
            ],
        })
    except Exception as e:
        return _error(str(e))


# ── Skills ──────────────────────────────────────────────────────────────

async def list_skills(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    skills = orch.skills.list_all()
    return _json({
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "roles": s.roles,
                "inputs": s.input_schema,
                "tags": s.tags,
            }
            for s in skills
        ]
    })


async def run_skill(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    name = request.match_info["name"]
    body = await request.json()
    role = body.get("role")
    if not role:
        return _error("'role' is required")
    try:
        response = await orch.run_skill(
            name, role,
            variables=body.get("variables"),
            cwd=body.get("cwd"),
            supervised=body.get("supervised", True),
        )
        return _json({"response": response})
    except Exception as e:
        return _error(str(e))


# ── HITL ────────────────────────────────────────────────────────────────

async def hitl_pending(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    return _json({
        "input_requests": await orch.store.rows("SELECT * FROM input_requests WHERE status='pending'"),
        "pending_approvals": orch.hitl.pending_approvals,
        "pending_escalations": orch.hitl.pending_escalations,
    })


async def resolve_approval(request: web.Request) -> web.Response:
    from orchestrator.hitl import ApprovalStatus
    orch: Orchestrator = request.app["orch"]
    request_id = request.match_info["request_id"]
    body = await request.json()
    status_str = body.get("status", "approved")
    try:
        status = ApprovalStatus(status_str)
    except ValueError:
        return _error(f"Invalid status: {status_str}")
    if request_id in orch.tasks.input_waiters:
        await orch.tasks.resolve_input(request_id, {'status': status_str, 'response': body.get('response')})
    else:
        orch.hitl.resolve_approval(request_id, status, response=body.get("response"))
    return _json({"request_id": request_id, "status": status_str})


async def resolve_escalation(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    escalation_id = request.match_info["escalation_id"]
    body = await request.json()
    response_text = body.get("response", "")
    orch.hitl.resolve_escalation(escalation_id, response_text)
    return _json({"escalation_id": escalation_id, "status": "resolved"})


# ── Audit ───────────────────────────────────────────────────────────────

async def audit_turns(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    if not orch.audit:
        return _error("Audit not initialized", 500)
    turns = await orch.audit.get_turns(
        role=request.query.get("role"),
        agent_id=request.query.get("agent_id"),
        thread_id=request.query.get("thread_id"),
        limit=int(request.query.get("limit", "50")),
    )
    return _json({"turns": turns})


async def audit_costs(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    if not orch.audit:
        return _error("Audit not initialized", 500)
    summary = await orch.audit.get_cost_summary(
        role=request.query.get("role"),
        agent_id=request.query.get("agent_id"),
    )
    return _json(summary)


async def audit_quality(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    if not orch.audit:
        return _error("Audit not initialized", 500)
    report = await orch.audit.get_quality_report(role=request.query.get("role"))
    return _json(report)


async def audit_timeline(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    if not orch.audit:
        return _error("Audit not initialized", 500)
    timeline = await orch.audit.get_recent_timeline(
        limit=int(request.query.get("limit", "20")),
    )
    return _json({"timeline": timeline})


# ── Workspaces ──────────────────────────────────────────────────────────

async def list_workspaces(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    wts = orch.workspace.list_worktrees()
    return _json({
        "worktrees": [
            {
                "worktree_id": wt.worktree_id,
                "repo": wt.repo_path,
                "path": wt.worktree_path,
                "branch": wt.branch,
                "base_branch": wt.base_branch,
                "agent_id": wt.agent_id,
                "work_item_id": wt.work_item_id,
                "status": wt.status,
            }
            for wt in wts
        ]
    })


async def create_workspace(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    body = await request.json()
    repo = body.get("repo")
    agent_id = body.get("agent_id")
    if not repo or not agent_id:
        return _error("'repo' and 'agent_id' are required")
    try:
        wt = await orch.workspace.create_worktree(
            repo_path=repo,
            agent_id=agent_id,
            work_item_id=body.get("work_item_id"),
            base_branch=body.get("base_branch"),
        )
        return _json({
            "worktree_id": wt.worktree_id,
            "path": wt.worktree_path,
            "branch": wt.branch,
        }, status=201)
    except Exception as e:
        return _error(str(e))


async def workspace_diff(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    wt_id = request.match_info["wt_id"]
    full = request.query.get("full", "false") == "true"
    try:
        if full:
            diff = await orch.workspace.get_full_diff(wt_id)
        else:
            diff = await orch.workspace.get_diff(wt_id)
        return _json({"diff": diff})
    except KeyError:
        return _error(f"Worktree {wt_id} not found", 404)


async def workspace_log(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    wt_id = request.match_info["wt_id"]
    try:
        log = await orch.workspace.get_log(wt_id)
        return _json({"log": log})
    except KeyError:
        return _error(f"Worktree {wt_id} not found", 404)


async def cleanup_workspace(request: web.Request) -> web.Response:
    orch: Orchestrator = request.app["orch"]
    wt_id = request.match_info["wt_id"]
    delete_branch = request.query.get("delete_branch", "false") == "true"
    try:
        await orch.workspace.cleanup_worktree(wt_id, delete_branch=delete_branch)
        return _json({"worktree_id": wt_id, "status": "cleaned"})
    except Exception as e:
        return _error(str(e))


async def run_api(orch: Orchestrator, host: str = "127.0.0.1", port: int = 8420):
    """Start the API server."""
    app = create_app(orch)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("API server running on http://%s:%d", host, port)
    return runner
