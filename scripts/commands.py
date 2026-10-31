"""Single catalog for Make targets, execution, summaries, and detailed help.

Keep this catalog aligned with cli.py and web/package.json. No project
dependencies are needed to read help; execution forwards arguments as argv.
"""

from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
from dataclasses import dataclass


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    backend: str
    argv: tuple[str, ...]
    details: str
    examples: tuple[str, ...]


COMMANDS = (
    Command("start", "Start the orchestrator service without prompts.", "orch", ("start",),
            "Starts immediately using configuration and supplied options. "
            "Roles are loaded from --roles-dir or configured roles_dir (global roles/ by default), "
            "with target-project roles/ loaded as well. "
            "Runs until Ctrl+C, which stops the service and its agents. "
            "Use --cwd for the target project, --roles-dir for separate role definitions, "
            "--api-port to change the port, --no-api to disable HTTP, and repeat --spawn-team for initial agents. "
            "The API port defaults to 8420. The task home registers server projects persistently, "
            "accepts workflows asynchronously, and starts/reuses their required team. "
            "Browser disconnects leave accepted work running; service shutdown interrupts it.",
            ("make start",
             'make start ARGS="--cwd /path/to/repo --roles-dir ./roles --spawn-team architect"')),
    Command("task", "Submit a task to the running service without prompts.", "orch", ("task",),
            "Requires a quoted DESCRIPTION inside ARGS. Connects to the service started by make start; "
            "starts missing workflow roles or reuses its agents, waits for the result, and prints the run ID. "
            "Uses the service's project directory unless --cwd is supplied. --workflow selects a workflow "
            "(feature by default); --api-url selects another service port. Repeat --var/-v KEY=VALUE for inputs. "
            "The implementer must use a separate task worktree unless the task explicitly requests main-branch "
            "implementation, and report its path and branch for review/publication. "
            "Feature tasks default to create_mr=false (local edits and commits); -v create_mr=true enables "
            "draft PR/MR publication and CI verification. pr-completion includes publication. "
            "Ctrl+C closes the client while submitted work remains running. Failed tasks return a nonzero exit status. "
            "This command does not start or stop an orchestrator service.",
            ("""make task ARGS='"Add search to the transactions page"'""",
             """make task ARGS='"Investigate slow imports" --workflow research --api-url http://127.0.0.1:8421'""",
             """make task ARGS='"Add OAuth login" -v create_mr=true -v mr_base=main'""")),
    Command("roles", "List available role definitions and models.", "orch", ("roles",),
            "Reads role definitions from the configured roles directory and project roles directory.",
            ("make roles",)),
    Command("status", "Show saved agents, threads, and work-item counts.", "orch", ("status",),
            "Reads persisted status and prints the data directory. Saved running status is not a process health check.",
            ("make status",)),
    Command("team", "Spawn a team using the supplied role names.", "orch", ("team",),
            "Pass space-separated roles, or omit them to use configured defaults. This CLI invocation stops "
            "its orchestrator on completion; use start --spawn-team for a long-running team.",
            ('make team ARGS="architect implementer reviewer"',)),
    Command("send", "Send a message to an agent by role.", "orch", ("send",),
            "Requires ROLE MESSAGE. Quote the message inside ARGS to preserve it as one argument. "
            "Use --cwd to set the agent working directory. The implementer must use a separate task worktree "
            "unless the message explicitly requests main-branch implementation.",
            ("""make send ARGS='architect "Design an auth system" --cwd /path/to/repo'""",)),
    Command("supervised-send", "Send a role message with deliverable supervision.", "orch", ("supervised-send",),
            "Requires ROLE MESSAGE and accepts --cwd. Reports accepted output or supervision issues, "
            "then lists detected deliverables and whether they were verified. The implementer must use "
            "a separate task worktree unless the message explicitly requests main-branch implementation.",
            ("""make supervised-send ARGS='implementer "Fix the user-list query" --cwd /path/to/repo'""",)),
    Command("agent-spawn", "Spawn one agent with a role and optional ID.", "orch", ("agent", "spawn"),
            "Requires ROLE and accepts --id. This invocation shuts down its orchestrator afterward; "
            "use start --spawn-team for persistent agents.",
            ('make agent-spawn ARGS="architect --id design-agent"',)),
    Command("agent-list", "List saved agent IDs, roles, backends, and status.", "orch", ("agent", "list"),
            "Reads agent records from the configured store without starting agents.",
            ("make agent-list",)),
    Command("agent-send", "Send a message to a specific agent ID.", "orch", ("agent", "send"),
            "Requires AGENT_ID MESSAGE. Use --thread/-t to resume a thread and --cwd to set its working directory.",
            ("""make agent-send ARGS='design-agent "Continue the design" --thread THREAD_ID --cwd /path/to/repo'""",)),
    Command("workflow-list", "List workflows and their step counts.", "orch", ("workflow", "list"),
            "Built-ins are feature, bugfix, review, research, and pr-completion.",
            ("make workflow-list",)),
    Command("workflow-run", "Run a named workflow with variables and a target directory.", "orch", ("workflow", "run"),
            "Requires NAME. Repeat --var/-v KEY=VALUE for workflow inputs. --cwd sets the target repository; "
            "--roles-dir selects role definitions independently. Most workflows require available agents. "
            "pr-completion automatically starts/reuses implementer and reviewer, publishes committed changes, "
            "and monitors/repairs CI. feature can also create a draft PR/MR. feature, bugfix, and pr-completion "
            "create an isolated task worktree shared by implementation, review, QA, and publication. "
            "Review uses the selected publication target even when the task branch differs. "
            "Existing PR/MR source branches are preserved when publishing from the task checkout. "
            "Worktrees are retained for inspection and resume. In the web monitor, Continue workflow retries the unfinished step using saved outputs and threads; Restart run starts over. At its step budget the run pauses; Continue workflow can explicitly approve 20 more steps, including for older limit-failed runs with checkpoints. Continuation may publish when publication was enabled in the original inputs. See docs/pr-completion.md for its inputs.",
            ("""make workflow-run ARGS='feature -v task_description="Add OAuth login" --cwd /path/to/repo -v mr_base=main'""",
             """make workflow-run ARGS='pr-completion --cwd /path/to/repo --roles-dir ./roles -v required_checks=Vercel'""")),
    Command("workflow-show", "Show a saved workflow phase and complete CI report.", "orch", ("workflow", "show"),
            "Requires RUN_ID. Reads saved state, including PR/MR URL, CI SHA/status, repair counts, "
            "and errors, without starting agents.",
            ('make workflow-show ARGS="RUN_ID"',)),
    Command("skill-list", "List skills, optionally filtered by role.", "orch", ("skill", "list"),
            "Reads available built-in and project skill definitions. Use --role ROLE to filter them.",
            ("make skill-list", 'make skill-list ARGS="--role reviewer"')),
    Command("skill-run", "Run a skill using a role and input variables.", "orch", ("skill", "run"),
            "Requires NAME ROLE. Repeat -v KEY=VALUE for template inputs. Accepts --cwd and --no-supervise; "
            "supervision is enabled by default. Requires available agents.",
            ("""make skill-run ARGS='investigate-bug architect -v bug_description="OOM on uploads" --cwd /path/to/repo'""",)),
    Command("audit-turns", "Show recent turns with timing and token usage.", "orch", ("audit", "turns"),
            "Defaults to 20 recent turns. --limit/-n changes the count. The CLI currently accepts --role "
            "but does not apply that filter to the timeline.",
            ('make audit-turns ARGS="-n 50"',)),
    Command("audit-costs", "Show recorded token usage and estimated costs.", "orch", ("audit", "costs"),
            "Prints totals, cached tokens, average cost, and maximum turn cost. Use --role ROLE to filter.",
            ("make audit-costs", 'make audit-costs ARGS="--role implementer"')),
    Command("audit-quality", "Show supervision acceptance rates and response quality.", "orch", ("audit", "quality"),
            "Reports supervised turn counts, acceptance/rejection rates, noise, length, and duration. "
            "Use --role ROLE to filter.",
            ("make audit-quality", 'make audit-quality ARGS="--role reviewer"')),
    Command("install", "Create .venv if needed and install the Python project.", "install", (),
            "Uses the Python interpreter running this catalog to create .venv, then runs .venv/bin/python "
            "-m pip install -e . with any ARGS. Choose Python 3.12+ with PYTHON=python3.12. "
            "An existing environment is reused. Agent execution also needs codex and/or claude on PATH.",
            ("make install PYTHON=python3.12",)),
    Command("web-install", "Install frontend dependencies from the lockfile.", "npm", ("ci",),
            "Runs npm ci in web/. Requires Node 20.19+ or 22.12+. Replaces node_modules using the lockfile.",
            ("make web-install",)),
    Command("dev", "Start the task home and monitor development server.", "npm", ("run", "dev"),
            "Runs Vite in web/. Start make start in another terminal for the API on port 8420. "
            "The development server proxies /api and /health to that API. Task home defaults feature "
            "submissions to local commits (create_mr=false); publication is opt-in for feature and "
            "required for pr-completion. The monitor remains read-only.",
            ("make dev", 'make dev ARGS="--port 5174"')),
    Command("preview", "Serve the production web build locally.", "npm", ("run", "preview"),
            "Runs Vite preview in web/. Run make build first and keep the orchestrator API running.",
            ("make preview", 'make preview ARGS="--port 4174"')),
    Command("typecheck", "Check frontend TypeScript without emitting files.", "npm", ("run", "typecheck"),
            "Runs the web package's TypeScript compiler with --noEmit.",
            ("make typecheck",)),
    Command("build", "Check TypeScript and build the production web assets.", "npm", ("run", "build"),
            "Runs the web build script, which typechecks before running Vite. Output goes to web/dist/.",
            ("make build",)),
    Command("test", "Run the frontend Vitest suite once.", "npm", ("test",),
            "Runs the web test script in non-watch mode. ARGS can select a test file or pass Vitest options.",
            ("make test", 'make test ARGS="--reporter verbose"')),
    Command("test-python", "Run Python tests with unittest discovery.", "python", ("-m", "unittest", "discover", "-s", "tests", "-v"),
            "Uses .venv/bin/python if installed, otherwise the catalog interpreter. ARGS adds unittest "
            "discovery options, such as -p to select a filename pattern.",
            ("make test-python", """make test-python ARGS='-p "test_api*.py"'""")),
)


