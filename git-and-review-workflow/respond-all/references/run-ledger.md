# Checkpoint and resume

The run ledger lives at `RUN_DIR/ledger.json`. Record the run path in the conversation. Raw evidence, reports and journals remain files; ledger entries hold paths and compact state. Use repository-qualified artifacts: `RUN_DIR/prs/OWNER/REPO/pr-NUMBER-*`. Existing flat runs retain their recorded paths.

## Progress

```bash
python3 ~/.codex/skills/respond-all/scripts/ledger.py progress RUN_DIR
python3 ~/.codex/skills/respond-all/scripts/ledger.py progress --latest
# Structured snapshot for tooling:
python3 ~/.codex/skills/respond-all/scripts/ledger.py progress RUN_DIR --json
```

The command is read-only and offline. It reports saved timestamps, exclusive state counts, active worker slots, deferred/no-findings counts, exact heads, current phases, blocker/next actions, and separate verification/publication/approval evidence. CI monitoring is waiting work, not an active child slot. Completed/skipped labels with outstanding actions or drafts remain unfinished. Missing historical evidence is reported as unrecorded, not inferred from a push.

A user may say `$respond-all status` or "Where are we?". Locate the run from the conversation (or use `--latest` when unambiguous) and summarize this command. A status request preserves the active task and existing authorization. It does not start a batch, replay publication or restore a watcher. When the user explicitly asks for a live refresh, record its timestamp and results separately from the saved snapshot.

## Writes

Use `ledger.py apply RUN_DIR` with a JSON list on stdin. One lock and atomic write cover all operations; invalid operations leave the previous ledger intact. Batch each wake-up's phase/results into one call. Each operation is limited to 8 KB, so large evidence belongs in referenced files.

```json
[
  {"pr": "https://github.com/O/R/pull/7", "set": {"phase": "fix", "status": "running", "child": {"taskId": "T", "requestId": "R"}}},
  {"pr": "https://github.com/O/R/pull/7", "report": {"analysis": "prs/O/R/pr-7-analysis.json"}},
  {"pr": "https://github.com/O/R/pull/7", "reviewer": {"login": "alice", "reviewId": 5, "changeRequest": true, "reviewedHead": "SHA"}},
  {"run": {"executionAuthorizedAt": "ISO time", "authorizationEvidence": "user's words"}}
]
```

The helper accepts `run`, `create`, `set`, `action`, `reviewer`, `comment`, `report` and `blocker` operations; `--help` gives syntax. The ledger records authorization but grants none.

For each completed fix, record the exact head, focused verification and per-row outcomes as compact `comment` updates. Record `verification`, `ci` (with head/timestamp), `publicationStatus`, `reviewDecision`/`humanApproval` with `approvalHeadSha`, and blockers separately. Each `reports.publication` value is the publisher's actual returned journal path. A verified publication does not mark CI or approval passed. These fields feed progress and `render_report.py --run-dir RUN_DIR --outcomes` directly.

Phases: `queued`, `analysis`, `fix`, `publish`, `respond`, `monitor`, `done`, `blocked`. Statuses: `queued`, `running`, `awaiting_authorization`, `blocked`, `completed`, `skipped`. Actions: `planned`, `in_flight`, `verified`, `blocked`. Preserve unfinished drafts/excluded actions at handoff.

## Resume an authorized run

1. Read progress and unfinished entries. Resolve missing/invalid ledgers from reports and journals before external actions. Use the supported task mechanism to reconcile existing children before dispatching duplicates; keep request IDs stable.
2. Recheck unfinished PRs together (`collect_feedback.py --pr URL ... --include-low-signal`) and refresh heads, state, labels, checks and review requests. Fresh discovery can exclude a PR after the author pushed/replied; keep its unfinished authorized entry. Record closed/merged dispositions explicitly.
3. For incomplete publication, replay the same plan through `publish.py`. Missing `humanFeedbackSnapshot` in a legacy plan is stale. Build it from the original complete REST evidence using `feedback_snapshot.py`; if that evidence is unavailable, re-analyze and revise drafts. Never bless old drafts with a snapshot of current feedback. New/edited human feedback or a moved head returns to analysis.
4. A verified push followed by interrupted publication stays in `respond`; pending CI does not block authorized replies or reviewer requests. Keep T3 linking/watching with the parent and follow delegate `next` instructions.
5. Record the result once, release completed resource owners and yield for outstanding async work. At final handoff, unwatch all watched PRs and leave blockers/drafts visible.

Older ledger schemas are accepted with current defaults and preserved extra fields. Repository-qualified journals prevent collisions; a legacy flat journal is reused only when its recorded PR URL matches exactly. A stale retry preserves earlier verified reply/request readbacks. A revised plan archives the previous journal and records its path in `history`; `--dry-run` leaves journals unchanged.
