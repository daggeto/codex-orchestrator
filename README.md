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

**Roles** define what each agent does. Architect reads the codebase and designs plans (read-only sandbox). Implementer writes code in the task workspace (workspace-write sandbox). Reviewer checks the work (read-only, must end with APPROVED or CHANGES_REQUESTED). QA runs tests. Planner decomposes tasks. All configured via TOML files.

**Supervision** is not a separate agent; it's how the orchestrator talks. Every response gets checked for deliverables vs noise. If an implementer says "I'll get right on that!" instead of producing file changes, the orchestrator sends a direct correction and retries. Reviewers that don't produce a clear decision get bounced back.

**Workspace isolation** via persistent Git workspaces. Tasks started in an existing worktree reuse its checkout and branch. Main-folder tasks create an isolated checkout when needed. All agents in a task use the same workspace; workflows and conversational turns hold a workspace write lease. Deletion requires a verified archive first.

## Install

Codex backends require `lsof` on PATH to verify listener ownership. Ports use
shared per-user locks in `~/.codex-orchestrator/ports`, including when services
use different projects or data directories. Occupied ports are skipped; startup
and reconnects require listener owners to be the spawned backend or verified
descendants in its process group. This supports npm launchers that spawn the
native backend as a child while keeping unrelated listeners excluded.

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
task-owned workflow agents. It defaults to the feature
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
| senior-ux-designer | codex | read-only | Assesses source before planning and reviews implementation UX |
| reviewer | codex | read-only | Code review; must produce APPROVED or CHANGES_REQUESTED |
| qa | codex | workspace-write | Runs tests, verifies fixes, checks regressions |
| planner | claude | read-only | Decomposes tasks into work items with priorities |

Edit `roles/*.toml` to change models, sandbox, token budgets, system prompts.

## Workflows

Five built-in pipelines:

- **feature**: prepare task branch → source UX assessment → architecture plan → implementation → UX review → code review → summary → optional draft PR/MR and CI/deployment verification
- **bugfix**: architect investigates, implementer fixes, qa verifies
- **review**: send code to reviewer
- **research**: parallel investigation by multiple agents, then synthesis
- **pr-completion**: review committed branch changes, push to an existing/new
  PR/MR, poll CI, repair failures, review and push again until green

Use `pr-completion` for finishing committed work or repairing an existing PR's
CI. It automatically starts task-owned implementer and reviewer agents, polls through
provider CLIs without model turns, and supports resuming an interrupted run.
See [usage, examples and manual validation](docs/pr-completion.md).

```bash
orch workflow run pr-completion --cwd /path/to/repo \
  --roles-dir /path/to/codex-orchestrator/roles \
  -v task_description="Fix the preview build and get CI green" \
  -v required_checks=Vercel
```

Feature tasks automatically include `senior-ux-designer`. Before planning, this
read-only role traces the affected task through routes, components, forms, state,
and API responses. It records existing patterns, practical recommendations, and
UX acceptance criteria in `ux_assessment`, which the architect and implementer
receive. The architect incorporates applicable recommendations and explains tradeoffs.

After implementation, UX review checks the actual source diff before code review.
Either reviewer can request revisions; every revision repeats UX review and code
review. Concrete usability failures block approval, while cosmetic preferences
remain advisory. Backend-only features receive a brief applicability assessment
without invented UI work. Both reviews use the selected publication target, or
the task baseline when publication is disabled.

UX findings cite files and lines, affected tasks, user impact, proposed fixes, and
acceptance criteria. `ux_review_text`, `ux_review_decision` (`approved`,
`changes_requested`, or `unclear`), and `ux_review_commit` remain separate from
code-review fields. A missing or conflicting final UX decision cannot approve.
The summary distinguishes source findings from outstanding browser and user
validation; source inspection cannot establish visual quality or usability.
The existing step budget and pause behavior remain in place. Older feature runs
with a different workflow signature must restart under the checkpoint rules.

