# shadcn/ui migration

The existing React/Vite application uses shadcn/ui's Base UI family, `base-nova`,
and neutral light/dark semantic colors. This implements the approved replacement
for the original Horizon request. Routing, Tailwind, npm scripts, server models,
execution policy, and publication contracts are preserved.

## Source and configuration

- Registry revision: `4aaea19d9cff3e0908e12a8c1d504b8c5ec34610`.
- Runtime baselines: `@base-ui/react@1.6.0`, `shadcn@4.23.0`. Supporting packages
  and transitive versions are locked in `web/package-lock.json`.
- `web/components.json` records the Base Nova configuration. TypeScript and Vite
  resolve `@/*` to `src/*`; existing API proxies are retained.
- `components/ui/primitives/*` includes the complete selected registry components
  and their dependencies. `styles/nova.css` contains the complete pinned style.
  Local imports replace registry aliases and `cn`; Lucide replaces every icon
  placeholder. `styles.css` imports `shadcn/tailwind.css` and `tw-animate-css`.
- Split Tailwind imports retain the no-Preflight foundation. Scoped defaults
  supply the native control border/reset assumptions needed by the primitives.
  Application CSS owns layout, density, local overflow, and semantic statuses.
- AppearanceProvider owns theme selection, including the existing storage key,
  dark fallback, pre-render `.dark`/`data-theme`, and cross-tab observations.
  The navigation breakpoint remains 1000px and conversation width remains 960px.
- MIT attribution and adaptations are recorded in
  `web/public/third-party-notices.txt`. The previous Horizon Card is replaced.

## Acceptance mapping

These are implementation locations and source evidence, not QA pass results.
UX numbering corresponds to the approved fourteen criteria. Technical numbering
adds recovery history (15) and authorization (16).

| Technical / UX | Implementation |
| --- | --- |
| 1 / 1 Navigation | `App.tsx` keeps hash routes and `/home`. `navigation.tsx` shares selected-task detail and expansion preferences. `RepositorySidebar.tsx` keeps all server-provided waiting tasks and selected tasks outside the preview. `TaskRow.tsx` uses Base UI render composition for router links. Sidebar stays expanded; mobile navigation uses Sheet. |
| 2 / 2 Presentation | `components.json`, `components/ui/primitives/*`, shared Button/Card/Badge adapters, `styles/tokens.css`, `styles/nova.css`, and `appearance.tsx` replace the old navy/purple palette and Card. Text references use `muted-foreground`; `muted` and `accent` now mean surfaces. Routed pages and retained legacy controls share the foundation. |
| 3 / 3 Destination | `TaskComposer.tsx` retains checkout path, isolation, execution kind, workflow team/settings, role/model/effort/recipe, and optional/required publication consequences. |
| 4 / 4 Composition | Workflow, role, and recipe readiness are independent, with loading/error/Retry. Default agent execution does not depend on workflow metadata. `Chat.tsx` snapshots submitted text/files and draft version; later edits survive acceptance. Settings remain changeable during metadata failure. `TaskComposer.tsx` retains exact uncertain requests, imports legacy submission identities, observes workspace history on reload, and offers explicit reconciliation. |
| 5 / 5 Hierarchy | `TaskPage.tsx` orders context, questions/plan/recovery, participants/conversation, then report/files/details. Pending approval has an adjacent original-request disclosure; the approved saved plan collapses. Task status wording is retained. |
| 6 / 6 Conversation | `AgentConversation.tsx` retains reducer/cursor reconciliation, task scoping, grouped activity, errors, and follow-scroll behavior. `MessageContent.tsx` and `conversation.ts` retain Markdown restrictions, code highlighting, and multiple replies. `TaskReply.tsx` reconciles durable message keys and keeps uncertain payloads separate from editable drafts. |
| 7 / 7 Decisions and reading | `PlanApproval.tsx` keeps the complete saved document visible during editing. `planSections.ts` remains unchanged, including fenced-code/heading/preamble behavior. Base Collapsible sections retain TL;DR/Excerpt previews and full-plan reading. Task/approval identity owns drafts; base revisions are persisted. New revisions preserve edits, expose current content, and require explicit reconciliation. Unsaved changes block approval. Structured question IDs/answer payloads and request-specific drafts remain in `PendingInput.tsx`; disappearance alone never confirms acceptance. |
| 8 / 8 Files | `AttachmentPicker.tsx`, `AttachmentList.tsx`, and `useAttachments.ts` retain native file selection/drop, binary uploads, per-file states, limits, retry/removal, draft recovery, unavailable-file history, and ownership. Storage failures now expose loss of reload durability. Restart still requires explicit omission of unavailable attachments. |
| 9 / 9 Browsing | `taskUrls.ts` centralizes Tasks/Archive links, preserves repository context, and clears cursors/incompatible filters. `TasksPage.tsx` keeps pagination and URL filters, offers Clear filters, blocks stale restore actions, and retains feedback outside removed rows. |
| 10 / 10 Recovery actions | `WorkflowRecovery.tsx` keeps Continue and Restart separate. Continue identities are independent of mutable run timestamps; authoritative continuation records reconcile accepted requests. New stopped checkpoints require explicit new actions. Restart retains complete saved variables, destination, publication notice, and independent attachment copy. `ScopeSuggestions.tsx` retains source/run/suggestion identity and persists answers plus exact uncertain requests. |
| 11 / 11 Workspace safeguards | `useWorkspaceMerge.ts` retains immutable operation outcomes, snapshots, dirty-file checks, reservations, legacy pending requests, and matching-ID cleanup. HTTP errors do not erase uncertain snapshots. `WorkspacePage.tsx` independently refreshes late incident reports after terminal merge outcomes. `RemoveWorkspace.tsx` uses controlled Alert Dialog, initial Cancel focus, explicit path/cancellation/permanent file deletion consequences, a fresh eligibility read, and persistent uncertainty. Only a matching archive ID is attributed to a confirmed removal; DELETE is never automatically repeated. |
| 12 / 12 Accessibility | Sheet uses Base UI initial/final focus contracts and destination focus. Alert Dialog starts on Cancel. Fields retain labels/legends, question choices use Radio Group, and checkboxes use Base UI. Skip navigation, selected states, native scrolling, local overflow, and reduced-motion rules remain. Narrow layout, zoom, contrast, announcements, and actual focus behavior await browser acceptance. |
| 13 / 13 Async feedback | `operationState.ts` provides pure preparation/reconciliation/settlement. `useOperation.ts` persists exact pending requests before sending, imports legacy identities through domain owners, subscribes to observations, and retains compact acceptance receipts separately. Accepted evidence cannot regress. A settles its receipt without replacing B's current attempt. Failed persistence retains memory and exposes lost durability. Generic HTTP 400 is never treated as proof of non-acceptance. Actions without request identities refresh state after uncertainty and do not automatically repeat. |
| 14 / 14 Monitor | `useMonitor.ts` adds `hasSnapshot`; `Monitor.tsx` suppresses zero/empty claims before first success. Existing resource selection, stale snapshots, stream distinctions, event-gap/follow controls, and committed-diff limitations remain. |
| 15 / additional parity | `RecoveryHistory.tsx` preserves summaries, next actions, nested reports, agent links, pagination and every recovery status. Loaded rows survive live refresh, deduplicate by incident ID, and live records take precedence. Pagination is keyed to its owner and aborted on owner change. Retry preserves the same incident-specific ID after uncertainty; `superseded` is never evidence of this tab's acceptance. |
| 16 / authorization | No backend approval, fingerprint, lease, reservation, retry eligibility, or execution policy code is modified by this migration. Plan approval submits the saved full revision explicitly; summaries, replanning, and recovery assessments cannot approve implementation. |

