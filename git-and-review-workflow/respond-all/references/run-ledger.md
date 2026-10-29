# Checkpoint and resume

The parent owns one durable ledger per run. Create a unique directory under `${CODEX_HOME:-$HOME/.codex}/review-runs/respond-all-<UTC timestamp>-<unique suffix>/` and record its absolute path in the conversation. Store `ledger.json` and linked analyzer reports, dispositions and drafts there, outside repository checkouts. Record each child's report in this directory before its checkout or context is released.

## Ledger format

Use JSON with `schemaVersion: 1`. Preserve the initial queue and its evidence; update current state separately. These fields are required, with empty arrays or null for information not yet collected:

| Location | Fields |
| --- | --- |
| Run | `runId`, `schemaVersion`, `createdAt`, `updatedAt`, `scope`, `initialQueue` (PR URLs), `coverageGaps`, `prs` (object keyed by PR URL) |
| Resource scheduling | `resourceLimits` (maximum active children, fixers and CPU-heavy local operations), plus the current local-operation owner; for older ledgers without limits, apply the skill's defaults |
| PR | `repository`, `url`, `initialHeadSha`, `currentHeadSha`, `baseSha`, `labels`, `labelsCollectedAt`, `triggeringFeedback` (stable ID/URL/date), `reviewers`, `authorization`, `phase`, `status`, `child`, `reports`, `comments`, `actions`, `watcher`, `blockers` |
| Reviewer | Login, original review ID/URL/state, reviewed head, associated comment/thread IDs, whether addressed, and final-head review-request outcome with readback evidence |
| Authorization | Granted action categories and their user-message evidence, plus explicit limits; preserve per-PR scope |
| Child | Mechanism, task ID, phase, request ID when supported, worktree and assigned head |
| Comment | Original comment ID/URL, thread ID, reviewer, disposition, draft/report path, verification and outcome |
| Action | Stable `key`, `kind`, `target`, `headSha`, `status`, `intentAt`, `observedAt`, `evidence`, `error` |
| Watcher | PR URL, last observed watched head, state and observation time |

Use phases `analysis`, `fix`, `publish`, `respond`, `monitor`, `done`; statuses `queued`, `running`, `awaiting_authorization`, `blocked`, `completed`, `skipped`. Action statuses are `planned`, `in_flight`, `verified`, `blocked`. A terminal PR status does not erase outstanding drafts or excluded actions; record explicit user limits and remaining work.

Give each action a stable key based on PR URL, action kind and target: a reply targets its original comment/thread ID; a reviewer request targets the reviewer and final head; a push targets the branch and intended SHA. Record commit SHAs, reply URLs, reviewer-request readbacks and CI observations in `evidence`, keeping each type separate. Store full content in linked report files when needed. The ledger records authorization; it cannot grant or expand it.

## Write timing

Write the initial ledger before dispatch. Checkpoint after every child result, authorization change, phase transition and verified external action, and before yielding or handing back. One parent writes the ledger; children return evidence to it.

Before an external action, save its exact intent and `in_flight` status. Afterward, read the resulting state back and checkpoint the evidence as `verified`; failures or uncertainty remain recorded. Use atomic replacement: write valid JSON to a temporary file in the same directory, flush and fsync it, then replace `ledger.json`. Keep the previous valid ledger if writing fails, and stop new external actions until checkpointing works.

## Resume procedure

1. Locate the run named in the conversation or by the user and load its ledger and referenced reports. If the requested run cannot be identified, ask which run to continue. A missing or invalid ledger requires reconstruction from available reports and conversation evidence before resuming external actions; do not assume an empty queue or completed work.
2. Reconcile unfinished entries directly by PR URL. Refresh head/base, open/closed state, labels, relevant comments, reviews, review requests and checks. A push, reply or approval may remove a PR from fresh discovery without completing its authorized lifecycle. Preserve those entries until the outstanding actions are reconciled. Closed or merged PRs need an explicit recorded disposition rather than automatic further publication.
3. Reconcile `in_flight` actions before retrying. For a push, compare the observed remote SHA and ancestry with the intended commit. For a reply, inspect the original thread/conversation for the expected author and content and record an adequate existing reply. For a review request, inspect current requests and subsequent reviews on the final head; retain the saved original change-request reviewers even if GitHub no longer shows their old review state. Verify watcher state through the available mechanism and reconcile the recorded local-operation owner before reusing its slot. If the effect cannot be determined, mark that action blocked and continue independent work; do not blindly repeat it.
4. Reuse documented authorization within its original scope and apply later user limits. Compare the live head with the last verified head and known child/published commits. Resume remaining replies and review requests after a verified own push; obtain fresh child analysis before applying a plan to an unexpected head. Refresh drafts and verification claims to match the actual published head.
5. Reconcile existing child tasks through the supported mechanism before creating duplicates, preserving request IDs across retries. Follow running T3 delegates' returned `next` instructions; use notification-driven continuation. Resume only unfinished, authorized phases and checkpoint the reconciled state before further side effects.

A PR interrupted after a successful push but before replies remains in phase `respond`, even when the collector now excludes it as author activity. Record the verified push, then finish its authorized replies and reviewer requests immediately, before waiting for CI. Queued or running CI does not block those actions. Continue monitoring CI and human review concurrently after the request readback. Do not reapply the fix or infer those later steps succeeded from the push.
