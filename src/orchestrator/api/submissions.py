"""Accepted workflow submissions owned by the service, not an HTTP connection."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict
from datetime import datetime, timezone

from aiohttp import web

from orchestrator.api.projects import normalize_directory
from orchestrator.state.store import WorkflowRunRecord


def own_task(app, operation):
    task = asyncio.create_task(operation)
    app["submitted_tasks"].add(task)

    def finished(done):
        app["submitted_tasks"].discard(done)
        if not done.cancelled():
            done.exception()

    task.add_done_callback(finished)
    return task


async def recover_submissions(app):
    # Accepted work survives browser navigation. Service restarts never silently
    # replay potentially destructive steps; retain an explicit interrupted state.
    await interrupt_unfinished(app, "Service restarted; inspect saved progress before submitting a new run")


async def interrupt_unfinished(app, reason):
    store = app["orch"].store
    for record in await store.list_workflow_runs():
        if record.config.get("submission_fingerprint") and record.status in {"accepted", "starting", "running"}:
            record.status = "interrupted"
            record.state.update(phase="interrupted", error=reason)
            await store.save_workflow_run(record)


async def execute(app, workflow, record):
    orch = app["orch"]
    try:
        record.state["phase"] = "starting_team"
        await orch.store.save_workflow_run(record)
        team = await orch.spawn_team(workflow.required_roles)
        record.state["team"] = team
        await orch.store.save_workflow_run(record)
        await orch.engine.run(workflow, variables=record.config["variables"],
                              run_id=record.run_id, accepted=True)
    except (Exception, asyncio.CancelledError) as exc:
        latest = await orch.store.get_workflow_run(record.run_id) or record
        latest.status = "interrupted" if isinstance(exc, asyncio.CancelledError) else "failed"
        latest.state.update(phase=latest.status, error=str(exc) or "Service stopped during submission")
        latest.completed_at = datetime.now(timezone.utc).isoformat()
        await orch.store.save_workflow_run(latest)
        if isinstance(exc, asyncio.CancelledError):
            raise


async def submit(request: web.Request) -> web.Response:
    # Shield acceptance itself as well as execution: disconnecting while SQLite
    # commits cannot leave an accepted record without a scheduled service task.
    return await asyncio.shield(own_task(request.app, accept(request)))


async def accept(request):
    orch = request.app["orch"]
    workflow = orch.workflows.get(request.match_info["name"])
    if workflow is None:
        return web.json_response({"error": "Unknown workflow"}, status=404)
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("variables"), dict):
            raise ValueError("Expected run_id and workflow variables")
        rid = body.get("run_id")
        if not isinstance(rid, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid):
            raise ValueError("run_id must contain 1-64 letters, digits, underscores or hyphens")
        variables = dict(body["variables"])
        task = variables.get("task_description")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task_description is required")
        variables["task_description"] = task.strip()
        if workflow.publication_policy == "optional":
            variables.setdefault("create_mr", False)
        if workflow.publication_policy == "required" and str(variables.get("create_mr", True)).lower() in {"false", "0", "no"}:
            raise ValueError("This workflow requires PR/MR publication")
        request_fingerprint = hashlib.sha256(json.dumps([workflow.name, variables], sort_keys=True).encode()).hexdigest()
        previous = await orch.store.get_workflow_run(rid)
        if previous:
            matches = previous.config.get("request_fingerprint") == request_fingerprint
            if not previous.config.get("request_fingerprint"):
                # Compatibility for acceptances saved before raw request identity.
                legacy = dict(variables)
                legacy["cwd"] = normalize_directory(legacy.get("cwd"), validate=False)
                matches = previous.config.get("submission_fingerprint") == hashlib.sha256(
                    json.dumps([workflow.name, legacy], sort_keys=True).encode()).hexdigest()
            if not matches:
                return web.json_response({"error": "Run ID belongs to a different submission"}, status=409)
            return web.json_response({"run_id": rid, "status_url": f"/api/workflows/runs/{rid}"}, status=202)
        variables["cwd"] = normalize_directory(variables.get("cwd"))
        if not any(p["path"] == variables["cwd"] for p in await orch.store.list_projects()):
            raise ValueError("Register this project before submitting a task")
        fingerprint = hashlib.sha256(json.dumps([workflow.name, variables], sort_keys=True).encode()).hexdigest()
        record = WorkflowRunRecord(
            run_id=rid, workflow_name=workflow.name, status="accepted",
            config={"variables": variables, "submission_fingerprint": fingerprint,
                    "request_fingerprint": request_fingerprint},
            state={"phase": "accepted", "required_roles": workflow.required_roles},
        )
        if await orch.store.reserve_workflow_run(record):
            own_task(request.app, execute(request.app, workflow, record))
        else:
            previous = await orch.store.get_workflow_run(rid)
            if not previous or previous.config.get("submission_fingerprint") != fingerprint:
                return web.json_response({"error": "Run ID belongs to a different submission"}, status=409)
        return web.json_response({"run_id": rid, "status_url": f"/api/workflows/runs/{rid}"}, status=202)
    except (ValueError, OSError, RuntimeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def get_run(request):
    record = await request.app["orch"].store.get_workflow_run(request.match_info["run_id"])
    if record is None:
        return web.json_response({"error": "Workflow run not found"}, status=404)
    return web.json_response({"run": asdict(record)})
