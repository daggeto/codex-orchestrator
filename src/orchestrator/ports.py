"""Per-user port reservations shared by all orchestrator processes."""
from __future__ import annotations

import errno
import fcntl
import os
from pathlib import Path
import socket

from orchestrator.config import DEFAULT_DATA_DIR


class PortReservations:
    def __init__(self, directory=None):
        # Deliberately independent of the selected project and database directory.
        self.directory = Path(directory) if directory else DEFAULT_DATA_DIR / 'ports'
        self._handles = {}

    def reserve(self, port):
        if port in self._handles:
            return False
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.directory / f'{port}.lock', os.O_CREAT | os.O_RDWR | os.O_CLOEXEC, 0o600)
        keep = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            # A crashed owner may have left a backend running after its file
            # lock was released. Also skip listeners outside the orchestrator.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                try:
                    probe.bind(('127.0.0.1', port))
                except OSError as exc:
                    if exc.errno in {errno.EADDRINUSE, errno.EACCES}:
                        return False
                    raise
            self._handles[port] = fd
            keep = True
            return True
        finally:
            if not keep:
                os.close(fd)

    def release(self, port):
        fd = self._handles.pop(port, None)
        if fd is not None:
            # Do not unlink lock files: another process may already have opened
            # the inode and be acquiring its lock.
            os.close(fd)
