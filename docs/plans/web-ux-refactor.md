# Conversation-first web UX

This specification is the source-review acceptance baseline for the task archive,
navigation, transcript, and page hierarchy changes. It builds on the existing
routes, theme tokens, native disclosures, task controls, and mobile dialog.
Browser acceptance remains a separate gate; these layouts are annotated wireframes,
not screenshots of a verified implementation.

## Desktop layout

```text
┌─ 248px navigation ───────┬─ task content, flexible width ─────────────────┐
│ O  Orchestrator         │ Repository / Workspace             Theme      │
│ Tasks       Archive     ├────────────────────────────────────────────────┤
│ Repositories           │ A very long task title wraps to readable lines │
│                         │ ◉ Working · Implementing changes     Actions ▾ │
│ ▾ finances              │ [Implementer ◉] [Reviewer] [UX designer]        │
│ ◉ Reconcile long…       │                                                │
│   feature/reconcile     │ Approval needed                                │
│ ? Confirm imports       │ Explain the requested operation                │
│   Main · Waiting for me │ [Approve] [Reject]                             │
│ ✓ Finish report         │                                                │
│   report-worktree       │ You                                            │
│                         │ Original message                               │
│ ▸ orchestrator          │                                                │
│                         │ Implementer                                    │
│ Browse all tasks        │ Readable assistant reply…                      │
│                         │ ▸ Activity · 3 commands, 2 file changes         │
│                         │ Error: build action failed                     │
│                         │                                                │
│ Service monitor         │ [Continue conversation…                  Send] │
│                         │ ▸ Execution details and original request       │
└─────────────────────────┴────────────────────────────────────────────────┘
```

The transcript occupies the largest continuous surface. The task title and
readable execution state are adjacent to participants and required input.
Participant chips select one agent's task-filtered history; separate agent
streams are never interleaved into a synthetic conversation. Role labels identify
who is speaking. The original prompt, IDs, raw events, and workflow step list
remain available through disclosures.

The sidebar has one repository grouping level. Each task has a title, status
symbol with accessible status text, and secondary workspace label. Desktop rows
target 36–40px with two compact lines. Long titles truncate visually in navigation
but expose their full text through accessible names and the task page. The
selected row remains visible when it falls outside the recent preview. Repository
expansion is shared between desktop and mobile and survives polling.

## Mobile layout at 360px

```text
┌──────────────────────────────────┐
│ Navigation          Theme        │
├──────────────────────────────────┤
│ finances / feature/reconcile     │
│ A very long task title wraps     │
│ over several readable lines      │
│ ? Waiting for me       Actions   │
│ [Implementer] [Reviewer] [UX]     │
│                                  │
│ Approval needed                  │
│ Operation and consequence        │
│ [Approve] [Reject]               │
│                                  │
│ Implementer                      │
│ Conversation text wraps here.    │
│ ▸ Activity · 5 actions           │
│ Error: action failed             │
│                                  │
│ [Response text…                 ]│
│ [Send]                          │
│ ▸ Execution details              │
└──────────────────────────────────┘

Navigation opens a modal drawer:
┌──────────────────────────────────┐
│ Close navigation                │
│ Tasks                Archive    │
│ ▾ finances                      │
│ ? Confirm imported categories   │
│   Main · Waiting for me          │
│ ✓ Archived report               │
│   report-worktree · Archived     │
│ Browse all tasks                │
│ Service monitor                 │
└──────────────────────────────────┘
```

Mobile controls have at least 44px touch targets. Participant and action rows wrap
without widening the viewport. Code and raw output scroll inside their own
containers. Opening the drawer moves focus into it; Escape and Close dismiss it,
and focus returns to the navigation trigger. Route changes close the drawer.

## Task browsing and archive

`/tasks` and `/tasks?tab=archive` share a paginated list. Tasks and Archive are
navigation links with an explicit selected state, not local filters over the
navigation preview. Repository filtering applies on the server. An Open tasks
filter exposes queued, running, and waiting tasks regardless of age. Pagination
is based on descending task IDs and only offers an older page when one exists.
Changing a filter resets the cursor.

Archive rows use the same title and workspace context plus an Archived label and
a Restore action. Archive and Restore show pending feedback and block duplicate
submissions. A restore failure keeps the row and explains the failure. Archiving
is available only for completed, failed, and cancelled tasks. An unavailable
archive action explains that the task must first finish or be cancelled.

