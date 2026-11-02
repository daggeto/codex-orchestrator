# Developer plan approval

New `feature` tasks stop after UX and architecture planning. Open the task page
and review the saved Markdown plan alongside the original request. Edit and save
it, discuss it with the architect, or send feedback to UX or Architect. Sending
to UX reruns UX and architecture; sending to Architect reruns architecture. Both
return to approval. **Approve and implement** authorizes the saved revision and
queues implementation. Other built-in workflows retain their existing behavior.

The reading view shows a short TL;DR for each section, with **Read more** to expand
its implementation details. UX and architecture planning provide explicit summaries
while retaining the complete plan. Older plans and developer edits without TL;DRs
show a short excerpt instead. **Read full plan** opens the original saved Markdown;
editing, approval, and the implementer handoff always use that complete document.
Collapsed details are included in approval. Summaries reset when the revision changes.

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
and lease. Planning snapshots include existing local edits without committing or
discarding them. Checkout changes trigger a read-only workflow-assistant assessment
under the task lease. Compatible plans can continue a requested approval after a
second checkout check. Technical replanning returns to the Architect and then
requires approval of the new revision. Ambiguous changes retain the plan and show
specific next steps. Older gates that failed before saving their first snapshot
can approve the recovered plan: acceptance captures the current selected checkout,
and execution verifies it again under the workspace lease before implementation.
Tasks awaiting input stay visible beneath their workspace even when its sidebar
group is collapsed. Generic Continue
cannot skip approval. Startup may assess a blocked approval and refresh planning,
but does not approve implementation or replay interrupted mutations. Other
interrupted accepted operations use explicit workflow recovery. Old unfinished feature
runs require restart under the new policy. Completed historical records stay intact.

## Manual acceptance (not executed during implementation)

1. Submit a feature. Confirm UX and Architect finish and implementation does not
   start. Reload the page and restart the service; the same revision remains pending.
2. Review headings, tables, lists, and code on desktop/mobile in both themes.
   Navigate controls with the keyboard. HTML and unsafe links remain restricted.
   Confirm sections start collapsed with brief summaries; Read more and Read less
   toggle each section independently. Read full plan must include all saved text.
   Check an older plan without TL;DRs and a plan without headings: excerpts and
   expandable details should remain available. After saving a revision, confirm
   the summary view refreshes and approval still passes all collapsed details.
3. Edit and preview the draft. Save it, then discuss a change. Confirm discussion
   cannot approve, and the original request and revision history remain available.
4. Send feedback to UX, then separately to Architect. Confirm the expected agents
   rerun and each path returns to approval with a new revision.
5. Open two tabs. Save in one and submit an old revision in the other. Confirm the
   stale action is rejected and its local draft is retained. Retry a failed request.
6. Approve the saved plan. Confirm implementation starts once with the approved
   text, and review receives that text. Confirm Continue cannot bypass the gate.
7. While waiting, change the checkout. Confirm approval invokes the assistant and
   task history shows its assessment. Compatible changes may continue approval;
   technical plan changes return to planning and fresh approval. Change files during
   assessment and confirm implementation does not start. Exercise merge reservations, cancellation during chat,
   malformed author responses, and service interruption during queued actions.
8. Confirm backend-feature and ui-small do not acquire an approval gate.

Automated regression cases are in `tests/test_plan_approval.py`. They have not been
run. Tests, typecheck, build, and browser acceptance require a separate explicit
request under repository instructions. Install updated web dependencies before
running the frontend, including the Markdown table plugin.
