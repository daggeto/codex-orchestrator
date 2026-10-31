"""Directory picker constrained to canonical configured service-machine roots."""
from pathlib import Path
from aiohttp import web


def roots(orch):
    return [Path(p).expanduser().resolve() for p in (orch.config.browse_roots or [str(Path.home()), str(orch.config.effective_project_dir.parent)])]


def permitted(orch, value):
    if not isinstance(value, str) or not value.strip() or not Path(value).expanduser().is_absolute():
        raise ValueError('An absolute server directory path is required')
    path = Path(value).expanduser().resolve()
    if not any(path.is_relative_to(root) for root in roots(orch)):
        raise ValueError('Directory is outside configured browse roots')
    if not path.is_dir():
        raise ValueError('Directory is unavailable')
    return path


async def directories(request):
    orch = request.app['orch']
    allowed = roots(orch)
    path = permitted(orch, request.query.get('path') or str(allowed[0]))
    children = []
    for entry in sorted(path.iterdir(), key=lambda p: p.name.casefold()):
        try:
            canonical = permitted(orch, str(entry))
            if not entry.name.startswith('.'):
                children.append({'name': entry.name, 'path': str(canonical)})
        except (ValueError, OSError):
            continue
    parent = path.parent if any(path.parent.is_relative_to(root) for root in allowed) else None
    return web.json_response({'path': str(path), 'parent': str(parent) if parent else None,
                              'roots': [str(r) for r in allowed], 'directories': children})
