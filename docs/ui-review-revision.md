# Migration review revision

This revision addresses the eight findings against `e09b638`. Source review is
next. Automated QA, browser acceptance, and the final advisory UX review remain
pending. No tests, typecheck, lint, build, or browser checks ran in this stage.

1. **Attachment ownership:** unresolved task/message/question attempts lock file
   selection, retries, removal, and drop handlers, including after reload. Text
   drafts remain available. Acceptance clears only its matching attachment draft;
   subsequent uploads create a fresh server draft. Opening accepted work preserves
   draft text.
2. **Plan draft identity:** Markdown and feedback use independent persisted UUIDs.
   Replaying a saved action clears only its matching draft identity and content,
   and only while that attempt remains current. Legacy attempts without identities
   preserve drafts. Revision conflict handling and complete Markdown remain intact.
3. **Question recovery:** task-scoped question envelopes survive reload, keeping
   saved uncertain responses reachable after the question leaves pending input.
   Matching durable response messages establish acceptance; disappearance does
   not. Expired/answered-elsewhere feedback and explicit dismissal remain available.
4. **Merge rejection:** matching operation lookup precedes rejection handling.
   A structured preflight rejection plus a missing matching operation settles the
   obsolete snapshot, refreshes eligibility, and requires a new explicit click.
   Generic HTTP errors remain uncertain. Rejection observations also reach other
   tabs; cleanup checks the operation ID before removing storage or pending state.
5. **Cleanup retry:** a matching failed archive permits another confirmation after
   fresh archive and workspace checks. Prior accepted archive IDs remain recorded.
   A partially removed checkout can retry verified server cleanup. Unknown or
   mismatched acceptance cannot authorize another DELETE.
6. **Async feedback:** continuation, restart, question, plan, and recovery callbacks
   settle through shared receipts. Attempt warnings come from the reconciled
   current record, so delayed failures cannot contradict acceptance or overwrite
   another attempt's warning. Restart result callbacks also check current identity.
7. **Evidence confidentiality:** recursive public projections redact internal
   checkout evidence from task detail (including plan and step outputs), run
   detail/list, and synchronous workflow outputs before string conversion. These
   projections copy data; durable evidence and fingerprint checks remain unchanged.
8. **Dialog reachability:** confirmation content has viewport-bounded height,
   internal scrolling, and path wrapping. Cancel retains initial focus.

The backend public-projection repair is the sole deviation from the frontend-only
migration boundary, required by the confidentiality finding. No schema, endpoint,
execution policy, or fingerprint behavior changed.

QA should cover delayed success/failure, shared and different operation IDs,
reload recovery, draft changes during uncertainty, rejected merge snapshots,
matching/mismatched cleanup failures (including a missing checkout), and nested
public evidence projections. Browser acceptance must separately cover scrolling
and Cancel focus in short/narrow viewports, plus the planned journey and theme
checks. These are pending gates, not results established by source inspection.
