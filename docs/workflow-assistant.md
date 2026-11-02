# Workflow assistant implementation checkpoint

The assistant assesses checkout compatibility and failed workflow steps, including
nested steps. Recovery incidents and individual attempts survive reloads through
schema migrations 10 through 12 and appear in task and workspace merge detail.

Checkout evidence retains HEAD, status, a tracked patch up to 256 KiB, and untracked
content hashes. Omitted patches and legacy snapshots require source assessment;
the assistant must not claim an exact historical diff. It may approve compatibility,
request technical replanning, or explain the required user decision. Services retain
routing authority. A requested approval continues only for the same assessed files.

General failures have a shared ten-minute budget and at most three actions:
assessment followed by an eligible repair, isolated capability installation, or
read-only retry. An unchanged incident never silently receives a fresh budget.
Source repairs require a clean selected checkout, explicit allowed files, task
ownership, and a reviewer route. Existing writers stop first; the assistant edits
files and the service verifies, stages, and commits only assigned changes. The
workflow invalidates stale approvals/checks and resumes at fresh source review.
Prior review and validation evidence is retained. Applied repairs remain labelled
review pending until the matching commit receives reviewer approval.
When existing committed work only needs fresh approval, the assistant can resume
review directly without editing files. Publication still verifies its configured
source identity, reviewed commit, and validation evidence.

Read-only retries must leave the checkout unchanged. Custom steps may explicitly
declare `recovery_retry_safe = True`; arbitrary service steps, publication, and Git
mutations are not replayed automatically. Technical replanning returns to developer
approval. Scope/behavior decisions, denied permissions, uncertain effects, dirty
checkouts, and unsupported repair routes produce specific instructions instead.
Final QA keeps its existing repair limits and explicit continuation policy; the
assistant cannot grant a new validation pass or extend workflow step budgets.
Missing read-only role agents can be restored within the owning task before a
retry, retaining saved conversation identities. Unrelated agents are left alone.
Built-in preparation steps opt into retry only in an already resolved task
workspace. Installed command directories are activated through task-local execution
context, without editing the process-wide environment or shell configuration.

For an authorized merge, the existing conflict resolver can make up to three
progressing attempts within ten minutes. Ownership and index checks remain in the
merge service; only that service stages and commits. No-progress attempts stop
early. Every attempt retains its report and agent/thread identity. Recovered means
the service verified the integrated commit, not that an agent claimed success.

Agents receive applicable application skill instructions, respecting role filters,
and may use installed backend skills. If the resolver emits a structured missing
capability request, the next attempt installs it in an incident directory beneath
the application's data directory. Installation consumes the same attempt/time
budget. The installer must report actual source/version and verifiable local paths;
it cannot modify global configuration, either checkout, or sandbox permissions.
Denied installs produce instructions instead of weakening restrictions. Installed
capability paths are supplied to the next resolver attempt.

Startup serially assesses eligible existing blocked tasks through normal task
ownership and workspace leases. Ordinary approval waits, cancelled/archived tasks,
live owners, merge reservations, and exhausted step budgets are excluded. A stale
approval error can trigger compatibility assessment or replanning, but startup
assessment itself never approves implementation. Eligible general failures receive
the same bounded action handling. A retained repair commit can resume source review
after its identity is verified, without repeating the repair. Interrupted merge
effects are first reconciled by the merge service. Uncertain interrupted actions
retain their evidence and require assessment instead of blind replay.

Task detail includes the latest 20 incidents; older incidents are available from
`GET /api/tasks/{task_id}/recoveries?before={incident_id}&limit=20`. Merge status and
eligibility include recovery history for the operation. Raw checkout patches remain
local evidence and are omitted from dashboard responses. Older history is also
available through the dashboard's Older recovery history control.

Preparation failures are recorded even before a workflow record exists. If no
workspace lease can be obtained, record the ownership/setup blocker without
starting an agent in the unavailable workspace. Retry recovery re-enters the normal
task preparation path; it does not bypass workspace reservations or create a
replacement task.

Retry recovery explicitly authorizes a new bounded cycle. Task retry acceptance
atomically records the request identity and queues the task against archive, live
ownership, and workspace lease checks. Requests survive reload and duplicates do
not schedule another execution. Normal plan waits remain approval waits. Merge
retries bind to the same captured commits; changed branches need ordinary Merge
controls to present their new snapshot. Unfinished retained merges need their
reported recovery actions before a new operation can be accepted.

The retry APIs are `POST /api/tasks/{task_id}/recoveries/{incident_id}/retry` and
`POST /api/workspaces/{workspace_id}/recoveries/{incident_id}/retry`, with
`{"request_id":"unique-request-identity"}`. Acceptance is HTTP 202, not proof
of repair. Inspect task/merge status and persisted attempt outcomes for completion.

No tests or browser checks were run during implementation. Manual acceptance:

1. After active runs stop, use `make stop` and `make start` to load the new role
   and apply migrations 10/11/12.
2. Prepare a feature plan, modify an unrelated file, then approve. Confirm a
   compatible assessment resumes approval and shows Recovered in task history.
3. Change an API or contract used by the plan. Confirm replanning returns a new
   revision for approval and preserves the saved developer draft as feedback.
4. Change files during assessment. Confirm approval stops and asks for a retry.
5. Cause a repairable source failure on a clean checkout. Confirm assigned files
   alone are committed, stale approvals are invalidated, and the workflow returns
   to source review. Check that dirty files or unapproved plans prevent mutation.
6. Cancel during assessment. Confirm the assistant stops, the incident is retained,
   and implementation does not start. Reload to inspect saved history.
7. Merge branches with a resolvable conflict. Confirm individual attempts appear
   on the workspace, only conflict paths change, and Recovered names the verified
   merge commit. Try ambiguous and no-progress cases; confirm bounded termination.
8. Exercise a missing-capability request and permission denial. Confirm installation
   uses only the incident directory and its source/version/paths appear in the report.
9. Restart with a blocked approval and a failed workflow. Confirm serial assessment,
   preserved approval gates, and no automatic retry of publication or Git mutations.
10. Retry an unresolved incident from two tabs and after a lost response. Confirm
    each accepted request runs once, archived/cancelled/busy tasks reject new cycles,
    and existing work and conversations remain intact. Check older-history paging.
11. Fail before workflow creation, then resolve the reported prerequisite and retry.
    Confirm preparation resumes on the same task without losing its selected workspace.
12. Interrupt after a source repair commit. Confirm recovery verifies that commit
    and resumes review rather than creating another repair commit.

Automated checks remain pending explicit authorization. Required regression cases
include migrations from schemas 9, 10 and 11, legacy hash-only gates, incompatible
decisions, invalid JSON, timeout, cancellation, duplicate incidents, nested step
failures, merge progress and ownership checks, isolated installation, and startup
selection/ownership handling, assigned-path source repairs, reviewer routing,
pre-workflow failures, retry idempotency and cancellation, and history pagination.
