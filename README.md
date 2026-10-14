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

## Local web monitor

The read-only React monitor shows agents and their current activity, tasks,
active and recent workflow runs, live SSE events, and committed branch diffs.
It observes the orchestrator instance serving `http://127.0.0.1:8420`.

Restart an existing API instance once to load the new observation fields and
read-only endpoints. Start the API from your target project's directory so
role discovery and agent context match that project. If no instance is running,
use `orch start` (or `./.venv/bin/orch start` from this checkout).

In a separate terminal, from this checkout:

```sh
cd web
npm ci
npm run dev
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
  monitor's view. The UI adds no authentication or execution controls.

Manual acceptance (requires a running instance with agents): open the monitor
and check the connection status; send an agent work through the existing CLI or
API and check idle/running transitions and live events; select a task and its
workspace and inspect the branch diff; select an existing workflow run and
reload to check persisted history. Stop the API and check the disconnected/stale
state, then restart it and check automatic recovery and the log-gap notice.
These browser checks are separate from the automated tests and build.
