"""Process ownership and awaited shutdown of managed process groups."""
from __future__ import annotations

import asyncio
import os
import signal
import shutil
import subprocess


def process_identity(pid):
    """Include start time so a recycled PID cannot inherit an execution lease."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return 'unknown'
    try:
        value = subprocess.check_output(
            ['ps', '-p', str(pid), '-o', 'lstart='], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return value or None
    except (OSError, subprocess.CalledProcessError):
        return 'unknown'


def owner_alive(pid, started):
    # Pre-migration leases have no verifiable owner. Retain them conservatively.
    if not pid or not started:
        return True
    identity = process_identity(pid)
    return identity is not None and (identity == 'unknown' or identity == started)


def process_group_alive(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


async def stop_process_group(proc):
    """Stop descendants even if their immediate parent has already exited."""
    def signal_group(sig):
        try:
            os.killpg(proc.pid, sig)
            return True
        except ProcessLookupError:
            return False

    async def wait_group():
        await proc.wait()
        while signal_group(0):
            await asyncio.sleep(0.05)

    if not signal_group(signal.SIGTERM):
        await proc.wait()
        return
    try:
        await asyncio.wait_for(wait_group(), 5)
    except TimeoutError:
        signal_group(signal.SIGKILL)
        try:
            await asyncio.wait_for(wait_group(), 5)
        except TimeoutError as exc:
            raise RuntimeError(f'Process group {proc.pid} did not stop; checkout retained') from exc


async def _managed_listener_owners(pid, owners):
    """Accept only the session leader and descendants in its shutdown group."""
    probe = await asyncio.create_subprocess_exec(
        'ps', '-axo', 'pid=,ppid=,pgid=', start_new_session=True,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(probe.communicate(), 5)
    except BaseException:
        await stop_process_group(probe)
        raise
    if probe.returncode or stderr.strip():
        raise RuntimeError('Cannot verify Codex process ancestry: ' + stderr.decode(errors='replace')[:500])
    try:
        processes = {}
        for line in stdout.decode().splitlines():
            if line.strip():
                child, parent, group = map(int, line.split())
                processes[child] = (parent, group)
    except ValueError as exc:
        raise RuntimeError('Cannot parse Codex process ancestry') from exc
    if pid not in processes or processes[pid][1] != pid:
        return False
    for owner in owners:
        seen = set()
        current = owner
        while current != pid:
            if current in seen or current not in processes:
                return False
            seen.add(current)
            parent, group = processes[current]
            if group != pid:
                return False
            current = parent
    return True


async def process_owns_listener(pid, port):
    """Verify listener ownership by the managed launcher or its native children."""
    lsof = shutil.which('lsof')
    if not lsof:
        raise RuntimeError('lsof is required to verify Codex listener ownership; install it before starting agents')
    probe = await asyncio.create_subprocess_exec(
        lsof, '-nP', f'-iTCP:{port}', '-sTCP:LISTEN', '-Fp',
        start_new_session=True, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        stdout, stderr = await asyncio.wait_for(probe.communicate(), 5)
    except BaseException:
        await stop_process_group(probe)
        raise
    if probe.returncode not in (0, 1) or stderr.strip():
        raise RuntimeError('Cannot verify Codex listener ownership: ' + stderr.decode(errors='replace')[:500])
    owners = {int(line[1:]) for line in stdout.decode().splitlines() if line.startswith('p') and line[1:].isdigit()}
    if not owners:
        return False
    if not await _managed_listener_owners(pid, owners):
        raise RuntimeError(f'Port {port} belongs to another process; refusing to connect')
    return True