The feature workflow creates a draft GitHub PR (`gh`) or GitLab MR (`glab`) after
both UX and code approval for the current commit when Git confirms committed changes. Install and authenticate the matching
CLI (`gh auth login` or `glab auth login`) and make sure Git can push to `origin`.
The workflow first looks for an open PR/MR on the current branch and reuses its
source and target branches. Otherwise it discovers the provider's default branch
through `gh`/`glab` repository metadata, or accepts an explicit target:

```bash
orch workflow run feature -v task_description="Improve transaction linking" --cwd /path/to/repo -v mr_base=main
```

Start with a clean checkout. Existing PR/MR branches and feature branches with
committed work are reused. Otherwise the workflow creates `orch/feature-<run_id>`
before implementation and leaves it checked out for inspection. Both reviewers
inspect the entire proposed PR/MR diff, including existing branch commits.
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

# Inspect local-main merge eligibility and the latest persisted outcome
curl http://127.0.0.1:8420/api/workspaces/{id}/merge

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

## Repositories, tasks, and agent conversations

Start the service from the target repository, then the web app from this checkout:

```sh
make start ARGS='--cwd /absolute/path/to/repository'
# In another terminal:
make web-install
make dev
```

Open the loopback URL printed by Vite, usually `http://127.0.0.1:5173`.
Development and preview servers proxy `/api` and `/health` to port 8420.
The navigation is repository → Main/worktree → task → agent. Hash routes support
reload and back navigation. The service monitor is available at `#/monitor`.

The interface uses Horizon UI Free's rounded cards and purple accents, adapted
within the existing app. **Light mode** / **Dark mode** in the top bar changes
appearance. Dark is the default. The browser remembers the choice under
`orchestrator.ui.theme.v1`; invalid or unavailable storage falls back to dark.
On narrow screens, **Open navigation** opens the repository tree. Close it with
**Close navigation**, Escape, or by selecting a destination. Horizon attribution
and its MIT license are included in `web/public/third-party-notices.txt`.

Manual acceptance checklist for the appearance refresh:

- On desktop and a narrow mobile viewport, open repositories, Main/worktrees,
  tasks, agents, and `#/monitor`. Check active navigation, direct URL reload,
  browser Back, and long paths. Open mobile navigation with the keyboard, check
  that Tab stays inside it, and use Escape to return focus to its trigger.
- Switch both themes and reload. Check forms, errors, status labels, and code
  readability. In browser storage tools, try an invalid theme value and confirm
  that reload returns to dark.
- Register a Git repository using the folder picker. Check root selection,
  parent/subfolder navigation, and an unavailable or disallowed folder error.
- In a disposable repository, submit a workflow and an agent conversation.
  Check checkout selection, optional/required publication summaries, role/model/
  effort/recipe settings, and disabled controls during submission. Switch
  workspaces and reload to check separate drafts. Page through task history;
  check archive success and error feedback only on a disposable worktree.
- Continue an agent conversation, answer a pending approval/input request, and
  use completion/cancellation controls. Leave an execution result or action
  detail expanded across polling updates. Check that long code lines scroll
  within their block and raw HTML or unsafe links remain inert.
- In the monitor, select each resource type and a branch diff. Check event
  following and gap warnings. With no valuable tasks running, interrupt and
  restore the API/stream: connection and stale labels must be explicit, stale
  continuation/restart controls disabled, and recovery must retain data and
  request identifiers. Review the continuation/restart explanations before
  exercising either action on a disposable run.

Implementation mapping and validation gates are in [docs/ui-refresh.md](docs/ui-refresh.md).
Source review, automated tests/typecheck/build, manual acceptance, and provider
CI are separate gates. The appearance implementation has not run automated or
browser acceptance checks; the checklist above is for the reviewer to execute.

If repository navigation or the folder picker reports a missing repository API,
the service on port 8420 may still be running older code. After active tasks finish,
stop it with Ctrl+C in its terminal and run `make start` from the current checkout
with the same project and configuration options. Service shutdown stops its agents.
Reload the web page after restarting.

Choose **Add repository** to browse Git folders on the service machine. This
registers the repository and discovers its existing worktrees without creating
one. By default browsing is restricted to the service user's home and the target
project's parent. Set `browse_roots = ["/absolute/projects"]` in the service TOML
to choose different roots. Canonical paths outside those roots are rejected.
Browser mutations require a loopback Host and a trusted Origin. Configure
`browser_origins` for a different local frontend port; remote authentication is
outside this feature's scope.

