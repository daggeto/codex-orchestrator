# Codex Orchestrator — Operations Guide

## Quick Start

```bash
cd codex-orchestrator
source .venv/bin/activate

# Start daemon with a team
orch start --spawn-team architect --spawn-team implementer --spawn-team reviewer

# Or spawn individually
orch agent spawn architect
orch agent spawn implementer
```

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│                     HTTP API (:8420)                      │
│  /api/agents  /api/send  /api/skills  /api/hitl  /events │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│                    Orchestrator                           │
│  ┌──────────┐ ┌──────────┐ ┌─────┐ ┌──────┐ ┌────────┐ │
│  │ Registry │ │Supervisor│ │HITL │ │Skills│ │Handoff │ │
│  └────┬─────┘ └──────────┘ └─────┘ └──────┘ └────────┘ │
│       │                                                  │
│  ┌────▼─────────────────────────────────────────┐        │
│  │              Agent Registry                   │        │
│  │  ┌─────────┐  ┌─────────┐  ┌─────────┐      │        │
│  │  │ Codex   │  │ Codex   │  │ Claude  │      │        │
│  │  │ :9001   │  │ :9002   │  │ (proc)  │      │        │
│  │  │architect│  │implement│  │ planner │      │        │
│  │  └─────────┘  └─────────┘  └─────────┘      │        │
│  └──────────────────────────────────────────────┘        │
│                         │                                │
│  ┌─────────┐  ┌────────▼┐  ┌──────────┐                │
│  │EventBus │  │SQLite DB│  │Workflows │                │
│  └─────────┘  └─────────┘  └──────────┘                │
└──────────────────────────────────────────────────────────┘
```

## Roles

| Role | Backend | Model | Purpose |
|------|---------|-------|---------|
| architect | codex | gpt-5.4 | Design, plan, review architecture |
| implementer | codex | gpt-5.1-codex | Write code, run commands |
| reviewer | codex | gpt-5.4 | Code review (read-only) |
| qa | codex | gpt-5.4 | Test execution, verification |
| planner | claude | opus | Task decomposition, strategy |

Roles are defined in `roles/*.toml`. Edit model, sandbox, approval_policy freely.

## Sending Messages

```bash
# Simple send
orch send architect "Design an auth system for this project" --cwd /path/to/repo

# Supervised send (enforces deliverables, rejects noise)
orch supervised-send implementer "Add a /health endpoint" --cwd /path/to/repo
```

### Supervised vs Unsupervised

**Unsupervised** (`orch send`): raw agent response, no quality gate.

**Supervised** (`orch supervised-send`): the supervisor checks for:
- Deliverables (commits, file changes, test results, review decisions)
- Noise ratio (rejects if >50% acknowledgement filler)
- Role-specific requirements (implementer must change files, reviewer must decide)
- Auto-retries with direct correction if agent blabs

## Skills

Skills are reusable prompt templates with verification criteria.

```bash
# List skills
orch skill list
orch skill list --role implementer

# Run a skill
orch skill run code-review reviewer -v diff="$(git diff)" --cwd /path/to/repo
orch skill run investigate-bug architect -v bug_description="Login fails on Safari" --cwd /repo
orch skill run decompose-task planner -v task="Build user dashboard"
```

Add custom skills as TOML files in `skills/`:

```toml
[skill]
name = "my-skill"
description = "What it does"
roles = ["implementer", "architect"]
prompt_template = "Do {thing} in {cwd}..."

[skill.input_schema]
thing = "What to do"
cwd = "Working directory"

output_expectations = ["file changes", "test results"]
verification = ["Tests pass", "Files committed"]
```

## Workflows

Built-in workflows orchestrate multi-agent pipelines:

```bash
# List workflows
orch workflow list

# Run feature pipeline: architect → implementer → reviewer (with revision loop)
orch workflow run feature -v task_description="Add OAuth login" --cwd /path/to/repo

# Bug investigation → fix → QA verification
orch workflow run bugfix -v task_description="Login timeout on slow networks" --cwd /repo

# Parallel research by multiple agents
orch workflow run research -v task_description="Evaluate switching from REST to gRPC"
```

## HTTP API

When running with `orch start`, the API is available at `http://127.0.0.1:8420`.

### Key Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Orchestrator health |
| GET | /api/agents | List all agents |
| POST | /api/agents | Spawn agent `{"role": "architect"}` |
| POST | /api/send/{role} | Send message `{"message": "...", "cwd": "..."}` |
| POST | /api/supervised/{role} | Supervised send |
| GET | /api/skills | List skills |
| POST | /api/skills/{name}/run | Run skill |
| POST | /api/workflows/{name}/run | Run workflow |
| GET | /api/hitl/pending | Pending approvals/escalations |
| POST | /api/hitl/approvals/{id} | Resolve approval |
| GET | /api/events?pattern=* | SSE event stream |

### SSE Events

Connect to `/api/events` for real-time events:

```bash
curl -N http://127.0.0.1:8420/api/events?pattern=*
```

Topics: `agent.*`, `workflow.*`, `supervision.*`, `hitl.*`, `work.*`

## Supervision Protocol

The supervisor enforces productive agent behavior:

1. **Deliverable detection** — extracts commits, test results, file changes, review decisions from responses
2. **Noise detection** — flags acknowledgements, narration, and filler
3. **Role-specific requirements** — implementer must produce code, reviewer must decide
4. **Auto-correction** — if agent blabs, sends direct correction and retries
5. **Verification** — file changes from the server are trusted; text claims are not

### What gets rejected

- "I'll analyze the codebase and..." (narration without action)
- "Understood, I'll proceed with..." (acknowledgement without deliverable)
- Reviewer response without APPROVED/CHANGES_REQUESTED
- Implementer response without file changes

## HITL (Human-in-the-Loop)

### Approval Gates

Workflows can pause for human approval:

```bash
# Check pending
curl http://127.0.0.1:8420/api/hitl/pending

# Approve
curl -X POST http://127.0.0.1:8420/api/hitl/approvals/abc123 \
  -d '{"status": "approved", "response": "looks good"}'

# Reject
curl -X POST http://127.0.0.1:8420/api/hitl/approvals/abc123 \
  -d '{"status": "rejected", "response": "need tests first"}'
```

### Auto-approval Rules

```python
from orchestrator.hitl import AutoApprovalRule

orch.hitl.add_auto_rule(AutoApprovalRule(
    name="low-risk-auto",
    risk_levels=["low"],
    roles=["architect"],  # auto-approve architect's low-risk actions
))
```

## Configuration

### Orchestrator config (`~/.codex-orchestrator/config.toml`)

```toml
data_dir = "~/.codex-orchestrator"
base_port = 9001
max_agents = 20
api_host = "127.0.0.1"
api_port = 8420
health_check_interval = 10.0
default_model = "gpt-5.4"
```

### Role config (`roles/architect.toml`)

```toml
[role]
name = "architect"
backend = "codex"           # or "claude"
model = "gpt-5.4"
sandbox = "read-only"       # read-only | workspace-write | danger-full-access
approval_policy = "on-failure"  # untrusted | on-failure | on-request | never
max_threads = 5
max_turn_seconds = 300
system_prompt = "..."       # appended to the strict role template
tags = ["planning"]

[role.features]
# codex feature flags

[role.config]
# codex config overrides
```

## File Structure

```
codex-orchestrator/
├── src/orchestrator/
│   ├── core.py          # Main orchestrator
│   ├── config.py        # Configuration
│   ├── supervision.py   # Noise detection, deliverable verification
│   ├── hitl.py          # Human-in-the-loop gates
│   ├── skills.py        # Skill registry and execution
│   ├── handoff.py       # Context transfer between agents
│   ├── prompts.py       # Strict role prompt templates
│   ├── agents/
│   │   ├── base.py      # Abstract agent interface
│   │   ├── codex.py     # Codex WebSocket agent
│   │   ├── claude.py    # Claude subprocess agent
│   │   └── registry.py  # Lifecycle, health, ports
│   ├── protocol/
│   │   ├── jsonrpc.py   # JSON-RPC types
│   │   └── events.py    # Stream event types
│   ├── workflows/
│   │   ├── engine.py    # DAG execution
│   │   ├── steps.py     # Step types (Assign, Review, Gate, Parallel)
│   │   └── builtins.py  # Feature, bugfix, review, research workflows
│   ├── state/
│   │   └── store.py     # SQLite persistence
│   ├── events/
│   │   └── bus.py       # Pub/sub event bus
│   └── api/
│       └── server.py    # HTTP + SSE API
├── roles/               # Role TOML configs
├── skills/              # Skill TOML configs
├── workflows/           # Custom workflow configs
└── .venv/               # Python virtual environment
```

## Working with task files

After source approval, enable `attachments_enabled = true` in `config.toml` and
restart the service. The source gate keeps uploads unavailable until cleanup and
shared agent delivery have been reviewed. Install the updated Python dependencies
with `make install` before enabling it.

Use **Attach files** or drop files into the attachment area on the task composer,
agent follow-up composer, or workflow response form. Both paths use the same upload
pipeline. Each row shows its name, size and state. Sending waits until every selected
file is ready. Failed uploads keep the text and successful selections. Retry the
file or remove it; an oversized file needs a smaller replacement. A request can
contain files without text, but workflow answers and approval decisions remain
required. General workflow chat is not an attachment destination.

Ready uploads recover after reload. Browser file selections do not: interrupted
uploads may need **Recover uploaded files**, or removal and reselection from your
device. If the request was accepted before its response was lost, recovery offers
**Open accepted task** using the files' saved ownership. This opens the original
task even when execution settings reset after reload; the files cannot be submitted
again. If file recovery fails, sending and file edits stay blocked until
**Retry file recovery** confirms their state. Otherwise, retry an uncertain
submission without changing its contents. Workflow retries retain the original
submission workspace even after execution moves into an isolated worktree.
Completed selections expire if abandoned for 24 hours.

Task files appear above the participant history, with references beside their
originating messages. Every role resolves the same task context on each turn.
Codex uses native `localImage` blocks and checks model image capability; Claude uses
stream-json user content over stdin and requires a compatible CLI and image-capable
model. [Claude's streaming input documentation](https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode)
describes the image block contract. Actual readability and image inspection with
each configured backend remain a separate QA gate. Generic files are resources for
tools, not a promise of document conversion.

Defaults: 10 files per message, 20 MiB per file, 200 MiB per task. Previewable images
must be still PNG, JPEG or WebP with at most 25 megapixels. Files are private under
`data_dir/attachments`; configure `data_dir` outside all registered Git checkouts.
No attachment is automatically copied into a checkout or archive.

Completing or archiving a task retains its files. Continuing keeps the same task
ownership. Restarting creates independent copies of available files; deleted files
require reselection in a new task, or an explicit choice to omit them. After checkout
removal, Restart uses the repository's active Main workspace with normal task
isolation. The restart form shows the destination and retains the request.
Removing a worktree cancels unfinished tasks, verifies and removes its checkout, then purges
managed files for every task currently owned by that worktree and its unbound drafts.
Deletion failure stays visible and retryable. Restoring a task restores history only.
Agent-created copies, provider transcripts and downloaded files are not managed
attachment content and are outside this deletion guarantee.

QA after source approvals: use `make test-python`, `make test`, `make typecheck`, and
`make build`. Browser acceptance is separate: picker/drop parity, keyboard operation,
focus after removal, screen-reader status announcements, narrow layouts, long names,
large images, upload recovery, deleted-file visibility, and cleanup failure/retry.
Verify actual file reading and image inspection with every configured backend and
role, including reviewers and continued workflows, before enabling uploads for use.
