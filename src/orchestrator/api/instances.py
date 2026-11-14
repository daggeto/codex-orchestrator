"""Checkout-owned development instances with isolated state and local ports."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import sys
import tempfile
import time

from aiohttp import ClientError, ClientSession, ClientTimeout, web


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


async def node_runtime():
    """A backend's inherited PATH may precede the Node used by the web server."""
    candidates = [os.environ.get('ORCH_NODE_BINARY'), os.environ.get('npm_node_execpath'), shutil.which('node')]
    nvm = Path(os.environ.get('NVM_DIR') or Path.home() / '.nvm')
    candidates.extend(str(path) for path in sorted((nvm / 'versions/node').glob('*/bin/node'), reverse=True))
    seen = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            process = await asyncio.create_subprocess_exec(
                candidate, '--version', stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            try:
                output, _ = await asyncio.wait_for(process.communicate(), 3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                continue
            version = tuple(int(part) for part in output.decode().strip().removeprefix('v').split('.'))
            if process.returncode == 0 and ((20, 19, 0) <= version < (21, 0, 0) or version >= (22, 12, 0)):
                return candidate
        except (OSError, ValueError):
            continue
    raise ValueError('Vite requires Node 20.19+ or 22.12+. Install a compatible Node version or set ORCH_NODE_BINARY for the backend.')


class Instances:
    def __init__(self, orch):
        self.orch = orch
        self.items = {}
        self.lock = asyncio.Lock()

    def status(self, wid):
        item = self.items.get(wid)
        if not item:
            return {'state': 'stopped'}
        alive = all(process.returncode is None for process in item['processes'])
        state = ('running' if item['ready'] else 'starting') if alive else 'failed'
        return {'state': state, 'url': item['url'] if state == 'running' else None,
                'data_dir': str(item['directory'] / 'data'), 'log_path': str(item['directory'] / 'instance.log')}

    async def stop(self, wid):
        item = self.items.get(wid)
        if not item:
            return
        # These process groups were created by this server, never recovered by PID.
        for process in reversed(item['processes']):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        for process in item['processes']:
            try:
                await asyncio.wait_for(process.wait(), 10)
            except asyncio.TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
        self.items.pop(wid, None)

    async def start(self, wid):
        if self.status(wid)['state'] == 'running':
            return self.status(wid)
        await self.stop(wid)
        workspace = await self.orch.store.workspace_record(wid)
        if not workspace:
            raise LookupError('Workspace not found')
        if workspace['kind'] != 'worktree' or workspace['status'] != 'active':
            raise ValueError('Choose an active worktree to start a test instance.')
        root = Path(workspace['path']).resolve()
        frontend = root / 'web'
        vite = frontend / 'node_modules/vite/bin/vite.js'
        if not (root / 'src/orchestrator/cli.py').is_file():
            raise ValueError('Test instances currently support Orchestrator worktrees only.')
        if not vite.is_file():
            raise ValueError(f'Install frontend dependencies in {root} with make web-install, then retry.')
        node = await node_runtime()
        python = root / '.venv/bin/python'
        if not python.is_file():
            python = Path(sys.executable)
        directory = Path(tempfile.mkdtemp(prefix='orch-test-'))
        api_port = free_port()
        web_port = free_port()
        while web_port == api_port:
            web_port = free_port()
        url = f'http://127.0.0.1:{web_port}'
        config = directory / 'config.toml'
        config.write_text('\n'.join(f'{key} = {json.dumps(value)}' for key, value in {
            'data_dir': str(directory / 'data'), 'project_dir': str(root),
            'roles_dir': str(root / 'roles'), 'workflows_dir': str(root / 'workflows'),
            'api_host': '127.0.0.1', 'browser_origins': [url],
        }.items()))
        # Load this checkout's styling/aliases, replacing its live API proxy and
        # removing the Vite plugin that controls the primary backend.
        vite_config = directory / 'vite.config.mjs'
        vite_config.write_text(
            f'import original from {json.dumps(str(frontend / "vite.config.ts"))};\n'
            'export default async (env) => {\n'
            'const config = await (typeof original === "function" ? original(env) : original);\n'
            f'const target = "http://127.0.0.1:{api_port}";\n'
            'async function clean(entries) { entries = await entries; if (Array.isArray(entries)) return (await Promise.all(entries.map(clean))).flat(Infinity);\n'
            'return entries && entries.name !== "local-backend-controls" ? [entries] : []; }\n'
            'return {...config, plugins: [...await clean(config.plugins), {name: "isolated-test-controls", enforce: "pre",\n'
            'configureServer(server) { server.middlewares.use((req,res,next) => {\n'
            'if (req.url?.split("?")[0] !== "/__backend") return next();\n'
            'res.writeHead(403); res.end("Manage this instance from its worktree page."); }); }}],\n'
            f'root: {json.dumps(str(frontend))}, cacheDir: {json.dumps(str(directory / "vite-cache"))},\n'
            f'server: {{host: "127.0.0.1", port: {web_port}, strictPort: true,\n'
            'proxy: {"/api": {target, changeOrigin: true}, "/health": {target, changeOrigin: true}}}}; };\n')
        item = {'directory': directory, 'url': url, 'processes': [], 'ready': False}
        self.items[wid] = item
        env = {**os.environ, 'PYTHONPATH': str(root / 'src'),
               'PATH': str(Path(node).parent) + os.pathsep + os.environ.get('PATH', '')}
        try:
            with (directory / 'instance.log').open('ab') as output:
                for command in (
                    [str(python), '-m', 'orchestrator', '--config', str(config), 'start', '--api-port', str(api_port)],
                    [node, str(vite), '--config', str(vite_config)],
                ):
                    item['processes'].append(await asyncio.create_subprocess_exec(
                        *command, cwd=root, env=env, stdin=asyncio.subprocess.DEVNULL,
                        stdout=output, stderr=output, start_new_session=True))
            async with ClientSession(timeout=ClientTimeout(total=1)) as session:
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline:
                    if any(p.returncode is not None for p in item['processes']):
                        break
                    try:
                        async with session.get(f'http://127.0.0.1:{api_port}/health') as api:
                            async with session.get(url) as page:
                                if api.status == page.status == 200:
                                    item['ready'] = True
                                    return self.status(wid)
                    except (ClientError, OSError, asyncio.TimeoutError):
                        pass
                    await asyncio.sleep(0.25)
            log_path = directory / 'instance.log'
            with log_path.open('rb') as log:
                log.seek(0, os.SEEK_END)
                log.seek(max(0, log.tell() - 3000))
                detail = log.read().decode(errors='replace').strip()
            raise RuntimeError(f'Test instance did not start. {detail}\nFull log: {log_path}')
        except BaseException:
            await self.stop(wid)
            raise


async def instance(request):
    service = request.app['test_instances']
    wid = request.match_info['workspace_id']
    if request.method == 'GET':
        return web.json_response(service.status(wid))
    async with service.lock:
        async with service.orch.workspaces.lock(wid):
            if request.method == 'DELETE':
                await service.stop(wid)
            else:
                return web.json_response(await service.start(wid))
            return web.json_response(service.status(wid))


def register(app):
    service = Instances(app['orch'])
    app['test_instances'] = service
    app.router.add_route('GET', '/api/workspaces/{workspace_id}/instance', instance)
    app.router.add_route('POST', '/api/workspaces/{workspace_id}/instance', instance)
    app.router.add_route('DELETE', '/api/workspaces/{workspace_id}/instance', instance)

    async def cleanup(app):
        async with service.lock:
            for wid in list(service.items):
                await service.stop(wid)
    app.on_shutdown.append(cleanup)
