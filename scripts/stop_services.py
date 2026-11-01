"""Stop checkout-owned service listeners without project dependencies."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]


def probe(argv: list[str]) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=5)
    # lsof and ps return 1 when the requested process/listener no longer exists.
    if result.returncode not in (0, 1) or result.stderr.strip():
        raise RuntimeError(f"Cannot inspect service: {result.stderr.strip() or argv[0]}")
    return result.stdout.strip()


def identity(pid: int) -> str:
    return probe(["ps", "-p", str(pid), "-o", "lstart="])


def belongs_to_checkout(pid: int, api: bool) -> bool:
    cwd = probe(["lsof", "-a", "-p", str(pid), "-d", "cwd", "-Fn"])
    directories = [Path(line[1:]).resolve() for line in cwd.splitlines() if line.startswith("n")]
    expected = ROOT if api else ROOT / "web"
    if expected not in directories:
        return False
    argv = shlex.split(probe(["ps", "-p", str(pid), "-o", "args="]))
    if api:
        return any(
            Path(arg).name == "orch" and "start" in argv[index + 1:]
            for index, arg in enumerate(argv)
        ) or any(argv[index:index + 3] == ["-m", "orchestrator", "start"] for index in range(len(argv)))
    return any(arg.endswith("/vite/bin/vite.js") or Path(arg).name == "vite" for arg in argv)


def port_number(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return number


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-port", type=port_number, default=8420)
    parser.add_argument("--web-port", type=port_number, default=5173)
    parser.add_argument("--preview-port", type=port_number, default=4173)
    parser.add_argument("--timeout", type=int, default=30, help="shutdown wait in seconds (default: 30)")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    for tool in ("lsof", "ps"):
        if not shutil.which(tool):
            raise RuntimeError(f"{tool} is required to identify service processes")

    services: dict[int, tuple[str, str]] = {}
    refused = False
    # Inspect every listener before sending any signals.
    for label, port, api in (("API", args.api_port, True), ("web", args.web_port, False),
                             ("preview", args.preview_port, False)):
        listeners = probe(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"])
        for pid in sorted({int(value) for value in listeners.split()}):
            started = identity(pid)
            if not started:
                continue
            if not belongs_to_checkout(pid, api):
                print(f"Refusing to stop PID {pid} on port {port}: not a verified {label} service for {ROOT}.", file=sys.stderr)
                refused = True
                continue
            services[pid] = (started, label)

    pending = {}
    for pid, (started, label) in services.items():
        if identity(pid) != started:
            continue
        try:
            os.kill(pid, signal.SIGINT)
        except ProcessLookupError:
            continue
        print(f"Stopping {label} (PID {pid})…", flush=True)
        pending[pid] = started

    deadline = time.monotonic() + args.timeout
    while pending:
        pending = {pid: started for pid, started in pending.items() if identity(pid) == started}
        if not pending:
            break
        if time.monotonic() >= deadline:
            raise RuntimeError(f"Shutdown timed out for PIDs {', '.join(map(str, pending))}; no force kill sent")
        time.sleep(0.2)
    if services:
        print("Service shutdown complete.")
    elif not refused:
        print("No services running on the selected ports.")
    return 1 if refused else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
