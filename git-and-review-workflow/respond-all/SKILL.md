---
name: respond-all
description: Find my open, unapproved PRs with untouched human feedback; delegate per-PR analysis and authorized fixes in parallel, then complete replies, reviewer requests and CI monitoring in the parent thread.
---

# Respond to untouched human feedback

Orchestrate the installed [respond skill](../respond/SKILL.md) for every matching PR from this parent conversation. In JouleMV, use the repository's `.agents/skills/respond/SKILL.md` and its referenced command when available. Read the chosen instructions before dispatching work. `/respond-all` and `$respond-all` mean this same workflow.

All PR analysis runs in child agents: feedback collection, per-comment dispositions, diagnosis, plans, reply drafts, and later CI/review investigations. Authorized fixes run in separate child phases. The parent discovers candidates, schedules work, aggregates results, verifies returned heads, publishes authorized changes and owns PR monitoring. Keep the batch in this conversation with child-agent results; ordinary top-level T3 threads are not subagents.

For any authorized browser work in T3 Code, load and follow [T3 Code Navigator](../../testing-and-quality/browser-testing/SKILL.md#t3-code-navigator): use the built-in `preview_*` browser exclusively, reuse owned tabs, and report unavailable browser evidence as **Blocked** without launching external Chrome or another browser. Include that requirement and the shared skill path in every browser-capable delegated or launched-thread brief. This does not expand this skill's execution or publication scope.

## Find the queue

Use the authenticated GitHub identity, not a hard-coded username. Default to all repositories accessible to that identity; honor a repository restriction supplied by the user.

Run the read-only collector from this skill's directory:

```bash
python3 scripts/collect_feedback.py
# Optional repository restriction:
python3 scripts/collect_feedback.py --repo EnerZam/JouleMV
# Recheck one candidate before processing it:
python3 scripts/collect_feedback.py --pr https://github.com/OWNER/REPO/pull/NUMBER
```

Resolve the script's absolute path from this skill; do not assume the shell is in its directory. It requires authenticated `gh` and Python 3's standard library. It paginates PR discovery, reviews, conversation comments, inline threads and replies, commits, and relevant timeline events. A partial collection is reported explicitly and exits nonzero. Never interpret failed collection as an empty queue.

A match must satisfy all of these conditions:

- The PR is open and authored by the authenticated user.
- It is not approved. Exclude an overall `APPROVED` decision. Where GitHub reports no overall decision, exclude PRs with a current human approval and no current human change request.
- Someone else using a personal GitHub account posted a comment or change request. Include top-level comments, submitted review bodies, and inline replies in unresolved threads. A personal account may post through AI tools; GitHub account type establishes this distinction, not guessed writing style.
- Since the latest such feedback, the author has not posted or edited a reply, submitted a review, or requested/removed a reviewer, dismissed a review, or marked the PR ready. No later branch commit or force push is visible either.

Bot feedback alone does not qualify. Later bot comments do not clear an unanswered human request. A historical change request does not qualify after the author has acted, even if GitHub still displays `CHANGES_REQUESTED`. An unresolved thread is not proof that the author has done nothing. Ignore pending reviews and resolved inline threads as new feedback.

The collector checks published GitHub activity; local uncommitted work and normal pushes of old, unchanged commits may not be timestamped by GitHub. Do not claim it proves the absence of such work. PR `updatedAt` alone cannot identify author activity because bots and CI also update it.

Show the matching PRs in a compact table, newest feedback first, with **PR / other labels**, **Risk**, **Priority**, and **Latest feedback** columns. Use the collector's labels and follow respond's **Show PR labels in the plan** rules. Preserve unavailable PRs as coverage gaps. If no matches remain, report that and stop.

## Delegate the queue

Freeze the initial queue. Keep a compact ledger with repository, PR URL, exact labels and collection time, triggering feedback URL/date, original human reviewer identities and thread/comment IDs, initial/current head SHA, permissions already granted, child phase/task/worktree, watcher and status. Re-fetch each candidate with `--pr` immediately before dispatching its analysis; skip it if now approved, closed, answered, or updated. Missing code access blocks only that PR; continue the others and retain the gap.

### Choose the child mechanism

Follow the chosen respond skill's **Coordinate subagents** contract for capability discovery, provider/model choice, T3/native dispatch, isolated target-head checkouts and fresh delegated phases with idempotent request IDs. The batch parent owns that dispatch; children execute their assigned analyzer or fixer phase directly rather than starting another batch or recursively invoking this coordination workflow.

Dispatch independent phases asynchronously up to available capacity, and refill freed slots with ready PR work. Queued tasks remain queued when capacity is exhausted; report that state accurately. Serialize only dependencies or conflicting checkouts/resources. A PR waiting for CI does not occupy an implementation slot or block another ready PR. When only asynchronous work remains, persist the ledger and yield for notifications instead of polling.

If no supported child mechanism is available, report the limitation and preserve the unanalyzed queue; do not substitute parent feedback analysis or describe queued work as running.

### Per-PR analysis phase

Give one read-only analyzer per PR its repository/URL, pinned head/base, triggering feedback, repository and respond instructions, and existing authorization. Every GitHub query must name the target repository or endpoint explicitly; the child's current branch must not determine the PR.

Require respond's full analyzer report: all relevant feedback, current labels, per-comment dispositions and evidence, fix/verification plan, exact-thread reply drafts, original human reviewer identities, observed head/base and blockers. Human feedback selects the PR; bot feedback within it still receives the disposition required by respond. The parent links the PR to this T3 thread when available and records the report. That PR's analysis phase remains read-only while independent authorized PR work can advance.

### Present the combined analysis plan

Before any combined authorization request, refresh labels and show one summary row per analyzed PR:

| PR / other labels | Risk | Priority | Feedback and suggested plan | Verification / blockers |
| --- | --- | --- | --- | --- |
| [#number — title](PR URL); remaining labels or None | Current risk label or Not labeled | Current priority label or Not labeled | Main feedback; proposed Fix / Answer / Defer / No change actions | Focused checks, dependencies, and missing evidence |

Apply respond's **Show PR labels in the plan** rules even when a repository-local respond command is selected. Follow the summary with the full per-comment tables, retaining the PR, risk, and priority columns so the context stays visible beside each proposed solution. Keep those columns in final per-PR outcome tables too. Labels are observed metadata; a critical risk label does not by itself establish a defect or high merge priority.

### Per-PR fix phase

Once that PR's analysis is complete and its fixes are authorized, dispatch its fixer immediately; independent analyses need not all finish first. Pass the approved dispositions, exact source head, isolated worktree, file scope, repository validation policy and permissions actually granted. Re-fetch the remote head before changes; if it advanced, obtain child analysis of the new head before applying a stale plan.

The fixer applies, validates and, when authorized, commits its changes. Require the worktree/branch, base and exact resulting head SHA, diff/checks/limits, remaining objections and final reply drafts tied to original comments/reviewers. The parent verifies the returned diff/head and clean commit state before publishing. Keep push, replies, review requests, bot triggers and watchers with the parent; send new CI/review issues through child analysis and authorized fixes.

## Authorization and continuation

A bare invocation authorizes the parallel analysis batch only. Finish dispositions and concrete plans for every eligible PR, then present one combined request for any approval required by respond or repository rules. Name and link the instruction requiring approval. Collection and read-only analysis need no separate approval gate.

Apply respond's authorization contract per PR. An authorized complete response workflow covers fixes, publication, replies, original-reviewer requests and babysitting together; a request limited to code fixes does not itself grant that wider scope. Carry existing batch or per-PR authorization forward without per-action approval gates. Explicit edit-only, local-only, no-commit, no-push or no-publication limits remain binding; prepare excluded steps and report them. Permission for one PR does not cover others, and delegation or this skill update grants no new authority. For an authorized batch, advance each PR as its results become ready; an unfinished analysis elsewhere is not a global barrier.

## Complete every authorized response

For each handled PR, complete respond's **Complete authorized fixes and follow through** lifecycle, including replies for dispositions without a push. The parent verifies the remote SHA; replies to every handled original comment/thread and reads back the posted replies; re-requests the original human reviewers rather than assignees; resumes applicable paused bots; and, when monitoring is authorized, invokes [babysit-pr](../babysit-pr/SKILL.md) for the existing PR's current head whether or not this response pushed changes. Preserve adequate existing replies/current review requests and final-head reviews as respond directs.

The parent owns T3 link/watch/yield and notification handling for every PR. Dispatch other ready work while CI or reviews run; when a head advances, refresh its ledger and monitor that head. Delegate new substantive diagnosis/fixes, then follow the repository/babysit milestone and stop watchers before handoff as required. Keep CI, bot feedback, unresolved threads and human review separate; approval, merging and deployment require their own authorization.

If explicit authorization excludes a required remote step, prepare its concrete draft and report the limit instead of executing it or calling the lifecycle complete. Preserve blocked PRs in the ledger while other independent PRs advance.

Finish with per-PR status: analyzed/awaiting authorization, completed with exact-head evidence, skipped after recheck, or blocked with the missing action. Keep proposed fixes, posted replies/readbacks, reviewer requests, code verification, CI and human approval distinct. Creating or updating this skill does not itself run the batch.