Task archiving is reversible organization. It changes no execution status,
conversation, agent, lease, or checkout. Worktree removal remains a separate,
explicitly named action on the workspace page. Its confirmation identifies the
workspace, cancellation of unfinished work, and removal of the checkout.
Restoring an archived task never recreates a removed checkout.

## State and component contract

| State | Required presentation and recovery |
| --- | --- |
| Initial loading | A named loading state, distinct from an empty result; no premature “No messages” message. |
| Empty tasks | Explain that there are no matching tasks; keep filters and repository navigation available. |
| Empty Archive | “No archived tasks” with a short explanation of the reversible archive action. |
| Empty conversation | “No recorded messages yet” only after the initial history response succeeds. |
| Background updating | Preserve visible content and drafts; use quiet status feedback without repeated assertive announcements. |
| Stale/failed refresh | Keep the last successful content and show that updates failed; initial failure offers understandable recovery. |
| Working | Neutral activity icon plus “Working” or the meaningful phase; routine activity stays quiet. |
| Waiting for input | Warning styling plus “Waiting for me”; request and controls appear before the transcript. |
| Failed | Danger icon and readable error; failed tools remain visible outside collapsed routine activity. |
| Finished | Compact success or terminal status; archive action becomes available. |
| Action pending | Disable duplicate actions and label progress; keep text and option selections on failure. |
| Expired input | Explain that the request is no longer active and refresh task state; never silently discard a draft. |
| Ownership conflict | Explain that another owner controls the task and preserve the response for recovery. |

Approvals use ordinary decision controls. Supported question payloads use labeled
options and text fields, preserving the existing backend answer envelope.
Task pages own responses and execution controls. Agent pages expose a direct
task link when an input request is pending, without a second messaging API.

## Transcript fidelity and activity

Persisted event IDs deduplicate transport records. Agent, thread, turn, and item
identity reconcile streamed messages with their final content. Multiple completed
messages within one turn remain separate and ordered. With missing item identity,
only unambiguous contiguous deltas merge; uncertain completed messages are kept.

Routine commands, file operations, and protocol events appear in stable native
disclosures with meaningful labels and counts. Raw details remain available.
Disclosure state survives polling. Failures are promoted to visible transcript
entries. New content follows the reader only near the bottom; otherwise a New
messages control allows an explicit jump without moving the current reading
position. Long histories and output remain within the conversation surface.

## Themes and accessibility

Both themes use existing canvas, surface, text, muted, border, and state tokens.
Neutral badges serve routine metadata; warning is reserved for required input,
danger for failures/destructive actions, and success for completed work. Selected
navigation uses an accent surface plus an explicit selected-state attribute.
Color never carries status alone. Focus rings remain visible on every control,
and native disclosure summaries remain keyboard operable. Respect reduced motion
and avoid frequent live-region announcements during event polling.

Review fixtures in both light and dark themes must include a three-line task
title, at least three participants, an approval, a failed command, and an archived
task with Restore. Assess text/background contrast and focus visibility on the
actual rendered surfaces.

## Checkpoint acceptance

1. **History/archive/navigation:** existing tasks survive migration; the first
   history page is populated; more than 20 tasks and older waiting tasks are
   discoverable; archive/restore persists without checkout effects; selected task
   and repository remain identifiable on explicit task-scoped agent routes.
2. **Transcript fidelity/grouping:** two completed replies in one turn survive
   streaming, interleaved tools, pagination, and reload; routine events are
   collapsed; failures remain visible; loaded content survives refresh failures;
   disclosures and reading position survive updates.
3. **Hierarchy/polish:** task context and required action are visible without
   technical disclosures; participants select independent histories; repeated
   submission is blocked; failed submissions preserve drafts; worktree removal
   has a specific consequence confirmation; monitor presentation remains scoped.

Each checkpoint receives code and UX source review. Automated tests, typecheck,
build, browser acceptance, and user acceptance are separate evidence. Manual
acceptance covers 360px and desktop, both themes, keyboard navigation, drawer
focus restoration, screen-reader status feedback, long output, and recognition
of Working, Waiting for me, and Finished.
