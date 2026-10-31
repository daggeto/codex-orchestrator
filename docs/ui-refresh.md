# Orchestrator appearance refresh

The existing Vite/React application adopts Horizon UI Free's rounded surfaces,
purple accents, and sidebar hierarchy. Dark is the default; the appearance
control switches to light. No demo pages, analytics, external fonts, backend
changes, database migrations, or template routing are included.

## Checkpoints and file map

1. Foundation and navigation: `web/src/styles/tokens.css`, `styles.css`,
   `appearance.tsx`, `navigation.tsx`, `components/ui/*`, `App.tsx`, `main.tsx`,
   `components/AppShell.tsx`, `components/RepositorySidebar.tsx`,
   `components/DirectoryPicker.tsx`, and `pages/RepositoriesPage.tsx`.
   `web/package.json`, its lockfile, and `vite.config.ts` add Tailwind's Vite plugin
   without changing scripts. `web/public/third-party-notices.txt` records the
   adapted Card source, pinned Horizon revision, and MIT license.
   Acceptance: consistent themes, one navigation poll, existing hash routes,
   clear active links, accessible mobile navigation, and repository registration.
2. Task screens: `pages/RepositoryPage.tsx`, `pages/WorkspacePage.tsx`,
   `components/TaskComposer.tsx`, `components/Chat.tsx`, `pages/TaskPage.tsx`,
   `pages/AgentPage.tsx`, `components/WorkflowTimeline.tsx`,
   `components/AgentConversation.tsx`, and `components/MessageContent.tsx`.
   Acceptance: checkout and publication stay visible, destructive actions are
   distinct, drafts and submission locking persist, history remains paginated,
   and approvals, messages, completion, cancellation, and action details work.
3. Monitor and documentation: `web/src/Monitor.tsx` and `README.md`.
   Acceptance: scoped layout, explicit connection/stale/gap labels, retained
   selection, diff scrolling, and unchanged continuation/restart recovery.

## Contracts and decisions

- `NavigationProvider` owns the existing `/api/navigation` resource. All pages
  consume its `repositories`, `workspaces`, `tasks`, and `agents` without changing
  fields or identifiers. Resource errors retain the last available navigation.
- Monitor `Task`/`Workspace` records remain distinct from navigation
  `TaskRecord`/`WorkspaceRecord`. `types.ts`, `api.ts`, and `useMonitor.ts` keep
  their existing contracts, snapshot reconciliation, SSE, and bounded buffer.
- Appearance uses `Theme = 'dark' | 'light'` and local storage key
  `orchestrator.ui.theme.v1`. Invalid or inaccessible storage falls back to dark;
  a failed write keeps the selected theme in memory.
- Mobile navigation uses a native modal dialog for focus containment and Escape.
  Desktop navigation stays in a sidebar. Both consume the same navigation poll.
- Tailwind utilities are available without Preflight, avoiding a global reset of
  forms and Markdown. Surface layout is applied through component classes.
- Task payloads, request/idempotency identifiers, draft keys, publication policy,
  folder restrictions, and Markdown URL/HTML restrictions retain their behavior.

## Validation gates

Implementation and source review are separate from automated and manual checks.
Automated tests, typecheck, build, and browser acceptance were not requested for
this implementation. Added theme regression cases are available for a later
explicit test run. README contains the manual acceptance checklist. Passing
source review does not establish browser acceptance or provider CI status.
