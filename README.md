# codex-orchestrator

Most multi-agent setups are demos. You spawn three agents, they chat at each other for 45 seconds, produce a wall of "I'll get right on that!" and "Great suggestion!", and nothing actually happens.

This is the opposite. Persistent agents with dedicated processes, strict prompts that reject noise, and workspace isolation so they don't step on each other. Codex instances run as WebSocket servers on their own ports. Claude sessions resume across turns. The orchestrator manages the lifecycle, routes work, and makes sure every response contains an actual deliverable; commits, test results, file changes, or a clear review decision.

```
┌──────────────────────────────────────────────────────────┐
│                     HTTP API (:8420)                      │
│  /api/agents  /api/send  /api/skills  /api/hitl  /events │
└────────────────────────┬─────────────────────────────────┘
                         │
│                    Orchestrator                           │
│  ┌──────────┐ ┌──────────┐ ┌─────┐ ┌──────┐ ┌────────┐ │
│  │ Registry │ │Supervisor│ │HITL │ │Skills│ │Handoff │ │
│       │                                                  │
│  ┌────▼─────────────────────────────────────────┐        │
│  │              Agent Registry                   │        │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐      │        │
│  │  │ Codex   │  │ Codex   │  │ Claude  │      │        │
│  │  │ :9001   │  │ :9002   │  │ (proc)  │      │        │
│  │  │architect│  │implement│  │ planner │      │        │
│  │  └─────────┘  └─────────┘  └─────────┘      │        │
│       │                                                  │
│  ┌────▼────┐  ┌─────────┐  ┌──────────┐                │
│  │EventBus │  │SQLite DB│  │Workflows │                │
└──────────────────────────────────────────────────────────┘
```

## What's actually here

**Agents** run as long-lived processes. Codex gets a `codex app-server --listen ws://127.0.0.1:PORT` per instance. Claude gets subprocess sessions with `--resume` for persistence. Threads carry context across turns; you can send a follow-up message to the same thread hours later.

**Roles** define what each agent does. Architect reads the codebase and designs plans (read-only sandbox). Implementer writes code (workspace-write sandbox, gets its own git worktree). Reviewer checks the work (read-only, must end with APPROVED or CHANGES_REQUESTED). QA runs tests. Planner decomposes tasks. All configured via TOML files.

**Supervision** is not a separate agent; it's how the orchestrator talks. Every response gets checked for deliverables vs noise. If an implementer says "I'll get right on that!" instead of producing file changes, the orchestrator sends a direct correction and retries. Reviewers that don't produce a clear decision get bounced back.

**Workspace isolation** via git worktrees. Write agents get their own branch per work item. Read agents share the main repo. No file conflicts, no race conditions. Merge back when approved, cleanup when rejected.

## Install

Project commands are available through Make:

```bash
make help                  # All commands with one-sentence descriptions
make workflow-run help     # Detailed help for one command, without running it
make install               # Create .venv and install the Python project (Python 3.12+)
make web-install           # Install frontend dependencies
```

Use `ARGS` for command arguments and `ORCH_ARGS` for global CLI options:

```bash
make start ARGS='--spawn-team architect --spawn-team implementer --spawn-team reviewer'
make send ARGS='architect "Design an auth system" --cwd /path/to/repo'
make workflow-run ARGS='feature -v task_description="Add OAuth login" --cwd /path/to/repo'
make status ORCH_ARGS='--config config.toml --log-level DEBUG'
make dev
```

`make start` starts immediately without prompts. Set the target project with
`--cwd`, choose role definitions with `--roles-dir`, and repeat `--spawn-team`
for initial agents. Roles are loaded from the explicit or configured directory
(global roles by default), plus the target project's `roles/` directory.

```bash
make start ARGS='--cwd /path/to/repo --roles-dir ./roles --spawn-team architect --spawn-team implementer --spawn-team reviewer'
```

Keep the service terminal open and submit a task from another terminal:

```bash
make task ARGS='"Add search to the transactions page"'
make task ARGS='"Investigate slow imports" --workflow research'
```

`make task` connects to the running service, uses its target project, and starts
missing workflow roles or reuses existing agents. It defaults to the feature
workflow with local edits and commits. Add `-v create_mr=true -v mr_base=main`
to publish a draft PR/MR and verify CI. Other inputs use `-v KEY=VALUE`;
`--cwd` overrides the target project for that task.

