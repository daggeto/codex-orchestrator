# Feedback Loop

Feedback Loop captures feedback, runs a read-only analyst, supports clarification
and editable briefs, and starts isolated implementation after explicit approval.
New submissions start an analysis task. Approve and implement freezes the latest
reviewed brief and displayed Orch destination, then starts a planner followed by
the default small-change implementation/review/validation flow.

Implementation starts from the registered Orch Main workspace with isolation
required. An execution guard verifies the task has a separate worktree in the
approved repository before planning. Approved prompts use the same surrounding
whitespace trimming as task submission, including for previously saved requests.
Internal prompt content remains exact; guard failures identify the specific
approval, prompt or isolation mismatch. The planner receives the screenshot, HTML,
public conversation context and approved brief. The default source review,
revision limits, scope escalation and validation restrictions remain in effect.
Scope proposals may explicitly launch separate ordinary workflows from the task
page; they are separate decisions and do not silently replace the approved task.

All implementation roles receive instructions to preserve the live service,
checkouts, ports, runtime data and global configuration. No code path automatically
merges, publishes, deploys or restarts the running app. The worktree starts from
committed Main HEAD; unrelated uncommitted source edits are not copied into it.

Feedback shows implementation phase, task/recovery link, worktree link and final
summary. Successful and cancelled tasks move feedback to Completed. Failed or
interrupted tasks remain Active: use the existing task page recovery controls to
continue the same task. An approval whose task acceptance was interrupted can
explicitly retry the saved submission with the original identity. Approval and
retries never create another task for the same saved handoff. Cancellation waits
for the existing task cancellation path before marking the feedback cancelled.

Migration 15 stores the approved handoff, immutable implementation request and
completion time. Pending implementation records reconcile from persisted task
status on list/detail reads, including after restart. Approval target resolution
is refreshed on approval and stale target identities are rejected.

The feedback-analyst role uses gpt-6.1-sol with medium reasoning. It produces a
title, TLDR, original requirement, frustration, evidence, uncertainties, one
category with rationale, and an implementation brief. Missing essential context
returns up to five questions. Answering or requesting a revision starts another
read-only task, preserving earlier revisions. Direct edits change the category,
rationale and implementation brief. Cancelling stops analysis and moves feedback
to Completed. Failed/interrupted analysis stays Active with explicit retry.
Analysis failures do not start automatic repair agents.

The analyst receives the screenshot as a native image input plus HTML and full
saved public context in managed files under data_dir/feedback-evidence. Its
prompt contains a bounded context excerpt and the complete revision history.
These files persist with the feedback and never enter the target checkout.

Set feedback_repository_path in the Orch config to the Orch source repository
when running an installed package. A Git source installation discovers its
checkout automatically. The service resolves its registered Main workspace;
other projects supply context only. Analysis uses a read-only role and an
ordinary workspace lease. The current role must be loaded by the running service.

Migration 14 adds revision history, structured analysis, action request identities,
and error state. Actions require revision and request_id, and reject stale edits.
A repeated request identity recovers the previous action. Unfinished task state
is reconciled on list/detail reads; interruptions require explicit retry.

Use the floating Feedback button on any page. Enter the situation and the
improvements found or wanted. The page is captured before the modal opens.
Submission includes a PNG of the app viewport, an inert HTML snapshot, route,
timestamp, viewport dimensions, and available theme/revision metadata. Browser
native content and embedded external content may differ from the visible page.
Capture must succeed before submission. Password input values are excluded.

The server resolves the page's task, repository, workspace, agents, sessions,
messages, and saved events. On an agent page with task context, events are scoped
to that task and agent. Private recovery assessment events remain redacted.
Revision metadata is null when the deployment supplies no app-revision meta tag.

Evidence is stored in the application SQLite database, independent of chat
attachment settings. HTML is download-only, with nosniff and sandbox headers.
Database migration 13 adds feedback and evidence tables on service startup.
There is no automatic deletion or retention policy in this checkpoint.

POST /api/feedback accepts situation, improvements, idempotency_key, capture,
HTML text, and base64 PNG bytes. Both text fields are required (20,000 characters
each). PNG and HTML limits are 20 MiB and 5 MiB respectively; the JSON request
limit is 34 MiB. Reusing a submission key with changed content returns conflict.
GET /api/feedback?tab=active|completed lists the newest 200 matching submissions.
GET /api/feedback/{id} returns saved context, analysis and history.
POST /api/feedback/{id}/actions accepts edit, answer, revise, retry, approve,
retry_implementation, or cancel. Approval also requires target_workspace_id. Evidence downloads are under
/api/feedback/{id}/evidence/screenshot and /evidence/html.

## Manual acceptance

After installing frontend dependencies with `make web-install` and starting the
updated application with `make dev`, check the following:

1. Open the widget from repository, workspace, task, and agent conversation pages.
   Confirm keyboard focus stays in the modal and returns to the button on close.
2. Confirm both fields are required and the preview shows the underlying page,
   including its scroll position, without the feedback widget or modal.
3. Submit feedback and inspect its captured context and downloaded evidence.
   Check that agent context belongs to the selected task and private recovery
   evidence is absent. Try a long chat and both light and dark themes.
4. Refresh the detail page and reopen Feedback Loop from the sidebar. Confirm
   the submission persists and appears in Active. Completed is initially empty.
5. Simulate a capture failure and a lost submission response. Confirm text is
   retained and retry does not create another feedback entry. Repeat with a
   service restart after the submission was accepted.

6. Confirm the analyst sees the screenshot and saved context, and returns a title,
   TLDR, category, rationale, insights and implementation brief. Check clarification
   questions, answers, direct edits, requested revisions and saved history.
7. Open two tabs, edit one revision and try the stale action in the other. Confirm
   it conflicts. Retry the same action after a lost response and confirm no extra
   analysis task appears. Cancel during analysis and confirm it stops and appears
   in Completed. Restart during analysis and confirm explicit retry is available.
8. Review the destination path, then approve one small feedback task. Confirm
   the planner runs before implementation in a new Orch worktree. Verify the
   original checkout, running app, live data and unrelated work are untouched.
9. Retry approval after a lost response and attempt approval from a stale tab.
   Confirm exactly one implementation task exists. Restart during task acceptance
   or execution and verify the retained task or explicit saved-submission retry.
10. Confirm progress and worktree links. Exercise cancellation, interrupted/failed
    recovery, and scope escalation from the task page. Failures stay Active;
    success and cancellation move to Completed. Review the final result without
    automatic merging, publication or restart.

No tests, typechecks, builds or browser validation were run during implementation.
The complete feedback flow still requires manual acceptance.
