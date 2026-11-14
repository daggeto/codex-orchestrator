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
            "accepts durable tasks asynchronously, and starts a task-owned team. "
            "Browser disconnects leave accepted work running; service shutdown interrupts it. "
            "Workspace details can merge a captured worktree commit into the checkout holding local main. "
            "For Orchestrator worktrees, Start test instance launches checkout source on separate frontend/API ports with fresh temporary data. "
            "Install that worktree's frontend dependencies first. Open test app opens its URL; Stop test instance or server shutdown stops its processes. "
            "Test instances select a Vite-compatible Node from PATH or installed nvm versions; ORCH_NODE_BINARY supplies an explicit candidate. "
            "Temporary data/logs remain for inspection; a later start creates fresh data. Stop the instance before removing its worktree. "
            "Local edits do not block Merge to main and remain uncommitted; Git rejects overwrites. The optional Commit and merge action generates messages using the configured commit_model "
            "(gpt-6-luna by default), commits captured tracked/untracked changes in both checkouts, then merges locally. "
            "Conflicts get up to three progressing recovery attempts within ten minutes (conflict-resolver uses gpt-6-luna, low effort by default); "
            "missing capabilities can be installed in an isolated incident directory within that same budget. Task and merge pages retain attempt reports. "
            "The service verifies the edits and creates the merge commit. Unresolved conflicts are aborted safely; "
            "interrupted or unexpected edits retain recovery reservations. No automatic model escalation occurs. "
            "Merge operations persist across reloads, reserve both checkouts against tasks/deletion, "
            "and restore active status after verified completion; uncertain recovery remains blocked. No remote publication occurs. "
            "Codex backends require lsof and reserve ports across processes using shared per-user locks.",
            ("make start",
             'make start ARGS="--cwd /path/to/repo --roles-dir ./roles --spawn-team architect"')),
    Command("stop", "Stop this checkout's API and web services.", "python", ("scripts/stop_services.py",),
            "Finds listeners on API port 8420, Vite dev port 5173, and preview port 4173. "
            "Verifies each process command and working directory belong to this checkout before sending Ctrl+C (SIGINT). "
            "Works after a service terminal closes; stopping the API interrupts active tasks and shuts down its agents. "
            "Waits up to 30 seconds for processes to exit, without force killing. Already stopped services are a no-op. "
            "Requires lsof and ps. Use --api-port, --web-port, or --preview-port for custom ports, "
            "and --timeout for a different shutdown wait in seconds. Services using --no-api cannot be discovered by port.",
            ("make stop", 'make stop ARGS="--api-port 8421 --web-port 5174"')),
    Command("task", "Submit a task to the running service without prompts.", "orch", ("task",),
            "Requires a quoted DESCRIPTION inside ARGS. Connects to the service started by make start; "
            "starts task-owned workflow agents, waits for the result, and prints the run ID. "
            "Uses the service's project directory unless --cwd is supplied. --workflow selects a workflow "
            "(small-change by default); --api-url selects another service port. Repeat --var/-v KEY=VALUE for inputs. "
            "small-change uses implementer and reviewer at medium effort, combined review, and up to 10 source revision rounds. The reviewer explains unresolved findings in the saved stop report. "
            "Small tasks have one clear outcome, straightforward acceptance criteria, a localized change using existing patterns and contracts, "
            "and a comfortably reviewable diff. Above five implementation files, explain why it remains small; file count alone does not stop work. "
            "Migrations, new background services or recovery systems, substantial contract or architectural changes, "
            "or dependencies or design decisions that break those conditions require escalation. "
            "Scope escalation or unresolved review stops with a saved report for an explicit follow-up task; it never publishes. "
            "Existing worktrees are reused without switching branches; Main tasks create one when isolation is needed. New task branches and checkout folders use task words plus a unique suffix; continuation retains their identity. "
            "All workflow roles share the resolved checkout and a workspace lease. "
            "Continuing a stopped workflow retains task-owned agents and the workspace lease, and updates the task when execution ends. "
            "Saved runs predating reviewer-only approval retain their workspace, implementation, plans, and budget; continuation obtains fresh reviewer approval before completion or publication. "
            "Continuing final validation authorizes fresh QA and up to three repair rounds with source review; repeated failures stop early. QA may request scoped execution approval and retry a sandbox-restricted command once per pass without consuming a repair round, subject to task/repository restrictions and approval decisions. Attempt- and commit-bound evidence is retained. Task pages show unresolved environment blockers, stopped repairs, and recovery actions. "
            "Before each QA pass the implementer prepares declared dependencies and source resolution in checkout-local ignored environments, preserving source and existing edits, and supplies QA tool paths. Dependency/import-path-only blockers permit one setup repair and fresh QA per cycle without a source commit; external or persistent blockers stop with recovery guidance. Installation and check restrictions still apply. "
            "Continuation atomically queues its unarchived task, preventing archive until execution ends. "
            "Legacy workflow runs without an owning task must restart as workspace tasks. "
            "Checkouts reserved for a local-main merge or merge recovery reject task execution. "
            "Feature includes UX and technical planning followed by mandatory developer plan approval in the web task page. "
            "Plans show short section summaries with expandable details; approval passes the complete saved plan to implementers. "
            "The readable plan remains available if preparation fails; feedback can return it to planning. "
            "Planning accepts existing uncommitted edits and binds approval to checkout contents. Implementation handoff, source review, and specialist consultation bind to the task commit, preserve unrelated working edits, and inspect committed content; final QA runs on the selected checkout including local edits, and publication pushes only approved commits. Existing local edits do not block workflow stages. "
            "Changed checkouts receive a bounded workflow-assistant assessment before approval: compatible plans continue, while required technical replanning returns to developer approval. Eligible failures receive up to three actions within ten minutes: diagnosis, assigned source repair with fresh reviewer approval, isolated capability installation, or a read-only retry. Timed-out Codex turns wait up to 30 seconds for interruption to finish before the agent is stopped. Late checkout changes trigger one fresh recovery assessment within the original time/action budget; continued edits stop recovery. Uncertain side effects require explicit action. Existing blocked tasks receive serial startup recovery, and dashboard Retry recovery starts a new idempotent cycle without extending workflow budgets or bypassing approval. "
            "Waiting tasks remain visible in the sidebar. Revisions return directly to code review; "
            "material decisions can request specialist consultation, and substantial UI work can receive a final advisory UX report. "
            "Source inspection leaves visual/usability validation outstanding. "
            "Feature tasks default to create_mr=false (local edits and commits); -v create_mr=true enables "
            "draft PR/MR publication after current code approval, plus CI verification. Final UX findings are advisory follow-up work. pr-completion includes publication. "
            "Ctrl+C closes the client while submitted work remains running. Failed tasks return a nonzero exit status. "
            "This command does not start or stop an orchestrator service.",
            ("""make task ARGS='"Add search to the transactions page"'""",
             """make task ARGS='"Investigate slow imports" --workflow research --api-url http://127.0.0.1:8421'""",
             """make task ARGS='"Add OAuth login" --workflow feature -v create_mr=true -v mr_base=main'""")),
    Command("roles", "List available role definitions and models.", "orch", ("roles",),
            "Reads role definitions from the configured roles directory and project roles directory.",
            ("make roles",)),
    Command("status", "Show saved agents, threads, and work-item counts.", "orch", ("status",),
            "Reads persisted status and prints the data directory. Saved running status is not a process health check.",
            ("make status",)),
    Command("team", "Spawn a team using the supplied role names.", "orch", ("team",),
            "Pass space-separated roles, or omit them to use configured defaults. This CLI invocation stops "
            "its orchestrator on completion; use start --spawn-team for a long-running team. "
            "Workflows discover their teams automatically: ui-small uses UX designer, implementer, reviewer and QA; backend-feature uses architect, implementer, reviewer and QA.",
            ('make team ARGS="senior-ux-designer architect implementer reviewer"',)),
    Command("send", "Send a message to an agent by role.", "orch", ("send",),
            "Requires ROLE MESSAGE. Starts a saved non-task agent for the role on demand inside a leased task in a Git checkout; unavailable workspaces reject execution. Interrupted turns can continue from their task page using the saved session. Task-owned agents use task messaging. Quote the message inside ARGS to preserve it as one argument. "
            "Use --cwd to set the agent working directory. The implementer must use a separate task worktree "
            "unless the message explicitly requests main-branch implementation.",
            ("""make send ARGS='architect "Design an auth system" --cwd /path/to/repo'""",)),
    Command("supervised-send", "Send a role message with deliverable supervision.", "orch", ("supervised-send",),
            "Requires ROLE MESSAGE and accepts --cwd. Starts a saved non-task role agent on demand inside a leased task in a Git checkout. Reports accepted output or supervision issues, "
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
            "Requires AGENT_ID MESSAGE. Starts the selected saved non-task agent on demand inside a leased task in a Git checkout. Records task participation and retains continuation settings after interruption. Use --thread/-t to resume a thread and --cwd to set its working directory.",
            ("""make agent-send ARGS='design-agent "Continue the design" --thread THREAD_ID --cwd /path/to/repo'""",)),
    Command("workflow-list", "List workflows and their step counts.", "orch", ("workflow", "list"),
            "Built-ins are small-change, ui-small, backend-feature, feature, bugfix, review, research, pr-completion, and feedback-analysis (read-only feedback assessment), and feedback-implementation (approved feedback in an isolated worktree).",
            ("make workflow-list",)),
    Command("workflow-run", "Run a named workflow with variables and a target directory.", "orch", ("workflow", "run"),
            "Requires NAME. Repeat --var/-v KEY=VALUE for workflow inputs. --cwd sets the target repository; "
            "--roles-dir selects role definitions independently. Required roles are started within the owning task. "
            "Built-in workflow roles have no elapsed-time turn limit; max_turn_seconds=0 disables it, while a positive role value opts into a hard timeout. "
            "Cancellation and workflow step limits still apply. feedback-analysis is managed by Feedback Loop; it categorises feedback and returns clarification questions or an editable brief without implementing changes. "
            "feedback-implementation adds a read-only planner to small-change after explicit Feedback Loop approval, requires an isolated Orch worktree, and retains changes for review without publication or live-service restart. "
            "Its approval guard ignores surrounding prompt whitespace and reports the specific approval or isolation mismatch. "
            "small-change implements directly, combines correctness and usability review, permits up to 10 source revisions, "
            "and generates its summary without another model call. It never publishes. small-change and ui-small "
            "require one clear outcome, straightforward acceptance criteria, a localized change using existing patterns and contracts, "
            "and a comfortably reviewable diff. Above five implementation files, explain why it remains small and continue if those conditions hold. "
            "Migrations, new background services or recovery systems, substantial contract or architectural changes, "
            "or dependencies or design decisions that break those conditions require escalation. Scope escalation or "
            "unresolved findings after 10 source revisions stop with the reviewer's explanations at a saved failed checkpoint. Scope reports propose standalone prompts, context, "
            "required inputs and recommended workflows for retained and separate tasks. The task page can start each "
            "proposal explicitly in a new worktree from repository Main, with copied attachments and publication disabled, "
            "and shows a durable task link. Retries recover the same task. Old unstructured reports need manual submission. "
            "Continuing that checkpoint does not authorize additional implementation. "
            "pr-completion starts implementer and reviewer, publishes committed changes, "
            "and monitors/repairs CI. feature runs UX planning, technical planning, developer plan approval in the web task page, then implementation and code review. "
            "The plan is shown before checkout validation, including recovery of saved planning output from a failed first approval entry. "
            "Section TL;DRs keep review compact, with Read more controls and the full saved document preserved for implementation. "
            "Feedback can return to planning even when checkout validation fails. "
            "Existing uncommitted edits do not block planning; approval detects checkout content changes. Recovered plans without an initial snapshot "
            "capture the current checkout on explicit approval and verify it again before implementation. "
            "In every implementation flow the reviewer owns source approval and checks the request against all supplied UX and architecture criteria. "
            "Revisions, including source revisions after QA repairs, return directly to the reviewer until approval, subject to revision and step limits. "
            "Before final QA, the implementer prepares checkout-local dependencies and correct Python imports, supplies tool paths, and preserves source and existing edits. Dependency/import-path-only QA blockers permit one setup repair and fresh check pass per cycle without a new source commit; external or persistent blockers stop with recovery guidance. Installation and testing restrictions still apply. "
            "Unclear decisions stop without requesting implementation; unchanged rejected commits stop with the implementer report. "
            "ui-small uses a brief UX plan and reviewer approval; backend-feature uses architecture planning and reviewer approval; bugfix uses investigation and reviewer approval. "
            "Only material decisions trigger a scoped UX or architecture consultation. "
            "Substantial UI work can receive one final advisory UX report: violations remain unresolved follow-up work "
            "without another revision cycle. Completion compiles saved reports without another architect assignment. "
            "Source inspection does not establish visual/usability validation. Optional feature publication "
            "requires code approval for the current commit, not UX approval. Incomplete agent turns cannot enter review. Implementation handoff and source review use the committed revision while preserving unrelated working edits; reviewers must inspect Git objects and reject requested changes missing from the commit. Final QA discloses local edits in its evidence; publication pushes approved commits while preserving those edits. small-change, feature, bugfix, and pr-completion "
            "reuse a selected worktree or create one from Main with task words and a unique suffix in its branch and folder name, shared by implementation, review, QA, and publication. "
            "Review uses the selected publication target even when the task branch differs. "
            "Existing PR/MR source and target branches are resolved before isolation and preserved when publishing from the task checkout. "
            "Worktrees and session history survive shutdown. Web deletion verifies a private archive before removal; "
            "failed captures retain the checkout. After checkout removal, managed attachments for all tasks owned by that worktree are permanently purged; purge failures remain retryable and history cannot restore files. Completing or archiving a task retains attachments; restarting independently copies available files. Codex backends require lsof and verify listener ownership before connecting. "
            "In the web monitor, Continue workflow retries the unfinished step using saved outputs and threads with the current workflow definition when the saved step exists or has a supported review migration; older review gates migrate to fresh reviewer approval while retaining completed implementation, and continuation authorizes fresh final validation; Restart run starts over. After checkout removal, Restart offers the repository's active Main workspace with normal task isolation and requires explicit omission of unavailable attachments. At its step budget the run pauses; Continue workflow can explicitly approve 20 more steps, including for older limit-failed runs with checkpoints. Continuation may publish when publication was enabled in the original inputs. "
            "See docs/pr-completion.md for its inputs.",
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
            "supervision is enabled by default. Starts a saved non-task role agent on demand inside a leased task in a Git checkout.",
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
    Command("dev", "Start repository navigation, task conversations, and the monitor.", "npm", ("run", "dev"),
            "Runs Vite in web/ with compact task navigation, a reversible task Archive, and task-scoped participant history. "
            "Tasks awaiting input remain visible beneath collapsed workspace groups with their review or input status. "
            "Worktree removal remains a separate confirmed operation. The toolbar can start, stop, or restart this checkout's backend on port 8420 while Vite stays running. "
            "Controls require a local same-origin connection, python3, lsof, ps, and an installed .venv. Stop and restart interrupt active tasks. "
            "Backend launches write to .backend.log; stop/start retains launch arguments while Vite stays running, and restart preserves the current CLI arguments. "
            "Launches use this checkout's .venv/bin/python and include its src directory on PYTHONPATH; restart checks backend imports with that same interpreter before stopping the current process. "
            "The development server proxies /api and /health to that API. Task home defaults to small-change "
            "with local commits and no publication. Scope stops show suggested task splits with standalone prompts, recommended workflows, "
            "required inputs, explicit start buttons and persistent links to accepted tasks. "
            "Before QA, the implementer prepares checkout-local dependencies and Python source resolution and hands off tool paths. Dependency/import-path-only blockers permit one setup repair per validation cycle without a source commit; installation restrictions remain in force. "
            "ui-small adds a brief UX plan, reviewer-owned approval, optional advisory UX reporting, and up to 10 source revision rounds; backend-feature omits UX stages and discloses UI edits. Implementation workflows finish with applicable QA checks and up to three validation repairs per accepted cycle. QA may request scoped execution approval and retry a sandbox-restricted command once per pass, subject to task/repository restrictions and approval decisions. Failed checks reach the implementer even alongside environment blockers; repeated failures or unresolved blockers stop with task-page recovery guidance. Publication is opt-in for feature/backend-feature and "
            "required for pr-completion. The monitor remains read-only.",
            ("make dev", 'make dev ARGS="--port 5174"')),
    Command("preview", "Serve the production web build locally.", "npm", ("run", "preview"),
            "Runs Vite preview in web/. Run make build first. Local toolbar controls can start, stop, or restart this checkout's API on port 8420, interrupting active tasks on stop/restart; launches write to .backend.log.",
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