The command prints a run ID and waits for the result. Ctrl+C closes the task
client while submitted work continues; Ctrl+C in `make start` stops the service
and its agents. The API uses port 8420 by default. When starting with
`--api-port 8421`, submit tasks with `--api-url http://127.0.0.1:8421`.
Task submission requires the API to be enabled.

`make send` and `make workflow-run` manage their own orchestrator lifecycle;
use `make task` to submit work to the running service. `make task help` displays
all task options without starting agents or submitting work.

The catalog in `scripts/commands.py` supplies Make targets and help text from one
source. `AGENTS.md` instructs coding agents to update it whenever commands or
their behavior change. Help needs only Make and Python 3; execution uses the local
`.venv` when available. Override executables with `PYTHON`, `ORCH`, or `NPM`.
Arguments use shell-style quoting but do not evaluate shell expressions. Any
command followed by the separate `help` goal only prints documentation.

```bash
git clone https://github.com/zm2231/codex-orchestrator.git
cd codex-orchestrator
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

Needs `codex` CLI for Codex agents and/or `claude` CLI for Claude agents.

## Usage

```bash
# Start with a team
orch start --spawn-team architect --spawn-team implementer --spawn-team reviewer

# Talk to agents by role
orch send architect "Design an auth system for this project" --cwd /path/to/repo

# Supervised mode (rejects filler, enforces deliverables)
orch supervised-send implementer "Fix the N+1 query in user list" --cwd /path/to/repo

# Run a multi-agent workflow
orch workflow run feature -v task_description="Add OAuth login" --cwd /path/to/repo
orch workflow run bugfix -v task_description="Race condition in sessions" --cwd /path/to/repo

