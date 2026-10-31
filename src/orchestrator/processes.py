"""Process ownership and awaited shutdown of managed process groups."""
from __future__ import annotations

import asyncio
import os
import signal
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
