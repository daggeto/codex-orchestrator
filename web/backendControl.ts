import { execFile } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { Plugin } from 'vite';

// Hosted by Vite so Start remains reachable while the API is stopped.
export function backendControl(): Plugin {
  let busy = false;
  let command: string[] | undefined;
  const root = fileURLToPath(new URL('../', import.meta.url));
  function middleware(req: IncomingMessage, res: ServerResponse, next: () => void) {
    if (req.url?.split('?')[0] !== '/__backend') return next();
    const send = (code: number, body: unknown) => {
      res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.end(JSON.stringify(body));
    };
    // Do not expose local process controls through a remote host or cross-origin page.
    const local = ['127.0.0.1', '::1', '::ffff:127.0.0.1'].includes(req.socket.remoteAddress || '');
    const host = req.headers.host || '';
    if (!local || !/^(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$/.test(host)) {
      send(403, { error: 'Backend controls require a local connection.' }); return;
    }
    const action = req.method === 'GET' ? 'status' : req.headers['x-backend-action'];
    if (req.method !== 'GET' && (req.method !== 'POST' || !['start', 'stop', 'restart'].includes(String(action)))) {
      send(405, { error: 'Unsupported backend action.' }); return;
    }
    if (req.method === 'POST' && (!req.headers.origin || ![`http://${host}`, `https://${host}`].includes(req.headers.origin))) {
      send(403, { error: 'Backend controls require a same-origin request.' }); return;
    }
    if (busy) { send(409, { error: 'A backend action is already in progress.' }); return; }
    busy = true;
    const child = execFile('python3', [root + 'scripts/backend_control.py', String(action)],
      { cwd: root, timeout: 75000 }, (error, stdout, stderr) => {
        busy = false;
        if (error) { send(503, { error: stderr.trim() || error.message }); return; }
        try {
          const status = JSON.parse(stdout);
          if (status.command) command = status.command;
          send(200, { state: status.state });
        } catch { send(503, { error: 'Could not read backend status.' }); }
      });
    child.stdin?.end(JSON.stringify({ command }));
  }
  return {
    name: 'local-backend-controls',
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
  };
}