# Skills (reusable prompt templates with verification)
orch skill run code-review reviewer -v diff="$(git diff)" --cwd /repo
orch skill run investigate-bug architect -v bug_description="OOM on large uploads" --cwd /repo
```

## Roles

| Role | Backend | Sandbox | What it does |
|------|---------|---------|-------------|
| architect | codex | read-only | Designs, plans, reviews architecture |
| implementer | codex | workspace-write | Writes code, runs commands, commits |
| reviewer | codex | read-only | Code review; must produce APPROVED or CHANGES_REQUESTED |
| qa | codex | workspace-write | Runs tests, verifies fixes, checks regressions |
| planner | claude | read-only | Decomposes tasks into work items with priorities |

Edit `roles/*.toml` to change models, sandbox, token budgets, system prompts.

## Workflows

Five built-in pipelines:

- **feature**: prepare task branch, architect plans, implementer codes, reviewer checks, revision loop if rejected, draft PR/MR, then implementer verifies CI and deployment
- **bugfix**: architect investigates, implementer fixes, qa verifies
- **review**: send code to reviewer
- **research**: parallel investigation by multiple agents, then synthesis
- **pr-completion**: review committed branch changes, push to an existing/new
  PR/MR, poll CI, repair failures, review and push again until green

Use `pr-completion` for finishing committed work or repairing an existing PR's
CI. It automatically starts/reuses implementer and reviewer agents, polls through
provider CLIs without model turns, and supports resuming an interrupted run.
See [usage, examples and manual validation](docs/pr-completion.md).

```bash
orch workflow run pr-completion --cwd /path/to/repo \
  --roles-dir /path/to/codex-orchestrator/roles \
  -v task_description="Fix the preview build and get CI green" \
  -v required_checks=Vercel
```

The feature workflow creates a draft GitHub PR (`gh`) or GitLab MR (`glab`) after
approval when Git confirms committed changes. Install and authenticate the matching
CLI (`gh auth login` or `glab auth login`) and make sure Git can push to `origin`.
The workflow first looks for an open PR/MR on the current branch and reuses its
source and target branches. Otherwise it discovers the provider's default branch
through `gh`/`glab` repository metadata, or accepts an explicit target:

```bash
orch workflow run feature -v task_description="Improve transaction linking" --cwd /path/to/repo -v mr_base=main
```

Start with a clean checkout. Existing PR/MR branches and feature branches with
committed work are reused. Otherwise the workflow creates `orch/feature-<run_id>`
before implementation and leaves it checked out for inspection. The reviewer
inspects the entire proposed PR/MR diff, including existing branch commits.
Approved commits are pushed without force, then the workflow uses the existing
request or creates a draft. No request is created when there is no diff against
the target. Dirty files, branch changes, missing tools, or publication errors
produce `mr_status=blocked` with an `mr_reason` and fail the workflow. Successful
publication returns `mr_url` and Git-derived `mr_files`, including documentation.

After creating or finding the PR/MR, the implementer checks CI and the preview
deployment for its latest commit. The workflow finishes successfully only when
both are confirmed green (`mr_ci_status=green`). Each monitoring turn lasts at
most five minutes, with an additional minute allowed to report. Queued/running
results return `CI_PENDING`, and monitoring continues in another turn until
green or cancellation. Progress is emitted as `workflow.mr.ci` events. Failed
checks, unavailable evidence, or an unresponsive verification agent fail the
workflow with `mr_ci_status=blocked` or `timeout` and an explanation in
`mr_ci_report`, including the PR/MR link and check/deployment links. Missing or
skipped deployment checks do not count as green. This step monitors remote results;
it does not run local tests,
push fixes, rerun jobs, or merge.

Use `-v create_mr=false` to disable publication. For custom GitHub/GitLab hosts,
also pass `-v mr_provider=github` or `-v mr_provider=gitlab`. The existing
`bugfix`, `review`, and `research` workflows do not publish PRs/MRs.

## HTTP API

Runs on `:8420` with `orch start`. REST endpoints for everything, plus SSE event stream at `/api/events`.

```bash
# Spawn an agent
curl -X POST http://127.0.0.1:8420/api/agents -d '{"role": "architect"}'

# Send a message
curl -X POST http://127.0.0.1:8420/api/send/implementer \
  -d '{"message": "Add input validation", "cwd": "/path/to/repo"}'

# Check worktree diffs
curl http://127.0.0.1:8420/api/workspaces/{id}/diff

# Merge approved work
curl -X POST http://127.0.0.1:8420/api/workspaces/{id}/merge -d '{"strategy": "squash"}'

# Stream all events
curl -N http://127.0.0.1:8420/api/events
```

## What it looks like in practice

Ran the orchestrator against a TODO API with intentional bugs. Three agents, three ports, one repo:

1. **Architect** (read-only, port 9001, 64s): analyzed the codebase, found ID reuse after deletion, missing input validation, no auth on DELETE, stub tests with no real coverage
2. **Implementer** (workspace-write, port 9002, 142s): worked in an isolated worktree on branch `orch/8bef2a4f/fix-bugs`, added validation and error handling, wrote 9 tests, committed `f1c44cb`
3. **Reviewer** (read-only, port 9003, 82s): reviewed the diff against the original findings, found 2 HIGH issues the implementer missed (auth still absent on DELETE, non-object JSON payloads crash the validator)

The worktree stays on its branch until merged. The reviewer's feedback would loop back to the implementer in the normal workflow.

## Project structure

```
src/orchestrator/
├── core.py          # Main orchestrator
├── supervision.py   # Noise detection, deliverable verification
├── hitl.py          # Human-in-the-loop approval gates
├── skills.py        # Composable skill templates
├── handoff.py       # Context transfer between agents
├── prompts.py       # Strict role prompt templates
├── workspace.py     # Git worktree isolation
├── agents/          # Codex (WebSocket), Claude (subprocess), registry
├── protocol/        # JSON-RPC types, stream events
├── workflows/       # DAG engine, step types, built-in pipelines
├── state/           # SQLite persistence
├── events/          # Async pub/sub bus
└── api/             # HTTP + SSE server
```

See [GUIDE.md](GUIDE.md) for the full reference.

## Task home and local monitor

The task home starts work on the orchestrator instance serving
`http://127.0.0.1:8420`. A centered chat surface combines the task composer,
project and workflow controls, required team, run status, recovery actions, and
optional agent conversations. Expand the project control to search or register
a server folder; expand workflow options for its description and publication
controls. A publication summary stays visible before submission. Configuration
changes preserve the draft; **Start another task** resets the composer. Hover or
focus a role chip to see its configured model and reasoning effort. Missing
settings are explicitly labeled as backend defaults. Role discovery stays tied
to the service configuration, independently of the selected task directory.

The Monitor navigation opens the existing read-only view of agents and their
activity, tasks, workflow runs, SSE events, and committed branch diffs. Once
opened, it stays mounted across navigation to retain selection and live events.

Restart an existing API instance once to load the new observation fields and
read-only endpoints. Start the API from your target project's directory so
role discovery and agent context match that project. If no instance is running,
use `make start ARGS="--cwd /absolute/path/to/project"` from this checkout.

In a separate terminal, from this checkout:

```sh
make web-install
make dev
```

Open the loopback URL printed by Vite, normally `http://127.0.0.1:5173`.
Node.js 20.19+ or 22.12+ is required. Both development and preview servers bind
to `127.0.0.1` and proxy `/api` and `/health` to port 8420. The npm scripts
use npm's own Node executable to avoid older Node binaries in parent directories
shadowing the selected runtime. Scripts use a POSIX shell (macOS/Linux).

```sh
# Frontend checks, from web/
npm test
npm run typecheck
npm run build
npm run preview

# Python API and observation regression checks, from the repo root
./.venv/bin/python -m unittest discover -s tests -v
```

The project picker searches registered server folders. Enter an absolute path
and choose **Register existing folder**, or explicitly choose **Create folder and
register** to create missing directories (including parents). Validation and
permission errors appear in the picker. Canonical paths and timestamps are
stored in SQLite's independent `projects` table, seeded with the service project
on startup. Missing or inaccessible folders remain listed as unavailable.
Temporary workspace registrations are unchanged.

Feature submissions default to `create_mr=false`, which keeps implementation
and review local. Enable publication explicitly to allow pushing approved
commits and creating a draft PR/MR with CI/deployment verification.
`pr-completion` always requires publication and shows a separate notice: it may
push committed work and repair CI without a second browser confirmation.
Other built-in workflows have no publication step. CLI defaults and synchronous
submission contracts are unchanged.

The home uses `POST /api/workflows/{name}/submit` with a client-generated run ID,
`task_description`, and registered `cwd`. It persists acceptance before returning
202 and starts/reuses exactly `Workflow.required_roles`, including nested steps.
The same ID and normalized payload return the same acceptance; a conflicting
payload returns 409. IDs cannot replace saved runs. `GET
/api/workflows/runs/{run_id}` returns saved progress, results, and errors without
the recent-history limit. The existing `/run` endpoint still waits for results.

The browser saves the most recent submission before sending it. Reloading the
page resumes polling; if acceptance is uncertain, **Retry same submission**
resends the saved ID and payload. Navigation and disconnects do not stop service
work. Storage must be available to initiate a task safely. A service shutdown or
restart marks unfinished web submissions interrupted, retaining saved progress;
it does not automatically replay steps. Inspect progress before starting a new
task. Older runs remain accessible in Monitor or by individual run API lookup.

Stopped runs in Monitor offer **Continue workflow** alongside **Restart run**.
Continue retries the unfinished step in the same run, retaining saved outputs,
branch, workspace, and agent threads. Completed top-level steps are skipped;
an unfinished step (including its nested steps) may repeat partial actions.
Restore the Codex allowance before continuing after a usage limit. Continuation
preserves the original publication policy, so it can push or publish if enabled.
Restart starts a new workflow from the beginning using its original inputs.

The engine checkpoints outputs and the next step together, and saves thread
identity before sending an agent request. Continuation uses
`POST /api/workflows/runs/{run_id}/continue` with a client-generated `request_id`.
Repeating the same request ID returns the original acceptance without executing
again. Concurrent different requests cannot claim the same stopped run. Previous
errors and continuation requests remain in the run's `state.continuations`.
No database migration is required. Checkpoints are saved in the existing state.

Workflows pause when their step budget is reached. In Monitor, choose
**Allow 20 more steps and continue** to approve another block of 20. Each approval
is persisted with the continuation request; retries do not grant extra steps.
Existing limit-failed runs with saved checkpoints can use the same action. API
clients must send `additional_steps: 20` with the continuation request when the
budget is exhausted. Pausing preserves the next step and conversation history.

Continuation rejects changed workflow definitions,
missing workspaces, and changed task branches. Saved Codex threads are resumed;
if unavailable, the step fails visibly rather than dropping conversation history.
Other backends require their saved conversation to remain in the running agent.
Older failed runs may continue at their recorded failed step using saved outputs,
but have no saved agent-thread checkpoint and start a new conversation. Older
interrupted runs and preparation failures without a reliable checkpoint must be
inspected and restarted instead. A step's external side effects and its checkpoint
cannot be committed atomically, so inspect partial changes before retrying.

Manual acceptance: interrupt a feature run during implementation or let an agent
hit its usage limit. Restore allowance, choose Continue workflow, and verify the
same run, task branch, and workspace proceed from implementation without planning
again. Repeat after restarting the service to check saved Codex thread recovery.
Retry a continuation request with the same ID to confirm it does not start another
execution. Check that a completed run cannot continue and Restart still starts over.

The compact layout was source-reviewed against baseline `054b310`, including
stable composer identity, separate task and agent forms, and keyboard-accessible
disclosures and role tooltips. Runtime acceptance remains unverified.

The reusable Chat accepts typed messages, an async transport callback, optional
toolbar and content slots, and input presentation options. It
provides a labeled multiline input, transcript, loading, error, and disabled
states. Agent conversations create an explicit persisted thread first using
`POST /api/agents/{agent_id}/threads`, then reuse `thread_id` and `cwd` on the
existing message endpoint. The browser tab retains thread identity in session
storage; transcripts remain in memory. Replies arrive at turn completion, with
no streaming. A disconnected message may continue running, so inspect Monitor
before sending again if its response is lost. Thread availability after a service
restart depends on the agent backend; automatic conversation replay is not provided.

The monitor uses `GET /health`, `/api/agents`, `/api/work`,
`/api/work/{item_id}`, `/api/workflows/runs`, `/api/workspaces`,
`/api/workspaces/{id}/diff?full=true`, and one `/api/events` SSE connection.
`/api/workflows` continues to list definitions. Run history includes all
unfinished runs and the latest 50 terminal runs with their persisted step state.
Snapshots reconcile every two seconds, after relevant events, and on SSE
reconnection. Only the selected branch diff refreshes, every five seconds.
Failed reads retain previous data with a stale indicator.

Limitations:

- “Branch diff” includes committed branch changes against the base branch only;
  uncommitted and untracked files are excluded.
- Activity is in memory and separate from process health. Agent lifecycle
  statuses and scheduling are unchanged. Text and command events are forwarded
  best-effort through a bounded queue; slow or failing observers cannot block turns.
- SSE has no replay IDs and may drop events. The latest 1,000 events are retained
  in the browser, and interruptions are marked as possible log gaps.
- Workspace registration is in memory. Historical workspaces may be unavailable
  after cleanup or restart. Task relationships use stored IDs and assignment
  history, never role names or path guesses.
- CLI commands that create a separate orchestrator instance are outside this
  monitor's view. The task home adds execution controls to this instance.
  Keep the service and frontend on loopback; they have no authentication.

Manual acceptance (requires a running instance with agents): open the monitor
and check the connection status; send an agent work through the existing CLI or
API and check idle/running transitions and live events; select a task and its
workspace and inspect the branch diff; select an existing workflow run and
reload to check persisted history. Stop the API and check the disconnected/stale
state, then restart it and check automatic recovery and the log-gap notice.
These browser checks are separate from the automated tests and build.

Task-home manual acceptance is pending. The implementer reported 10 passing
focused regression tests during revision; these were run despite the task's
no-tests instruction. Typecheck, build, and browser checks remain unverified.

1. Start the API and UI with the Make commands above. Check workflow changes,
   nested research roles, and model/reasoning tooltips with mouse and keyboard,
   including a role with no configured values. Check narrow-screen layout.
2. Search and register an existing folder, create a missing folder explicitly,
   and try a relative path, file path, and inaccessible folder. Restart the
   service and confirm registrations persist and unavailable folders are labeled.
3. Submit a local feature task in a disposable repository. Confirm 202 acceptance,
   exactly the required team (reused on a second task), current step, final outputs,
   and no PR/MR publication. Double-click submission and resend the same ID/payload;
   check that only one execution exists. Change the payload with that ID and
   confirm 409. Keep CLI `/run` clients synchronous.
4. Navigate to Monitor, return home, and reload during execution. Disconnect the
   client around acceptance and recover with the same submission. Stop/restart
   the service during team startup and during a step; confirm explicit interrupted
   progress. Simulate a team-start failure and check its persisted error. Confirm
   individual lookup still works for a run outside recent history.
5. Inspect feature publication opt-in and the required `pr-completion` notice.
   Exercise publication only in an authorized disposable remote repository.
6. Refresh running agents, send two conversation turns, and confirm the same
   thread and cwd are used. Confirm errors are visible. Check monitor selection,
   events, task details, and branch diffs still work across home navigation.

Review the complete task diff against main in the isolated worktree, not the
original checkout: `git -C .worktrees/task-home diff main HEAD`. Implementation
is committed locally; publication belongs to the external review workflow.
