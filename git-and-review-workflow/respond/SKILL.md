---
name: respond
description: Analyze PR feedback in a delegated phase and, when authorized, fix it, publish replies, request reviewers and monitor CI. Use for PR comments, reviewer requests or feedback plans.
---

# Respond to PR feedback

A bare `/respond` or `$respond` requests a plan. Resolve the target from the supplied URL/number or current branch. Read target-repository instructions. Reuse the user's established authorization for code, commit/push, replies, reviewer requests, CI actions and thread resolution; explicit limits remain binding. A push, merge, approval and deployment are separate actions. Prepare excluded steps as drafts and continue permitted work.

## Phase ownership

The parent schedules work, records compact ledger results, presents rendered plans and owns T3 linking/watching. Each child reads only its phase reference and applicable repository rules:

- Read-only analyzer: [references/analyzer.md](references/analyzer.md), with [report schema](references/report-schema.md). It writes the analysis and coverage inventory.
- One fixer per PR: [references/fixer.md](references/fixer.md). It implements approved rows and owns authorized push, replies and reviewer requests.

Use native subagents for supported same-provider work and T3 `delegate_task` for isolation, other providers or requested settings. Follow the tool's orchestration/continuation instructions. A fixer needs an isolated checkout with one writer, assigned exact head and stable request ID. If delegation is unavailable, report that blocker. Delegation adds no authorization.

A brief carries variables, not copied instructions: role/reference, PR URL/head/base, untouched or approved row IDs, run directory, exact authorization/limits, and resource slot. Start every child with clean context: native Codex agents get `fork_turns: "none"`, since a forked child re-reads the parent's history on every step. Source, logs and full reports stay on disk; each result is one line with PR URL, head, outcome, artifact path and remaining objections.

## Artifacts and plans

Use `RUN_DIR/prs/OWNER/REPO/pr-NUMBER-*` for each PR's artifacts. Preserve repository identity even when PR numbers coincide. Write JSON artifacts directly; use the shared scripts for recurring transformations.

Full rows cover untouched human feedback, other open human threads and actionable current bot findings. The coverage inventory accounts for history and author comments. An explicit exhaustive-table request gets an appendix on disk. Summary/context rows have `publish: false`. The current user's scope takes precedence over defaults; generic complete-coverage requirements do not imply repeated analysis or replies for history.

With a run ledger, render via `render_report.py --run-dir RUN_DIR`; for a single report, pass its path. Show only the printed summary and link the full report. Risk, Priority and other labels remain separate from each row's importance/value; refresh labels or label the timestamped snapshot. Displaying labels authorizes no label mutation.

## Complete the authorized workflow

- No code change: the parent runs `publish.py REPORT.json [...]` for the covered PRs together.
- Code change: the fixer writes its publication plan with the original `humanFeedbackSnapshot`, pushes when authorized, runs the shared publisher and records the returned journal path.
- The publisher blocks moved heads, new/edited human feedback and missing snapshots. Re-analyze stale feedback and revise drafts; preserve original raw evidence for safe legacy migration. Replay the same plan after an uncertain write, so hidden markers prevent duplicate replies.
- Re-request original human reviewers whose feedback was addressed, change-request reviewers included. Preserve pending requests and skip reviewers already satisfied at the final head. Bot review stays separate from human approval.
- The parent links each pushed PR immediately, watches it through supported T3 tools, and ends the turn while waiting. Follow returned notifications; batch settled CI failures and actionable feedback into one follow-up round. Repository CI policy governs validation. PRs with no push get no watcher. Stop all watches before final handback.

Resolve threads only when authorized and verified. Replies still accompany implemented fixes. This workflow alone does not authorize merging.

## Progress and outcomes

Record phase/status, head, report/journal paths, focused verification, current-head CI, approval and blockers in the ledger in one update per wake-up. `ledger.py progress RUN_DIR` reports saved state without a network refresh. At phase transitions and when asked "Where are we?", show its compact counts, current PR/phase, blockers and next action. Include the run path and progress command in the first update. Status requests are read-only and preserve the active task.

Render outcomes with `render_report.py --run-dir RUN_DIR --outcomes`; it joins reports, publication journals and ledger status without bespoke outcome builders. Keep proposed fixes, verified publication, focused tests, CI, human/bot reviews, mergeability and deployment distinct. A terminal ledger label cannot hide remaining drafts or actions. End with exact heads, unresolved work and watch state.

## Validate changes

Run the offline tests in `scripts/`: `test_publish.py` and `test_render_report.py`, plus relevant regression cases. They make no GitHub writes.
