---
name: respond
description: Analyze PR feedback in subagents and, when authorized, fix it in parallel, reply to reviewers, request their review again, and babysit CI. Use for PR comments, reviewer requests, or feedback plans.
---

# Respond to PR feedback

Use the PR number supplied by the user, if any. Otherwise, find the PR for the current branch with `gh pr view`. If neither identifies a PR, ask for its number. Read the repository's agent instructions before examining code.

A bare `/respond` or `$respond` requests a plan only. Determine the authorized response workflow from the user's request and existing session instructions. When that authorization covers the full workflow, complete verified fixes, publication, original-comment replies, reviewer re-requests and babysitting without asking again. A request limited to code fixes does not itself authorize publication or reviewer-facing actions.

Establish which action categories are covered: code changes, commits/pushes, replies, reviewer requests, CI actions and thread resolution. A single workflow authorization can cover them together; reuse that scope assessment unless the user's instructions or task scope change, rather than adding per-action approval gates. Explicit code-only, local-only, no-commit, no-push or no-post limits remain binding; replies alone do not authorize code changes. Prepare excluded steps as drafts or a concrete handoff, report the blocked steps, and continue independent permitted work. Follow repository approval rules within the established scope.

## Roles

The parent stays **lean**: it schedules children, records one line per result, presents rendered plans, runs `publish.py` for PRs without code changes, and owns T3 linking and watching. Substantive work happens in children, each running one phase from its reference and returning one line:

- **Analyzer** (read-only): [references/analyzer.md](references/analyzer.md). Collects feedback, builds rows and draft replies, and writes `pr-NUMBER-analysis.json`.
- **Fixer** (one per PR): [references/fixer.md](references/fixer.md). Applies approved rows, validates, commits, and when authorized pushes and publishes that PR's replies and reviewer requests. It is the PR's single publishing owner.

The parent reads child results through their one-line returns and rendered summaries. Reports, raw evidence and source code stay on disk. If no supported child mechanism is available, report the blocked phase; do not substitute parent analysis or implementation. Delegation preserves existing authorization; it does not expand it.

## Dispatch children

Discover the live provider/model catalog through T3 `orchestrator_capabilities` when available. Use native subagents when they support the selected same-provider model; otherwise use T3 `delegate_task`. A fixer needs its own checkout: in T3 use `delegate_task` with `isolation:"worktree"` (it starts from the parent's committed state, so the brief names the exact PR head to check out); outside T3, use the supported workspace mechanism. Each checkout has one writer. Give each round a distinct `clientRequestId` that stays stable across retries, and pass prior evidence and unresolved objections by file path. All child work remains in the current user thread.

Briefs carry variables and point at the phase reference; the reference carries the method:

```text
Role: analyzer. Follow ~/.codex/skills/respond/references/analyzer.md for exactly this PR.
PR: URL  head: SHA  base: SHA
Untouched feedback: URL, URL
Run dir: RUN_DIR
Authorization: analysis only.
```

```text
Role: fixer, round N. Follow ~/.codex/skills/respond/references/fixer.md.
PR: URL  head: SHA  branch: HEAD_REF  head repository: OWNER/REPO
Approved rows: RUN_DIR/pr-NUMBER-analysis.json ids ID, ID (other rows: replies only)
Authorization: CATEGORIES ("user's words"); limits: LIMITS
Run dir: RUN_DIR
```

For a single PR, use `~/.codex/review-runs/respond-NUMBER-<UTC timestamp>/` as the run directory. Independent feedback groups in one PR may get separate analyzers; tightly coupled fixes get one fixer.

## Present the plan

Render analyzer reports with `python3 ~/.codex/skills/respond/scripts/render_report.py REPORT.json [...]`. It prints the summary table and writes every per-comment table, plan and draft reply to a linked Markdown file. Show the summary table in chat and link the file. Refresh labels before presenting a plan or outcome; labels can change without a new commit. If a refresh fails, mark labels **Unavailable** or name the timestamped snapshot.

Label rules the renderer applies: **Risk** and **Priority** get their own columns beside the feedback and solution. JouleMV uses `risk:limited|disruptive|critical` (deployment consequence) and `priority:low|medium|high` (merge urgency). Other repositories keep labels verbatim, with legacy priority labels such as `high` or `Urgent Ticket` marked legacy. A missing label is **Not labeled**, never assumed low. Several labels in one category show as **Conflicting labels**. Remaining labels appear under **PR / other labels**, or `None`. Labels are observed metadata, separate from each comment's importance and value. Any proposed reassessment belongs in the plan, and displaying labels authorizes no label changes.

## Complete authorized fixes and follow through

1. **No code change needed** (every row Answer, Defer or No change): run `publish.py` on the analysis report directly. It verifies the head, refuses stale plans, posts each reply once, re-requests reviewers and reads everything back. Several reports can go in one call.
2. **Code change needed**: dispatch the PR's fixer with the approved rows and authorization. It pushes and publishes through `publish.py` itself.
3. **Re-request the original human reviewers** whose feedback was addressed, change-request reviewers included, through GitHub review requests: never assignees, mentions or inferred code owners. `publish.py` preserves pending requests and skips reviewers who already reviewed the final head. Request review while CI runs; green CI is not a prerequisite. When a paused bot must review the new head, trigger its supported review command and keep bot review distinct from human approval.
4. **Babysit pushed PRs** with [babysit-pr](../babysit-pr/SKILL.md) and current-head evidence. With T3 tools, `link_pull_request` and `watch_pull_request` each pushed PR, dispatch ready work, then end the turn: notifications wake the parent. PRs without a push have no new CI to watch. Route a CI failure or new feedback to a follow-up fixer round. Continue until required checks and actionable feedback are handled or an external blocker is concrete. Before handing back, `unwatch_pull_request` every watched PR. This workflow does not authorize merging.

Resolve threads only within the user's authorization and after verification; a reply is still required when a fix is implemented.

## Record outcomes

Render outcomes with `render_report.py REPORT.json [...] --outcomes`. It joins every row with its `pr-NUMBER-publication.json` journal: posted reply URLs, drafts, and reviewer outcomes (**Requested**, **Already requested**, **Already reviewed final head**, **Blocked**). Every original comment has a disposition and an outcome. Keep proposed work separate from completed work; a pushed fix alone does not complete the workflow. The final report names the PR URL and published head, current-head CI, human and bot review states kept separate, merge/conflict state, active watches, and any human or external blocker.

## Validate skill changes

From `scripts/`: `python3 -B test_publish.py` and `python3 -B test_render_report.py`. Both run offline.
