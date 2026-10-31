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
        if (record.config.get("submission_fingerprint") or record.state.get("continuations")) and record.status in {"accepted", "starting", "running"}:
            record.status = "interrupted"
            record.state.update(phase="interrupted", error=reason)
            await store.save_workflow_run(record)


async def execute(app, workflow, record, *, continuing=False):
    orch = app["orch"]
    try:
        record.state["phase"] = "starting_team"
        await orch.store.save_workflow_run(record)
        team = await orch.spawn_team(workflow.required_roles)
        record.state["team"] = team
        await orch.store.save_workflow_run(record)
        await orch.engine.run(workflow, variables=record.config["variables"],
                              run_id=record.run_id, accepted=True, continuing=continuing)
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
            if not matches:
                # Equivalent paths retain the normalized-payload contract, even
                # when the accepted directory is no longer available.
                normalized = dict(variables)
                normalized["cwd"] = normalize_directory(normalized.get("cwd"), validate=False)
                matches = previous.config.get("submission_fingerprint") == hashlib.sha256(
                    json.dumps([workflow.name, normalized], sort_keys=True).encode()).hexdigest()
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
    workflow = request.app["orch"].workflows.get(record.workflow_name)
    reason = None
    if record.status not in {"failed", "interrupted", "cancelled", "paused"}:
        reason = "Only stopped runs can continue"
    elif workflow is None:
        reason = "Workflow definition unavailable"
    else:
        from orchestrator.workflows.engine import continuation_checkpoint
        try:
            continuation_checkpoint(workflow, record)
        except ValueError as exc:
            reason = str(exc)
    iteration_limit = record.config.get("iteration_limit", workflow.max_iterations if workflow else 20)
    checkpoint = record.state.get("checkpoint", {})
    requires_extension = (checkpoint.get("next_step") != "__end__"
                          and checkpoint.get("iteration", 0) >= iteration_limit)
    return web.json_response({"run": asdict(record), "continuation": {
        "available": reason is None, "reason": reason,
        "requires_extension": requires_extension,
        "iteration_limit": iteration_limit, "next_iteration_limit": iteration_limit + 20,
        "step": record.state.get("checkpoint", {}).get("next_step", record.state.get("current_step")),
    }})


async def continue_run(request):
    return await asyncio.shield(own_task(request.app, accept_continuation(request)))


async def accept_continuation(request):
    orch = request.app["orch"]
    try:
        body = await request.json()
        token = body.get("request_id") if isinstance(body, dict) else None
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", token):
            raise ValueError("request_id must contain 1-64 letters, digits, underscores or hyphens")
        record = await orch.store.get_workflow_run(request.match_info["run_id"])
        if record is None:
            return web.json_response({"error": "Workflow run not found"}, status=404)
        attempts = record.state.get("continuations", [])
        if any(attempt["request_id"] == token for attempt in attempts):
            return web.json_response({"run_id": record.run_id}, status=202)
        if record.status not in {"failed", "interrupted", "cancelled", "paused"}:
            return web.json_response({"error": "This workflow is already active or completed"}, status=409)
        workflow = orch.workflows.get(record.workflow_name)
        if workflow is None:
            raise ValueError("Workflow definition unavailable")
        from orchestrator.workflows.engine import continuation_checkpoint
        checkpoint = continuation_checkpoint(workflow, record)
        iteration_limit = record.config.get("iteration_limit", workflow.max_iterations)
        additional_steps = body.get("additional_steps", 0)
        if type(additional_steps) is not int or additional_steps not in (0, 20):
            raise ValueError("additional_steps must be 0 or 20")
        exhausted = (checkpoint["next_step"] != "__end__"
                     and checkpoint.get("iteration", 0) >= iteration_limit)
        if exhausted and additional_steps != 20:
            return web.json_response({"error": "Step budget reached; approve 20 more steps to continue"}, status=409)
        if additional_steps and not exhausted:
            raise ValueError("The current step budget has not been reached")
        record.config["iteration_limit"] = iteration_limit + additional_steps
        variables = checkpoint["variables"]
        normalize_directory(variables.get("cwd"))
        if variables.get("task_worktree"):
            from orchestrator.workflows.merge_request import _run
            root = normalize_directory(variables["task_worktree"])
            if root != normalize_directory(variables.get("cwd")):
                raise ValueError("Saved workflow directory does not match its task worktree")
            if await _run(root, "git", "branch", "--show-current") != variables.get("task_branch"):
                raise ValueError("The saved worktree is on a different branch")
            await _run(root, "git", "merge-base", "--is-ancestor", variables["task_baseline"], "HEAD")
        record.state["continuations"] = [*attempts, {
            "request_id": token, "previous_status": record.status,
            "previous_error": record.state.get("error"),
            "step": checkpoint["next_step"], "at": datetime.now(timezone.utc).isoformat(),
            "additional_steps": additional_steps, "iteration_limit": record.config["iteration_limit"],
        }]
        record.state.update(checkpoint=checkpoint, phase="accepted", error=None)
        if not await orch.store.claim_workflow_continuation(record):
            latest = await orch.store.get_workflow_run(record.run_id)
            if not latest or not any(a["request_id"] == token for a in latest.state.get("continuations", [])):
                return web.json_response({"error": "Run changed; refresh before continuing"}, status=409)
        else:
            record.status = "accepted"
            record.completed_at = None
            own_task(request.app, execute(request.app, workflow, record, continuing=True))
        return web.json_response({"run_id": record.run_id}, status=202)
    except (ValueError, OSError, RuntimeError, KeyError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
