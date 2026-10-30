---
name: respond-all
description: Find my open, unapproved PRs with untouched human feedback across all accessible GitHub repositories; delegate analysis and authorized fixes, then complete replies, reviewer requests and CI monitoring in the parent thread.
---

# Respond to untouched human feedback across repositories

Run the installed [respond skill](../respond/SKILL.md) for a batch of PRs from this conversation. Read it first: it defines the roles, briefs, authorization, plan rendering and follow-through used below. Also read the target repository's agent instructions and any repository-local `respond` skill or command; these supplement the shared contract. User instructions and repository policies retain precedence. `/respond-all` and `$respond-all` mean this same workflow.

## Stay lean

Every parent step re-sends the whole conversation, so this parent keeps its context small. Each wake-up is a few compact commands followed by a yield:

- Read state through `ledger.py summary` and child one-line returns. Reports, raw evidence, diffs and source stay on disk for children and renderers.
- Record state with one `ledger.py apply` per wake-up, batching all of that wake-up's operations.
- Publish through `publish.py` and render through `render_report.py`; write no per-PR scripts.
- Give multi-PR commands `yield_time_ms` of at least 60000, or run them in the background. Never poll at second intervals.
- When only asynchronous work remains, checkpoint and yield; notifications wake the parent.

Scripts live in `~/.codex/skills/respond-all/scripts/` (`collect_feedback.py`, `ledger.py`) and `~/.codex/skills/respond/scripts/` (`publish.py`, `render_report.py`). Resolve their absolute paths; do not assume the shell's directory.

## Find the queue

For a continuation, load the existing run with [Checkpoint and resume](references/run-ledger.md) and resume its unfinished entries. A new invocation starts a new run.

Discover with the authenticated GitHub identity across every repository its credentials can see; pass `--repo` only when the user restricts repositories (a JouleMV working directory is not a restriction):

```bash
RUN_DIR=$(python3 ~/.codex/skills/respond-all/scripts/ledger.py init)
python3 ~/.codex/skills/respond-all/scripts/collect_feedback.py --out "$RUN_DIR/discovery.json" [--repo OWNER/REPO ...]
```

The collector selects open PRs authored by the user, not approved, with human (non-bot) feedback that the author has not acted on since: no later reply, review, reviewer-request change, dismissal, ready-for-review, commit or force push. Feedback edits count as fresh feedback. It prints one line per PR and exits nonzero on partial collection: a failed collection is a coverage gap, never an empty queue. It sees only published GitHub activity, not local uncommitted work.

It sorts PRs into two lists:

- **matches**: at least one untouched item is a request, a question, a change request, an inline comment or a cross-PR reference. Ordered by priority label, then by the feedback that has waited longest.
- **lowSignal**: every untouched item is a review body or comment that only reports no findings. Show these in one compact table with the feedback link and skip them. Add `--include-low-signal` only when the user asks to handle them.

Freeze one batch, 12 PRs by default or the size the user asks for:

```bash
python3 ~/.codex/skills/respond-all/scripts/ledger.py queue "$RUN_DIR" --discovery "$RUN_DIR/discovery.json" [--batch N]
```

Report the account, repository scope, batch, deferred count, low-signal list and coverage gaps. Identify PRs as `OWNER/REPO#number` everywhere; ledger entries are keyed by full URL. Deferred PRs belong to the next run, started in a new thread.

## Analyze the batch

Run at most **four children at once**, of which at most **one fixer**. The run has **one CPU-heavy slot** (builds, test runs, typechecks, installs, service startup): the active fixer holds it, and analyzers and the parent take none. Prefer remote CI for full suites; required local validation is serialized rather than skipped. Record these limits in the ledger and every brief. The user may set different limits.

Before dispatching, re-check the whole batch in one call: `collect_feedback.py --pr URL --pr URL ... --include-low-signal --out "$RUN_DIR/recheck.json"`. Skip any PR that no longer qualifies. Then fill the slots with respond's analyzer brief, adding `Ledger: RUN_DIR` and replacing the untouched URLs with `Untouched feedback: ledger.py show RUN_DIR PR_URL (field untouched)`. When an analyzer returns, record its result and report path in one `apply`, then refill the slot. A PR whose code is inaccessible is blocked; other PRs continue.

Analyzers record every original human reviewer in the ledger, change-request reviewers flagged with login, review ID/URL, reviewed head and comment IDs. Those reviewers are re-requested after their feedback is addressed, whatever GitHub's review state shows later.

## Present one plan

When the batch's analyses are done, render them all:

```bash
python3 ~/.codex/skills/respond/scripts/render_report.py "$RUN_DIR"/pr-*-analysis.json --out "$RUN_DIR/combined-plan.md"
```

Show the printed summary table and link `combined-plan.md`, which holds every per-comment table, plan and draft reply. A bare invocation authorizes analysis only: end with one combined request for the approvals respond and the repository require, naming and linking each instruction that requires approval.

## Execute the authorized plan

Apply respond's authorization contract per PR: permission for one PR does not cover another, and explicit limits stay binding. Advance each PR as soon as it is ready.

- **No code change** (every row Answer, Defer or No change): publish all such PRs in one call, `publish.py RUN_DIR/pr-A-analysis.json RUN_DIR/pr-B-analysis.json ...`, and record each one-line outcome. These PRs get no watcher.
- **Code change**: queue one fixer per PR (one at a time) with respond's fixer brief. The fixer pushes and publishes. When it returns a pushed head, `link_pull_request` and `watch_pull_request` that PR in the same step and record it.
- **Stale** (`publish.py` exit 3, or a fixer returning `stale`): re-dispatch an analyzer for the new head before any further publication.

## Handle notifications

Handle each wake-up in one pass, then record and yield:

- **Child finished**: record its line, refill the freed slot from ready work.
- **CI still running, or only bot comments**: record and keep waiting. Bot comments are collected for the next round.
- **CI settled with a failure, new human feedback, or bot comments collected**: queue one follow-up fixer round for that PR covering all of it (respond's fixer reference, "Follow-up rounds").
- **CI green and nothing new**: record the PR as `done`, unwatch it.

A PR that would need a **third fixer round** leaves the batch: mark it `blocked` with reason `handoff`, unwatch it, and recommend running `$respond PR_URL` in its own thread. Its complexity no longer holds the single fixer slot.

## Finish

Render outcomes with `render_report.py ... --outcomes --out "$RUN_DIR/combined-outcomes.md"` and show its summary table. Then give per-PR status: awaiting authorization, completed at an exact head, skipped after recheck, handed off, or blocked with the missing action. Keep posted replies, reviewer requests, code verification, CI and human approval distinct. List the deferred and low-signal PRs and suggest a new `$respond-all` thread for the next batch. Unwatch every watched PR before handing back. Approval, merging and deployment need their own authorization; updating this skill runs nothing.

## Validate skill changes

From `scripts/`: `python3 -B test_collect_feedback.py` and `python3 -B test_ledger.py`. Both run offline. Respond's scripts have their own tests.
