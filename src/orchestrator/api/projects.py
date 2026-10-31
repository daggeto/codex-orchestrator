"""Persistent server-side project registration, separate from workspaces."""
from __future__ import annotations

import os
from pathlib import Path

from aiohttp import web


def normalize_directory(value: object, *, create: bool = False, validate: bool = True) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A server directory path is required")
    path = Path(value.strip()).expanduser()
    if not path.is_absolute():
        raise ValueError("Use an absolute server directory path")
    path = path.resolve()
    if create:
        path.mkdir(parents=True, exist_ok=True)
    if not validate:
        return str(path)
    if not path.is_dir():
        raise ValueError(f"Directory does not exist or is not a folder: {path}")
    if not os.access(path, os.R_OK | os.X_OK):
        raise ValueError(f"Directory is not accessible: {path}")
    return str(path)


def project_info(record: dict) -> dict:
    path = Path(record["path"])
    return {**record, "name": path.name or str(path),
            "available": path.is_dir() and os.access(path, os.R_OK | os.X_OK)}


async def list_projects(request: web.Request) -> web.Response:
    query = request.query.get("query", "").casefold()
    records = await request.app["orch"].store.list_projects()
    return web.json_response({"projects": [project_info(r) for r in records
                                          if query in r["path"].casefold()]})


async def register_project(request: web.Request) -> web.Response:
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("create_if_missing", False), bool):
            raise ValueError("Expected path and a boolean create_if_missing")
        path = normalize_directory(body.get("path"), create=body.get("create_if_missing", False))
        store = request.app["orch"].store
        await store.register_project(path)
        record = next(r for r in await store.list_projects() if r["path"] == path)
        return web.json_response({"project": project_info(record)}, status=201)
    except (ValueError, OSError, RuntimeError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
