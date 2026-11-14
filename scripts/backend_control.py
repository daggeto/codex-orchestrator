"""Local web-server helper for the checkout-owned backend on port 8420."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

from stop_services import ROOT, belongs_to_checkout, identity, probe

PORT = 8420
LOG = ROOT / ".backend.log"


def launch_environment() -> dict[str, str]:
    env = os.environ.copy()
    # A terminal may supply PYTHONPATH even when the web server does not.
    source = str(ROOT / "src")
    env["PYTHONPATH"] = source + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def prepare_launch() -> None:
    python = ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        raise RuntimeError("Backend is not installed. Run make install first.")
    result = subprocess.run(
        [str(python), "-c", "import orchestrator.cli"], cwd=ROOT,
        env=launch_environment(), capture_output=True, text=True, timeout=10,
    )
    if result.returncode:
        raise RuntimeError(f"Backend cannot start; the running backend was left unchanged. Run make install. {result.stderr.strip()}")


def launch_command(command: list[str] | None) -> list[str]:
    prefix = [str(ROOT / ".venv" / "bin" / "python"), "-m", "orchestrator"]
    if not command:
        return [*prefix, "start", "--api-port", str(PORT)]
    # ps may expose the resolved base interpreter, losing virtualenv discovery.
    # Keep CLI arguments, but always launch with the interpreter we preflight.
    for index, argument in enumerate(command):
        if Path(argument).name == "orch" and "start" in command[index + 1:]:
            return [*prefix, *command[index + 1:]]
        if command[index:index + 2] == ["-m", "orchestrator"] and "start" in command[index + 2:]:
            return [*prefix, *command[index + 2:]]
    raise RuntimeError("Cannot recover backend launch arguments. The running backend was left unchanged.")


def inspect() -> dict:
    listeners = probe(["lsof", "-nP", f"-iTCP:{PORT}", "-sTCP:LISTEN", "-t"])
    pids = sorted({int(value) for value in listeners.split()})
    if not pids:
        return {"state": "stopped"}
    if len(pids) != 1 or not belongs_to_checkout(pids[0], True):
        raise RuntimeError("Port 8420 belongs to another service or checkout. Manage it from its own terminal.")
    pid = pids[0]
    started = identity(pid)
    command = shlex.split(probe(["ps", "-ww", "-p", str(pid), "-o", "args="]))
    ready = False
    try:
        with urlopen(f"http://127.0.0.1:{PORT}/health", timeout=1) as response:
            ready = response.status == 200
    except OSError:
        pass
    return {"state": "running" if ready else "starting", "pid": pid, "identity": started, "command": command}


def stop(status: dict) -> None:
    if status["state"] == "stopped":
        return
    pid = status["pid"]
    if identity(pid) != status["identity"] or not belongs_to_checkout(pid, True):
        raise RuntimeError("Backend ownership changed. Refresh and try again.")
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + 30
    while identity(pid) == status["identity"]:
        if time.monotonic() >= deadline:
            raise RuntimeError("Backend shutdown timed out. No force kill was sent.")
        time.sleep(0.2)


def start(command: list[str]) -> None:
    if inspect()["state"] != "stopped":
        return
    with LOG.open("ab") as output:
        process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=output, start_new_session=True,
                                   env=launch_environment())
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Backend exited during startup. See .backend.log in the project directory.")
        if inspect()["state"] == "running":
            return
        time.sleep(0.3)
    raise RuntimeError("Backend is still starting. See .backend.log in the project directory.")


def main() -> None:
    action = sys.argv[1]
    status = inspect()
    command = status.get("command")
    if action == "status":
        print(json.dumps(status))
        return
    if action not in {"start", "stop", "restart"}:
        raise ValueError("Unknown backend action")
    saved = json.load(sys.stdin)
    command = command or saved.get("command")
    if action in {"start", "restart"}:
        command = launch_command(command)
        prepare_launch()
    if action in {"stop", "restart"}:
        stop(status)
    if action in {"start", "restart"}:
        start(command)
    print(json.dumps({**inspect(), "command": command}))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
