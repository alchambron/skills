# Checkpoint and resume

The parent owns one ledger per run at `${CODEX_HOME:-$HOME/.codex}/review-runs/respond-all-<UTC timestamp>-<suffix>/ledger.json`, created by `ledger.py init`. Record the run directory's path in the conversation. Every artifact lives in that directory: discovery, analyzer reports, raw evidence, publish plans and publication journals. The ledger records their paths, never copies of their content; `ledger.py` rejects operations over 8 KB.

## Writing

Change the ledger only through `ledger.py apply RUN_DIR`, which takes a JSON list of operations. One locked, fsynced, atomic write covers the whole list; an invalid operation leaves the previous ledger untouched. Children may record their own PR's entry the same way, and the lock serializes them with the parent.

```bash
python3 ~/.codex/skills/respond-all/scripts/ledger.py apply "$RUN_DIR" <<'EOF'
[{"pr": "https://github.com/O/R/pull/7", "set": {"phase": "fix", "status": "running", "child": {"taskId": "T", "requestId": "R"}}},
 {"pr": "https://github.com/O/R/pull/7", "report": {"analysis": "pr-7-analysis.json"}},
 {"pr": "https://github.com/O/R/pull/7", "reviewer": {"login": "alice", "reviewId": 5, "changeRequest": true, "reviewedHead": "SHA"}},
 {"run": {"executionAuthorizedAt": "2026-10-30T13:04:00Z", "authorizationEvidence": "Go for it execute the plan"}}]
EOF
```

`ledger.py --help` lists the operations: `run`, `create`, `set`, `action`, `reviewer`, `comment`, `report` and `blocker`.

Write once per wake-up, before yielding, covering every child result, authorization change and phase transition of that wake-up. Publication intent and readback are journaled per action by `publish.py` in `pr-NUMBER-publication.json`; the ledger records the journal path and the PR's resulting status.

## Fields

| Location | Fields |
| --- | --- |
| Run | `runId`, `schemaVersion`, `createdAt`, `updatedAt`, `scope`, `initialQueue`, `deferred`, `lowSignal`, `coverageGaps`, `resourceLimits`, `localOperationOwner`, `prs` |
| PR | `repository`, `url`, `number`, `initialHeadSha`, `currentHeadSha`, `baseSha`, `labels`, `labelsCollectedAt`, `triggeringFeedback`, `untouched`, `reviewers`, `authorization`, `phase`, `status`, `child`, `reports`, `comments`, `actions`, `watcher`, `blockers`, `fixRounds` |
| Reviewer | `login`, original review ID/URL/state, `reviewedHead`, comment IDs, `changeRequest`, addressed, final-head request outcome |
| Authorization | Granted categories with the user's words as evidence, explicit limits, per-PR scope |
| Child | Mechanism, task ID, request ID, phase, worktree, assigned head |

Phases: `queued`, `analysis`, `fix`, `publish`, `respond`, `monitor`, `done`, `blocked`. Statuses: `queued`, `running`, `awaiting_authorization`, `blocked`, `completed`, `skipped`. Action statuses: `planned`, `in_flight`, `verified`, `blocked`. A terminal status keeps outstanding drafts and excluded actions visible. The ledger records authorization; it cannot grant it.

## Resume

1. Locate the run named by the conversation or the user and run `ledger.py summary RUN_DIR`. If the run cannot be identified, ask which one. A missing or invalid ledger is reconstructed from the run directory's reports and journals before any external action.
2. Re-check unfinished PRs in one call with `collect_feedback.py --pr URL ... --include-low-signal`, and refresh head, state, labels, review requests and checks. A push, reply or approval can drop a PR from fresh discovery without completing its authorized lifecycle; keep such entries until their outstanding actions are reconciled. A closed or merged PR gets an explicit recorded disposition.
3. Re-run `publish.py` on any PR whose journal is not `complete`. Its markers and request checks make the replay idempotent: existing replies are recorded rather than duplicated, and pending or satisfied review requests are not re-sent. A pushed head that differs from the plan makes it stop as stale, and the PR returns to analysis.
4. Reconcile existing child tasks through the supported mechanism before dispatching duplicates, keeping request IDs stable. Follow running T3 delegates' returned `next` instructions. Verify watcher state, and release a `localOperationOwner` whose task has ended.
5. A PR interrupted after a verified push but before publication stays in `respond`: run `publish.py` on its plan right away. Queued or running CI does not block replies or review requests.

Ledgers from earlier runs (schema 1, without limits) are read with the current defaults, and their extra fields are preserved.