Opening a repository shows a fresh task composer with Main selected. Opening a
worktree shows its composer and paginated task history. Each workspace keeps its
own browser draft. A worktree is created only when execution requires isolation;
selecting an existing worktree always reuses it, without switching branches.
Main-folder execution can be selected explicitly. All workflow roles share the
resolved checkout. Workspace leases serialize workflows and conversational turns
in the same checkout. Different workspaces can execute concurrently.

Choose Workflow or Agent conversation. Conversations support a role, model,
Codex effort, and a recipe from the existing skill registry. A skill may include
optional `settings` with `model` and `effort`. The task snapshots the effective
configuration, including the recipe prompt. Defaults apply first, then role,
recipe, and explicit choices. Selecting a recipe does not start a workflow.
Roles loaded by the service are never modified by task settings.

Task pages show status and phase, the participating agents, workflow attempts,
parallel parents and dependencies, and pending user input. Agent pages show
persisted messages and expandable action output. Markdown uses react-markdown
with raw HTML disabled, and Shiki renders code tokens as React elements with
on-demand grammars. Conversation records come from SQLite, including cursor
recovery after disconnected previews. Browser storage contains drafts only.

Agent conversations stay open between turns. Continue on the task page after a
reload or service restart, then choose **Mark complete** when finished. An active
turn must finish or be cancelled before completion. Completed tasks reject new
messages. Navigation and browser disconnects leave accepted work running. Service
startup reclaims execution only when its recorded process owner has stopped,
then marks it interrupted and expires its input requests. Live owners retain their
workspace leases. A dead service owner is insufficient to reclaim a lease while
its backend process group is alive or shutdown cannot be verified. Pre-upgrade leases without ownership evidence are retained for
manual inspection. Startup never replays commands. Saved backend sessions resume only after a new message.
Backend session retention and installed CLI credentials are still required.
Legacy `make send`, `make agent-send`, `make supervised-send`, and `make skill-run`
start the selected saved agent on demand inside a tracked task with a workspace
lease. These commands require a Git checkout and reject unavailable workspaces.
Task-owned agents use task messaging. Legacy task pages retain their agent and
thread participation, show only that task’s transcript, and resume interrupted
turns through the saved direct or supervised adapter. Work-item assignments use
the actual saved turn outcome; failed turns leave the conversation open for retry.
An agent owned by another live service must receive messages through that service.

Feature tasks default to local edits and commits. **Create draft PR/MR** enables
publication and CI/deployment monitoring. `pr-completion` requires publication.
Publication resolves the original source branch and any existing PR/MR target
before creating an isolated checkout, and preserves that request for publication.
`make task` and `make workflow-run` use the same task lifecycle as the browser.
The legacy project and workflow endpoints remain adapters; new integrations use:

- `GET/POST /api/repositories` and `GET /api/filesystem/directories`
- `GET /api/repositories/{id}/workspaces`
- `GET /api/workspaces/{id}/tasks?before=...&limit=50`
- `POST /api/tasks` with `workspace_id`, `prompt`, `kind`, `settings`,
  `isolation` (`auto`, `always`, `never`), and optional `idempotency_key`
- `GET /api/tasks/{id}`, `POST /api/tasks/{id}/messages`, `/complete`, `/cancel`
- `POST /api/input-requests/{id}/resolve` with `response`
- `GET /api/agents/{id}/events?after=0&limit=200`
- `DELETE /api/workspaces/{id}` and `GET /api/workspaces/{id}/archive`
- `GET /api/workspaces/{id}/merge` for local-main eligibility and latest operation
- `POST /api/workspaces/{id}/merge` to accept an idempotent local merge
- `GET /api/workspaces/{id}/merges/{operation_id}` for its persisted outcome

