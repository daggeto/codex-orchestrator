# Migration review revision

This revision addresses the eight findings against `e09b638`. Source review is
next. Automated QA, browser acceptance, and the final advisory UX review remain
pending. No tests, typecheck, lint, build, or browser checks ran in this stage.

The follow-up to `4500ccb` closes the two remaining source findings: assessment
transcripts now have a private/public boundary, including legacy event reads,
and evidence redaction targets the plan model instead of arbitrary dictionaries.

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
7. **Evidence confidentiality:** public projections remove `checkout_evidence`
   only from explicitly identified `kind: plan_approval` models in task detail,
   nested step outputs, run detail/list, and synchronous workflow outputs before
   string conversion. Unrelated variables and outputs named `tracked_patch`,
   `untracked_hashes`, or `checkout_evidence` retain their values and sibling fields,
   including Restart's saved inputs. Recovery assessment inputs are private turn
   inputs: the backend receives complete evidence while persisted events, live
   observations, activity previews, and errors expose a public lifecycle summary.
   Backend echoes, reasoning, and tool output are private for these turns too.
   Agent-history reads identify old assessment threads through durable recovery
   records and project their saved payloads without changing event IDs, task
   scoping, pagination, or cursors. Other conversation threads retain their content.
   Durable server evidence and fingerprint checks remain unchanged.
8. **Dialog reachability:** confirmation content has viewport-bounded height,
   internal scrolling, and path wrapping. Cancel retains initial focus.

Backend projections and private recovery transcript handling are the deviations
from the frontend-only migration boundary, required by the confidentiality finding.
No schema, endpoint, execution policy, or fingerprint behavior changed.

QA should cover delayed success/failure, shared and different operation IDs,
reload recovery, draft changes during uncertainty, rejected merge snapshots,
matching/mismatched cleanup failures (including a missing checkout), and nested
public evidence projections, previously persisted assessment events, backend
echoes, and custom evidence-named variables with intact Restart inputs. Browser
acceptance must separately cover scrolling
and Cancel focus in short/narrow viewports, plus the planned journey and theme
checks. These are pending gates, not results established by source inspection.