def show_help() -> None:
    print("Usage: make TARGET [ARGS='arguments'] [ORCH_ARGS='global options']")
    print("\n  help              List all commands with one-sentence descriptions.")
    for command in COMMANDS:
        print(f"  {command.name:<18}{command.summary}")
    print("\nDetailed help: make TARGET help")


def show_command(command: Command) -> None:
    print(f"\n{command.name}: {command.summary}\n  {command.details}")
    if command.backend == "orch":
        print(f"  Wraps: orch {' '.join(command.argv)}")
        print("  Global options: ORCH_ARGS='--config config.toml --log-level DEBUG'.")
        print(f"  Native option help: make {command.name} ARGS='--help'.")
    elif command.backend == "npm":
        print(f"  Wraps: npm {' '.join(command.argv)} (in web/).")
    for example in command.examples:
        print(f"  Example: {example}")


def run(command: Command) -> int:
    args = shlex.split(os.environ.get("ARGS", ""))
    cwd = ROOT
    if command.backend == "orch":
        local = ROOT / ".venv/bin/orch"
        executable = os.environ.get("ORCH") or (str(local) if local.exists() else "orch")
        argv = [executable, *shlex.split(os.environ.get("ORCH_ARGS", "")), *command.argv, *args]
    elif command.backend == "npm":
        cwd = ROOT / "web"
        argv = [os.environ.get("NPM") or "npm", *command.argv]
        if args:
            argv.extend(["--", *args])
    else:
        local = ROOT / ".venv/bin/python"
        if command.backend == "install":
            if not local.exists():
                subprocess.run([sys.executable, "-m", "venv", str(ROOT / ".venv")], cwd=ROOT, check=True)
            argv = [str(local), "-m", "pip", "install", "-e", ".", *args]
        else:
            argv = [str(local) if local.exists() else sys.executable, *command.argv, *args]
    return subprocess.call(argv, cwd=cwd)


def main() -> int:
    target = sys.argv[1] if len(sys.argv) > 1 else "help"
    if target == "--targets":
        print(" ".join(command.name for command in COMMANDS))
    elif target == "help":
        show_help()
    else:
        command = next((item for item in COMMANDS if item.name == target), None)
        if command is None:
            raise ValueError(f"Unknown command: {target}")
        if "--help-mode" in sys.argv[2:]:
            show_command(command)
        else:
            return run(command)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