Submission returns 202 with a task ID and status URL. Retrying the same key and
payload returns the same task; changed payloads conflict. Missing resources return
404, invalid input returns 400, and lifecycle conflicts return 409. Directory and
checkout paths are resolved by the service rather than trusted from task settings.
Older records are attached only where saved checkout and ownership evidence exists;
ambiguous runs and globally reused agents remain accessible through the legacy APIs.
Schema migrations are additive and versioned; back up the SQLite database before
upgrading an existing installation.

**Archive and delete worktree** blocks new work, cancels its execution, stops owned
agents, including legacy sessions whose thread directory belongs to the checkout,
and awaits shutdown of backend and command process groups. A live execution in
another service blocks deletion. It flushes history and captures tracked, untracked, and ignored files, a Git
bundle, task settings, workflow steps, messages, and actions. Archives are stored
outside the checkout in `<data_dir>/archives/<archive_id>/` with private permissions.
Symlinks are preserved as links. File hashes, bundle validity, the saved manifest,
and checkout stability are verified before Git removes the checkout. Database
history and branches are retained. Main folders cannot be deleted this way.

A failed stage retains the checkout and reports the failure in workspace history.
Retry after resolving it. After its recorded owner stops, an incomplete operation
is marked interrupted; retry can reconcile an already removed checkout against its saved
verified archive. Submodules, Git LFS, and special filesystem entries currently
block removal because complete capture is unsupported. Staged changes also block
removal because the archive does not preserve index-only content; commit or unstage
them before retrying. Archive restoration UI is
outside scope. Restore manually into a fresh folder using `history.bundle` and the
checkout files; never use an old worktree `.git` pointer as a restored Git repository.
Archives may contain ignored secrets and should be handled as private backups.

Implementation and validation are separate. The task ownership and archive tests
are in `tests/test_task_ownership.py` and `tests/test_archives.py`; they do not launch
agent backends. When validation is requested, run the appropriate gates once:

```sh
make test-python
make typecheck
make build
make test
```

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

Manual acceptance still requires the real installed backends: migrate an existing
store and restart twice; start two tasks in one worktree and check serialization;
continue an agent conversation after reload/restart; inspect workflow retries and
parallel steps; answer input once and reject a duplicate; disconnect/reconnect and
check persisted output; archive a dirty worktree containing ignored files and an
external symlink. Simulate capture failure and confirm the checkout remains.
Verify that completed and archived history stays readable and that Main deletion
and mutations from an untrusted browser Origin are rejected.

Manual acceptance for the UX workflow: run a UI feature and confirm the assessment
reaches planning and implementation, then run a backend-only feature and confirm
the applicability assessment stays scoped. Exercise a UX rejection and a code
rejection separately; each revision must repeat both reviews. After approvals,
create another commit and confirm publication blocks stale approvals. Check that
the summary lists outstanding visual/usability validation.


### Merge a worktree into local main

Workspace details offers **Merge to main** beside the displayed source branch,
commit, and destination checkout path. The destination is the existing checkout
holding `refs/heads/main`, even when that is a linked worktree rather than the
primary folder. Commit or remove dirty tracked/untracked files in both checkouts
first. Missing main, detached or changed branches, unfinished Git operations,
submodules, active execution, archive operations, and unresolved merges block
acceptance. Ignored files are protected against overwriting by Git.

This action merges the captured source commit with a fast-forward or normal merge
commit. It does not fetch, push, switch branches, squash, rebase, archive, or delete.
The existing diff/log views still compare against the recorded base; they are not
a preview against main. Both workspaces are temporarily reserved as `merging`.
Verified completion restores `active`, so the worktree can accept tasks and merge
new commits later. Outcomes are separate persisted records, not lifecycle statuses.

The eligibility GET returns `eligible`, visible `blockers`, `source_workspace_id`,
`source_branch`, `source_sha`, `destination_workspace_id`, `destination_path`,
`destination_sha`, and `latest_operation`. POST requires exactly these six string
fields (the five captured identities plus a caller-generated operation ID):

```json
{
  "operation_id": "unique-client-operation-id",
  "source_branch": "feature/example",
  "source_sha": "full-source-commit-from-eligibility",
  "destination_workspace_id": "destination-id-from-eligibility",
  "destination_path": "/actual/main/checkout",
  "destination_sha": "full-main-commit-from-eligibility"
}
```

