# Developer plan approval

New `feature` tasks stop after UX and architecture planning. Open the task page
and review the saved Markdown plan alongside the original request. Edit and save
it, discuss it with the architect, or send feedback to UX or Architect. Sending
to UX reruns UX and architecture; sending to Architect reruns architecture. Both
return to approval. **Approve and implement** authorizes the saved revision and
queues implementation. Other built-in workflows retain their existing behavior.

Each approval uses the dedicated `PlanApprovalStep` kind. Workflow definitions
configure the author role, contributing output keys, earlier return targets, and
approved next step. Targets must precede the gate. Keep planning agents read-only.
The feature architect consolidates the plan; original UX criteria remain visible
as supporting planning evidence. The approved document supersedes historical
planning drafts in implementation and source review.

Approval state, append-only revisions, discussion, action identities, and checkout
snapshot live in the existing durable workflow checkpoint JSON. No database schema
migration is needed. This uses the same atomic checkpoint persistence as workflow
execution rather than introducing another persistence authority. The task-detail
API adds `plan_approval`; actions use
`POST /api/tasks/{task_id}/plan-approval/actions` with `action`, `approval_id`,
`revision`, and `idempotency_key`. Save adds `markdown`; chat adds `message`;
send_back adds `message` and a configured `target`. Approve adds no content.

An idle approval has no timeout, running agents, or workspace lease. Restarting the
service preserves it. Chat and approval reacquire the normal task workspace lock
and lease. Checkout changes block approval until planning is refreshed; restore
the selected branch and a clean checkout before refreshing. Generic Continue
cannot skip approval. Interrupted accepted operations use explicit workflow
recovery; they are never automatically replayed on startup. Old unfinished feature
runs require restart under the new policy. Completed historical records stay intact.

## Manual acceptance (not executed during implementation)

1. Submit a feature. Confirm UX and Architect finish and implementation does not
   start. Reload the page and restart the service; the same revision remains pending.
2. Review headings, tables, lists, and code on desktop/mobile in both themes.
   Navigate controls with the keyboard. HTML and unsafe links remain restricted.
3. Edit and preview the draft. Save it, then discuss a change. Confirm discussion
   cannot approve, and the original request and revision history remain available.
4. Send feedback to UX, then separately to Architect. Confirm the expected agents
   rerun and each path returns to approval with a new revision.
5. Open two tabs. Save in one and submit an old revision in the other. Confirm the
   stale action is rejected and its local draft is retained. Retry a failed request.
6. Approve the saved plan. Confirm implementation starts once with the approved
   text, and review receives that text. Confirm Continue cannot bypass the gate.
7. While waiting, use another task in the workspace. Confirm checkout changes block
   approval; refresh planning. Exercise merge reservations, cancellation during chat,
   malformed author responses, and service interruption during queued actions.
8. Confirm backend-feature and ui-small do not acquire an approval gate.

Automated regression cases are in `tests/test_plan_approval.py`. They have not been
run. Tests, typecheck, build, and browser acceptance require a separate explicit
request under repository instructions. Install updated web dependencies before
running the frontend, including the Markdown table plugin.
