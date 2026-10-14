# Complete a PR/MR and get CI green

`pr-completion` starts from committed work on the current branch. It reviews the
whole proposed diff, pushes approved commits to an existing open GitHub PR or
GitLab MR, or creates a draft if none exists. Provider CLI queries poll CI without
model turns. Failed CI invokes the implementer to inspect logs and commit a focused
repair, then the reviewer approves it before another push.

The CLI starts or reuses the implementer and reviewer automatically. Authenticate
`gh` or `glab` first and use a clean checkout. Keep the intended branch checked out.
`--roles-dir` selects role definitions independently of the target repository.
The workflow preserves dirty files and stops rather than overwriting them.

## Example: fix the Finances preview

Suppose your current Finances branch contains the committed reimbursement type
repair, and its PR has a failing Vercel check. Run from the Finances repository:

```bash
cd /Users/arturingelevic/Projects/finances
orch workflow run pr-completion \
  --cwd . \
  --roles-dir /Users/arturingelevic/Projects/codex-orchestrator/roles \
  -v task_description="Fix the reimbursement preview build and get CI green" \
  -v required_checks=Vercel
```

`Vercel` must be the exact check/context name reported by your provider. If it is
named differently, pass that name. For GitHub, inspect names without modifying the
PR using `gh pr view --json statusCheckRollup`. Required checks can be comma-separated,
for example `-v required_checks="build,test,Vercel"`.

The workflow discovers the open PR on the current branch, reviews its full diff,
and pushes the approved local commits there. If Vercel is still running, it polls
every 30 seconds. If Vercel fails on a second TypeScript error, the implementer
reads the failed deployment logs and commits a repair. The reviewer reviews the
updated code, the workflow pushes it to the same branch, and polling starts again
for the new SHA. Success requires green checks for that SHA, confirmed by a second
provider query. The workflow does not merge the PR or deploy to production.

If the branch has no open PR, the same command creates a draft after review and
monitors it. It discovers the provider's default branch without `origin/HEAD`;
`-v mr_base=main` can explicitly select a target when creating a request. When an
open request exists, an explicit target must agree with its existing target.

This workflow does not implement a new feature from scratch. First commit the
feature or repair, then run `pr-completion`. It is also useful after the existing
`feature` workflow reports a publication or CI blocker.

## Interruption and resume

The CLI prints a full run ID when the workflow starts. Press Ctrl+C to interrupt
waiting or an agent turn. The run saves its last phase, PR URL, approved commit,
CI evidence and repair counters. Interrupted file edits remain in the checkout;
resolve and commit them before resuming.

```bash
orch workflow run pr-completion \
  --cwd . \
  --roles-dir /Users/arturingelevic/Projects/codex-orchestrator/roles \
  -v resume_run=YOUR_SAVED_RUN_ID
```

Resume creates a new run linked by `resumed_from`. It requires the same repository,
branch and origin. Previously approved, unchanged commits can go directly to
publication; changed commits receive a fresh review. Remote results are always
queried again. A closed/replaced PR, another live run owner, or a changed remote
head stops completion rather than silently changing scope. After an external
push, synchronize the branch yourself and resume for review.

Inspect the saved phase, counters and complete CI report without starting agents:

```bash
orch workflow show YOUR_SAVED_RUN_ID
```

## Limits and completion evidence

| Variable | Default | Meaning |
| --- | --- | --- |
| `required_checks` | empty | Exact comma-separated check/job names that must succeed; include the preview check when preview readiness is required |
| `max_ci_repairs` | `3` | Maximum CI repair attempts, preserved on resume |
| `max_review_revisions` | `3` | Maximum review revision attempts, preserved on resume |
| `poll_interval` | `30` | Seconds between queries; minimum 20 |
| `monitor_timeout` | `0` | Seconds of monitoring per publication; zero waits until green, failure or interruption |
| `checks_grace_seconds` | `120` | Registration grace for missing checks before reporting unavailable CI evidence |
| `resume_run` | unset | Saved `pr-completion` run to continue |
| `mr_provider` | inferred | `github` or `gitlab`; set explicitly for a custom host |
| `mr_base` | discovered | Target branch for a new request |

Resume preserves settings unless you explicitly supply replacements. After
reviewing a repair-limit blocker, you can deliberately increase the total budget
with `-v max_ci_repairs=5`; counters do not reset. For a bounded monitoring session,
use `-v monitor_timeout=1800` and resume later if it expires.

Without named required checks, all reported meaningful checks must pass and at
least one must succeed. Optional skipped/neutral jobs do not establish success;
named required checks must actually succeed. GitLab additionally requires the
head pipeline to succeed. A repository with no checks cannot complete as green.
This confirms provider CI results, including any explicitly named preview check;
it does not prove browser behavior or manual product acceptance.

Credentials, missing evidence, infrastructure problems, uncommitted work, changed
heads, repair limits, and unresponsive agents stop with a failure and saved
evidence. The CLI exits nonzero on failure. Repairs must produce a new descendant
commit; no-change repair attempts do not repeatedly rerun the same failed CI.
Agents are told to follow the user's local testing boundaries. Publishing a
commit naturally triggers the repository's remote CI.

## Manual validation before relying on this workflow

Implementation has not been exercised against a live provider. In a disposable
repository with CI, check these paths:

1. A committed branch without an open request creates one draft and ends green.
2. An existing request receives changes on the same branch without a duplicate.
3. A deliberately failing build produces a repair commit, reviewer approval and
   another push; the final SHA matches the successful check evidence.
4. Pending CI can be interrupted with Ctrl+C and resumed using the printed ID.
5. Missing required checks, authentication failure and exhausted repair limits
   exit nonzero and preserve the checkout and CI report.
6. External head changes and dirty files stop publication; optional skipped jobs
   do not substitute for the named required preview check.

Provider interfaces: [GitHub PR JSON fields](https://cli.github.com/manual/gh_pr_view)
and [GitLab merge request pipeline data](https://docs.gitlab.com/api/merge_requests/).