POST returns HTTP 202 with `operation` and `status_url`; acceptance is not success.
Reuse the same ID and exact request after a lost response. Different input for an
existing ID returns 409. Invalid requests return 400, missing resources 404, and
stale or unsafe requests 409 with an explanation. Status GET returns HTTP 200 for
an existing operation, including failed attempts. Inspect `operation.state`:
`accepted`, `running`, `merged`, `already_integrated`, `failed`, or
`recovery_required`. Success includes the destination path and `resulting_sha`;
failures include an error, bounded diagnostics, and captured conflict paths.

The browser saves the request per workspace before submitting and polls its saved
operation after reload. A lost response offers **Check saved merge attempt** before
retrying; it reuses that operation ID. A terminal failed attempt needs a new ID.
Accepted work belongs to the application and continues after a browser disconnect.
Service shutdown stops its managed Git process group and retains uncertain state.
Migration 5 adds merge records and indexes without replacing task/archive history.

A conflicting merge is aborted only when its MERGE_HEAD and original HEAD match
this operation. Cleanup must be verified before releasing reservations. Timeouts,
interruption, or uncertain checkout state retain `merge_recovery_required` on both
workspaces. Startup and merge/status reads reconcile dead owners and verify the
original HEAD, exact fast-forward result, or two-parent merge result before
releasing them. A live or unverifiable orphan Git process group stays blocked.
The child waits for its persisted process identity before executing Git, so a crash
before recording that identity cannot start a merge.

For recovery, inspect the reported destination with `git status`, `git log`, and
`git reflog`. Stop any remaining merge process before editing Git state. Resolve or
abort an unfinished merge manually after checking its ownership, then reload to
reconcile. An unexpected HEAD or checkout identity remains blocked for operator
inspection; do not clear reservations just to retry. No destructive reset is run.
External Git tools do not honor orchestrator reservations, so avoid concurrent
manual Git changes in either checkout during a merge.

Manual acceptance (not performed during implementation):

1. In a disposable repository, merge a clean branch that can fast-forward main.
   Check the displayed destination/SHA, unchanged source branch/files, restored
   `active` status, and ability to submit another task. Add another commit and merge
   again; the new attempt must capture the new source SHA.
2. Create independent, nonconflicting commits on main and a worktree. Merge and
   inspect the resulting two-parent commit. Merge again and expect “Already
   integrated into local main” with no extra commit.
3. Create conflicting committed edits. Check visible conflict paths and diagnostics;
   verify main returns to its original clean HEAD without MERGE_HEAD. Resolve on
   the source branch, commit, and retry with a new attempt.
4. Add tracked, staged, and untracked changes to each checkout in turn. Verify
   specific blockers and preservation of files. Check that an ignored file which
   would be overwritten makes Git fail safely. Exercise an unfinished merge or
   rebase, detached source, missing main checkout, and changed source/destination
   SHAs between eligibility and POST; all must block safely.
5. Check out main in a linked worktree while the primary checkout holds another
   branch. Verify the displayed and actual destination is that linked checkout.
   Register a newly created source worktree and merge without restarting.
6. Race merge requests, task starts/continuations, and archive requests against both
   checkouts, including requests through two services sharing the same database.
   Only one unresolved repository merge may exist; reserved checkouts reject
   execution and deletion while unrelated workspaces remain usable. Repeat an ID
   with identical input and then different input; expect the same operation and
   a 409 respectively.
7. Disconnect after POST and reload during execution. Verify the saved ID is queried
   and no duplicate merge occurs. Stop/restart the service around acceptance,
   execution, and completion. Check verified outcomes or explicit recovery blockers;
   no hidden unfinished merge may be reported as success. After manually aborting
   an interrupted owned merge, reload and check reconciliation releases reservations.
8. Use the keyboard to reach and activate the button. Check visible focus, announced
   progress/results/errors, destination wrapping at narrow widths, and disabled
   merge/archive controls while busy. Switch workspaces during submission and
   polling; no previous workspace result or error may appear in the new page.
