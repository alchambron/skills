---
name: respond-all
description: Find open authored PRs with untouched human feedback, analyze a bounded batch and complete authorized fixes, replies and CI follow-through. Also report or resume an existing run's progress.
---

# Respond to a batch of PR feedback

The parent uses [respond](../respond/SKILL.md) for authorization and phase ownership. Children use only their named phase reference and relevant repository rules. Target-repository safety, validation and explicit user scope remain binding.

## Progress or continuation

A request such as `$respond-all status`, "Where are we?" or "Show progress" reads the existing run; it does not start discovery, authorize actions or resume a stopped watch:

```bash
python3 ~/.codex/skills/respond-all/scripts/ledger.py progress --latest
# For a specific run:
python3 ~/.codex/skills/respond-all/scripts/ledger.py progress RUN_DIR
```

Report the saved timestamp, batch total, completed/active/waiting/blocked/queued/skipped counts, deferred and no-findings counts, current phase and next action. `--json` gives machine-readable details. The snapshot is recorded evidence, not a live GitHub check. If several runs could match the user's request, identify the intended run before changing it. For a resume, follow [checkpoint and resume](references/run-ledger.md).

## Keep coordination bounded

Read `ledger.py progress` or `summary` and one-line child results. Full reports, source and raw logs stay in files for children/renderers. Limit parent reads to projected metadata and short summaries. Write JSON operations directly and batch them into one `ledger.py apply` per wake-up. Use publisher/renderer helpers for repeated work. At compaction, restore from the ledger rather than replaying full history.

Start every child with clean context: native Codex agents get `fork_turns: "none"`, and the brief carries variables only. Low-signal acknowledgements and publication-only children use `reasoning_effort: "medium"`.

Use the execution tool's supported long-command yield and returned wait instruction. When only async work remains, checkpoint and yield. Inspect each notification once; stop watching concrete infrastructure-blocked PRs with an explicit handoff instead of repeatedly re-triaging the same external failure. New actionable feedback stays visible in the ledger.

## Find and freeze the queue

A new invocation creates a new run. Discover across every repository the authenticated account can see; a working directory is not a repository restriction:

```bash
RUN_DIR=$(python3 ~/.codex/skills/respond-all/scripts/ledger.py init)
python3 ~/.codex/skills/respond-all/scripts/collect_feedback.py --out "$RUN_DIR/discovery.json"
python3 ~/.codex/skills/respond-all/scripts/history.py filter --discovery "$RUN_DIR/discovery.json" --out "$RUN_DIR/discovery-new.json"
python3 ~/.codex/skills/respond-all/scripts/ledger.py queue "$RUN_DIR" --discovery "$RUN_DIR/discovery-new.json"
```

`history.py filter` drops untouched feedback that an earlier run already answered or explicitly skipped, so a repeated launch queues only new or edited items; list its `alreadyHandled` PRs with the batch summary.

The collector selects open, unapproved, authored PRs with untouched human feedback. Author actions can exclude unresolved feedback from fresh discovery; existing authorized run entries remain until reconciled. Use `--repo` only for a user restriction. Partial collection is a coverage gap, never an empty queue.

Freeze 12 PRs by default (`queue --batch N` for explicit user size). Show account/scope, batch total, deferred count, low-signal PRs with links and gaps. Low-signal means a confidently benign no-findings item; ambiguous/mixed text stays actionable. `--include-low-signal` is an explicit override. Deferred PRs belong to a fresh thread's next batch. Provide the run path and progress command in this first update.

## Analyze and present one plan

Recheck the whole batch once with `collect_feedback.py --pr URL ... --include-low-signal --out RUN_DIR/recheck.json`; skip entries no longer eligible. Run at most four children, up to three of them fixers on independent PRs; layers of one stack run in sequence. Every build, test or install goes through `python3 ~/.codex/skills/respond/scripts/heavy_lock.py -- COMMAND`, one machine-wide slot shared with other threads. Analyzers run no builds or tests. Record limits in the ledger and briefs.

Dispatch analyzer briefs pointing at `respond/references/analyzer.md`, the run directory, assigned exact head and `ledger.py show RUN_DIR PR_URL` for untouched feedback. Use the phase's compact row scope and coverage inventory. Each PR's artifacts live under `RUN_DIR/prs/OWNER/REPO/`; save the analysis path in its ledger entry. Record a result once and refill the available slot.

When analyses finish, run `render_report.py --run-dir RUN_DIR`, show its summary and link the full plan. A bare invocation stops for one combined approval of the concrete plan; cite the instruction requiring it. Approval remains PR- and category-specific.

## Execute and monitor

For authorized no-code PRs, the parent publishes their reports together. For code changes, queue one fixer per PR; it pushes and publishes. Immediately link each pushed PR, then watch through supported T3 tools. Follow delegate `next` instructions. End the turn while waiting for PR notifications.

Handle each wake-up once: record returned results, refill slots, and checkpoint. Await settled CI before bundling failures and actionable comments into one follow-up. Bot walkthroughs alone do not require a fixer. A third fixer round becomes a blocked handoff to a separate `$respond PR_URL` task. Stop its watch; complexity must not hold a fixer slot.

After each phase transition, report compact progress with counts, current phase, blocker and next action. Keep publication completion separate from CI/approval. A terminal label preserves all outstanding drafts/actions.

## Finish

Use `render_report.py --run-dir RUN_DIR --outcomes`; ledger fields and referenced artifacts supply outcome evidence, so the parent need not rebuild per-PR reports. Show the summary and link the full result. Give exact heads, CI/review states, outstanding handoffs, deferred/no-findings lists and watch state. Unwatch every watched PR before final handback. Starting another batch or overriding 12 does not inherit the previous batch's authorization.

Save the run's settled feedback so the next launch skips it: `python3 ~/.codex/skills/respond-all/scripts/history.py record RUN_DIR`. Run it at final handback, and also after each batch's publication when CI follow-through continues.

## Validate changes

Run offline `scripts/test_collect_feedback.py`, `scripts/test_ledger.py` and `scripts/test_history.py`, plus respond's script tests. Progress reads require no credentials, watcher or network calls.