## Adaptations and deviations

- The approved shadcn/Base UI plan supersedes the original Horizon/Chakra choice.
  No Chakra or Emotion dependency, template app, authentication, dashboard,
  charting, or transcript replacement is introduced.
- Registry source was retrieved from the pinned revision and its aliases/icons
  resolved locally instead of generating from the mutable live registry. All
  selected source, required dependencies, Nova CSS, animation and theme imports
  are committed. Sidebar cookie persistence/global shortcuts are omitted;
  application navigation preferences remain authoritative.
- Existing native details remain for supporting execution, revision, attachment,
  and legacy content. Saved plan sections and recovery incidents use Base
  Collapsible. The native file input retains its upload/focus protocol.
- Specialized merge and attachment hooks retain their established recovery
  contracts; they are not rewritten into a generic mutation engine. New small
  TaskReply, RemoveWorkspace, Disclosure, EmptyState, and URL helpers isolate
  presentation and operation ownership without changing endpoints.
- Bounded workspace-history observation only confirms a matching submission
  key. Missing rows never prove rejection; explicit same-ID replay remains the
  recovery path. No retry-status endpoint is invented.
- Removal has no client request ID. A lost response cannot safely be attributed
  from the workspace's latest archive alone. The UI retains uncertainty and
  offers read-only status checks instead of inventing exactly-once deletion.
- Existing concurrent workflow-progress edits in this checkout are preserved
  and excluded from migration commits, including their backend/type additions.

## Source checkpoints and validation

Foundation/navigation/composition and complete-parity changes are separate
commits for review. Source inspection found and addressed metadata submission
without readiness, stale task mutations, plan editor remounts on revision changes,
continuation keys coupled to timestamps, discarded uncertain HTTP requests, and
Monitor emptiness before a successful snapshot.

Dependency installation completed with lifecycle scripts disabled. Source and
Git diff inspection were performed. No tests, typecheck, lint, build, service
restart, or browser automation ran during implementation. No test results are
claimed. Source approval is still a reviewer gate.

QA owns the next authorized automated pass after source approvals. Use the Make
catalog (`make help`) to select the existing checks without redundant loops.
Coverage should include operation-order permutations, delayed accepted/failed
responses, reload/two-tab behavior, different operation IDs and draft versions,
plan section/full-document fidelity and revision conflicts, recovery pagination
and same-ID retries, independent late incident reports, the relevant existing
regressions, typecheck, and production build. No npm script or project command
was added or changed, so the command catalog requires no new entry.

**FINAL_UX_REVIEW_REQUIRED** after code approval. Browser/user acceptance remains
separate and pending for the complete task journey, both themes, 320px layout,
200% zoom, keyboard/focus, contrast, announcements, plan editing/conflicts,
uncertain acceptance, files, interrupted recovery, archive/restore, and workspace
safeguards. The README checklist is guidance for that later acceptance pass.
