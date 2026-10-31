"""Noninteractive task submission to an existing orchestrator service."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from urllib.parse import quote, urlsplit

import aiohttp
import click

from orchestrator.config import OrchestratorConfig


def _service_url(obj, supplied):
    if supplied:
        url = supplied.rstrip("/")
    else:
        path = obj.get("config_path")
        config = OrchestratorConfig.load(Path(path) if path else None)
        host = config.api_host if config.api_host not in {"0.0.0.0", "::"} else "127.0.0.1"
        if ":" in host:
            host = f"[{host}]"
        url = f"http://{host}:{config.api_port}"
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise click.ClickException("Invalid service URL; use http://127.0.0.1:8420") from exc
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or port == 0
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise click.ClickException("Invalid service URL; use http://127.0.0.1:8420")
    return url


async def _request(session, url, method, path, body=None, *, long_running=False):
    timeout = aiohttp.ClientTimeout(total=None if long_running else 15, connect=5, sock_connect=5)
    try:
        async with session.request(method, url + path, json=body, timeout=timeout) as response:
            try:
                data = await response.json()
            except (ValueError, aiohttp.ContentTypeError) as exc:
                raise click.ClickException(f"Service returned an invalid response (HTTP {response.status})") from exc
            if not isinstance(data, dict):
                raise click.ClickException("Service returned an unexpected response")
            if response.status >= 400:
                raise click.ClickException(str(data.get("error", f"HTTP {response.status}")))
            return data
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise click.ClickException(
            f"Cannot reach {url}: {exc}. Start make start with the API enabled. "
            "If work was already submitted, inspect its run ID before retrying."
        ) from exc


async def submit_task(obj, api_url, workflow, variables):
    url = _service_url(obj, api_url)
    async with aiohttp.ClientSession() as session:
        listing = await _request(session, url, "GET", "/api/workflows")
        workflows = listing.get("workflows")
        if not isinstance(workflows, list):
            raise click.ClickException("Service returned invalid workflow metadata")
        selected = next((wf for wf in workflows if isinstance(wf, dict) and wf.get("name") == workflow), None)
        if selected is None:
            raise click.ClickException(f"Unknown workflow: {workflow}")
        roles = selected.get("required_roles")
        if not isinstance(roles, list) or not all(isinstance(role, str) for role in roles):
            raise click.ClickException("Restart make start to load workflow role metadata")
        click.echo(f"Service: {url}\nWorkflow: {workflow}")
        run_id = uuid.uuid4().hex[:16]
        click.echo(f"Run ID: {run_id}")
        click.echo("Waiting for the result. Ctrl+C leaves submitted work running on the service.")
        try:
            result = await _request(
                session, url, "POST", f"/api/workflows/{quote(workflow, safe='')}/run",
                {"variables": variables, "run_id": run_id}, long_running=True,
            )
            if not isinstance(result.get("success"), bool) or result.get("run_id") != run_id:
                raise click.ClickException("Service returned an invalid task result")
            click.echo(f"Workflow {'succeeded' if result['success'] else 'failed'} (run_id={run_id})")
            for key, value in (result.get("outputs") or {}).items():
                click.echo(f"  {key}: {value}")
            if not result["success"]:
                raise click.ClickException(str(result.get("error") or f"Workflow failed (run_id={run_id})"))
        except asyncio.CancelledError:
            click.echo("\nTask client closed. Any work already submitted continues on the service.")
            raise
        finally:
            click.echo(f"Run history: {url}/api/workflows/runs")
