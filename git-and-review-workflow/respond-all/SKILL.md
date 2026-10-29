---
name: respond-all
description: Find my open, unapproved PRs with untouched human feedback across all accessible GitHub repositories; delegate analysis and authorized fixes, then complete replies, reviewer requests and CI monitoring in the parent thread.
---

# Respond to untouched human feedback across repositories

Orchestrate the installed [respond skill](../respond/SKILL.md) for every matching PR from this parent conversation. Read it before dispatching work: it is the shared contract for coordination, analyzer reports, labels, authorization and response completion referenced below. Also read the target repository's agent instructions and any repository-local `respond` skill or command for project-specific requirements; these supplement the shared contract rather than replace it. User instructions and repository policies retain precedence for scope and project work. `/respond-all` and `$respond-all` mean this same workflow.

All PR analysis runs in child agents: feedback collection, per-comment dispositions, diagnosis, plans, reply drafts, and later CI/review investigations. Authorized fixes run in separate child phases. The parent discovers candidates, schedules work, aggregates results, verifies returned heads, publishes authorized changes and owns PR monitoring. Keep the batch in this conversation with child-agent results; ordinary top-level T3 threads are not subagents.

For any authorized browser work in T3 Code, load and follow the general [T3 Code Navigator](../../testing-and-quality/browser-testing/SKILL.md#t3-code-navigator) section: use the built-in `preview_*` browser exclusively, reuse owned tabs, and report unavailable browser evidence as **Blocked** without launching external Chrome or another browser. Use the target project's own URLs, setup and credentials; JouleMV-specific guidance applies only to JouleMV. Include that requirement and the shared skill path in every browser-capable delegated brief. This does not expand this skill's execution or publication scope.

## Find the queue

For a continuation, first load the existing run's ledger using [Checkpoint and resume](references/run-ledger.md). Resume its unfinished entries even if they no longer match fresh discovery. A new invocation starts a new queue unless the user asks to continue a prior run; add newly discovered PRs to an existing run only within the user's requested scope.

Use the authenticated GitHub identity, not a hard-coded username. By default, discover that user's PRs across every repository visible to the current credentials: personal, organization, public, private and contributions to other owners' repositories. Local clones, the current worktree, and organization membership do not restrict discovery. Pass `--repo` only when the user explicitly restricts the run; a JouleMV working directory is not such a restriction. Existing author, approval and feedback filters below still apply.

Report the account and requested repository scope with the queue. Coverage is limited to what GitHub exposes to those credentials; retain known access failures as gaps rather than claiming access to invisible repositories.

Run the read-only collector from this skill's directory:

```bash
python3 scripts/collect_feedback.py
# Only when the user restricts repositories (repeatable):
python3 scripts/collect_feedback.py --repo OWNER/REPO --repo OTHER/REPO
# Recheck one candidate before processing it:
python3 scripts/collect_feedback.py --pr https://github.com/OWNER/REPO/pull/NUMBER
```

Resolve the script's absolute path from this skill; do not assume the shell is in its directory. It requires authenticated `gh` and Python 3's standard library. Discovery paginates the authenticated user's `pullRequests` connection rather than a capped search; explicit repository restrictions filter that inventory. It also paginates reviews, conversation comments, inline threads and replies, commits, and relevant timeline events. A partial collection is reported explicitly and exits nonzero. Never interpret failed collection as an empty queue.

A match must satisfy all of these conditions:

- The PR is open and authored by the authenticated user.
- It is not approved. Exclude an overall `APPROVED` decision. Where GitHub reports no overall decision, exclude PRs with a current human approval and no current human change request.
- Someone else using a personal GitHub account posted a comment or change request. Include top-level comments, submitted review bodies, and inline replies in unresolved threads. A personal account may post through AI tools; GitHub account type establishes this distinction, not guessed writing style.
- Since the latest such feedback, the author has not posted or edited a reply, submitted or edited a review, or requested/removed a reviewer, dismissed a review, or marked the PR ready. No later branch commit or force push is visible either.

Count edits to eligible human feedback as fresh feedback using the later of creation/submission and `lastEditedAt`. This includes conversation comments, unresolved inline comments and submitted review bodies. An edit can make a PR eligible for analysis; the analyzer determines whether it changes the request or needs action. Edits do not reopen resolved threads or change approval-state ordering.

Bot feedback alone does not qualify. Later bot comments do not clear an unanswered human request. A historical change request does not qualify after the author has acted, even if GitHub still displays `CHANGES_REQUESTED`. An unresolved thread is not proof that the author has done nothing. Ignore pending reviews and resolved inline threads as new feedback.

The collector checks published GitHub activity; local uncommitted work and normal pushes of old, unchanged commits may not be timestamped by GitHub. Do not claim it proves the absence of such work. PR `updatedAt` alone cannot identify author activity because bots and CI also update it.

Show the matching PRs in a compact table, newest feedback first, with **PR / other labels**, **Risk**, **Priority**, and **Latest feedback** columns. Identify PRs as `OWNER/REPO#number` in every table and handoff so equal PR numbers in different repositories remain distinct; key ledger entries by full URL. Use the collector's labels and follow respond's **Show PR labels in the plan** rules. Preserve unavailable PRs as coverage gaps and continue collecting other explicit `--pr` targets if one lookup fails. Stop for an empty queue only when no unfinished entries remain in a resumed run.

## Delegate the queue

Freeze the initial queue and checkpoint it before dispatching children. Follow [Checkpoint and resume](references/run-ledger.md) for the ledger location, schema, write timing and recovery procedure. For newly selected candidates, re-fetch with `--pr` immediately before dispatching analysis; skip candidates that no longer qualify. For unfinished entries on resume, reconcile current PR state against the ledger instead of applying fresh-discovery exclusions. Missing code access blocks only that PR; continue the others and retain the gap.

### Choose the child mechanism

Follow the installed respond skill's **Coordinate subagents** contract for capability discovery, provider/model choice, T3/native dispatch, isolated target-head checkouts and fresh delegated phases with idempotent request IDs. The batch parent owns that dispatch; children execute their assigned analyzer or fixer phase directly rather than starting another batch or recursively invoking this coordination workflow.

Dispatch independent phases asynchronously within the local resource limits below and available capacity, and refill freed slots with ready PR work. Queued tasks remain queued when capacity is exhausted; report that state accurately. A PR waiting for remote CI does not occupy a child slot or block another ready PR. When only asynchronous work remains, persist the ledger and yield for notifications instead of polling.

If no supported child mechanism is available, report the limitation and preserve the unanalyzed queue; do not substitute parent feedback analysis or describe queued work as running.

### Limit local CPU use

Use these defaults unless the user requests different limits; they bound the shared respond skill's concurrency guidance for this batch. Record them in the ledger and include them in every child brief. These are scheduling rules, not an operating-system CPU quota, and may increase elapsed time.

- Run at most **two active child tasks total**, including at most **one fixer**. Two analyzers may run together, or one analyzer alongside one fixer. The parent owns the queue; children do not independently increase concurrency.
- Allow at most **one CPU-heavy local operation across the parent and its children**: builds, test runs, typechecks, dependency installation or local service startup. Reserve that slot before dispatching work that needs it; release it after completion. Use the tool's supported single-worker setting for focused tests where available. Treat a focused verification session and its required services/browser as one reserved operation until the session ends; other heavy work waits.
- Prefer existing **remote CI** for full builds, lint/typechecking and complete test suites when repository policy permits. Locally inspect diffs and run only focused checks that answer a concrete question. Follow required repository checks; serialize required local validation rather than skipping it. CI on this same machine still consumes the local budget. Missing or blocked remote CI is missing evidence, not permission to claim a pass or silently install and run the entire stack locally.
- Reuse stored discovery results and immutable source/diff artifacts keyed by repository and exact commit. Recheck only the target PR before dispatch or action; refresh mutable feedback, labels, review state and checks as required. An unchanged head alone does not prove unchanged feedback. Use scoped source reads for analysis and create working checkouts only for phases that need them through the supported mechanism.
- Keep monitoring notification-driven. Reuse completed evidence within its verified scope; rerun checks when changed code, new failures or unresolved concerns justify it.

### Per-PR analysis phase

Give one read-only analyzer per PR its repository/URL, pinned head/base, triggering feedback, repository and respond instructions, and existing authorization. Every GitHub query must name the target repository or endpoint explicitly; the child's current branch must not determine the PR.

Load instructions, skills, build/test commands, labels and branch policies from each target repository at the reviewed revision. Apply JouleMV-specific rules only to JouleMV; another repository need not have `AGENT.md`, a `frontend/` directory or JouleMV labels. Where instructions are absent, derive conventions from that repository's code, documentation and CI. Keep observed labels verbatim and missing risk/priority labels as **Not labeled**.

Give each child access to the correct repository and exact head through the supported checkout mechanism, verifying repository identity before code inspection or edits. A child checkout inherited from the parent must not be treated as another repository merely because its prompt names that repository. Use target-repository source reads for read-only analysis when appropriate; if the required checkout cannot be supplied, record that PR's blocked phase and continue other repositories. Publication must use the verified PR head repository and branch, including fork heads.

Require respond's full analyzer report: all relevant feedback, current labels, per-comment dispositions and evidence, fix/verification plan, exact-thread reply drafts, original human reviewer identities, observed head/base and blockers. Human feedback selects the PR; bot feedback within it still receives the disposition required by respond. The parent links the PR to this T3 thread when available and records the report. That PR's analysis phase remains read-only while independent authorized PR work can advance.

Before fixes, preserve each human `CHANGES_REQUESTED` review's author login, review ID/URL, reviewed head and associated comment/thread IDs in the ledger. These identify the people to re-request after their feedback is addressed, even if a later push changes review state or the requested-reviewer list is empty. Keep this mapping alongside other original human feedback authors.

### Present the combined analysis plan

Before any combined authorization request, refresh labels and show one summary row per analyzed PR:

| PR / other labels | Risk | Priority | Feedback and suggested plan | Verification / blockers |
| --- | --- | --- | --- | --- |
| [OWNER/REPO#number — title](PR URL); remaining labels or None | Current risk label or Not labeled | Current priority label or Not labeled | Main feedback; proposed Fix / Answer / Defer / No change actions | Focused checks, dependencies, and missing evidence |

Apply the installed respond skill's **Show PR labels in the plan** rules alongside repository-specific requirements. Follow the summary with the full per-comment tables, retaining the PR, risk, and priority columns so the context stays visible beside each proposed solution. Keep those columns in final per-PR outcome tables too. Labels are observed metadata; a critical risk label does not by itself establish a defect or high merge priority.

### Per-PR fix phase

Once that PR's analysis is complete and its fixes are authorized, dispatch its fixer as soon as the resource limits permit; independent analyses need not all finish first. Pass the approved dispositions, exact source head, isolated worktree, file scope, repository validation policy, resource limits and permissions actually granted. Re-fetch the remote head before changes; if it advanced, obtain child analysis of the new head before applying a stale plan.

The fixer applies, validates and, when authorized, commits its changes. Require the worktree/branch, base and exact resulting head SHA, diff/checks/limits, remaining objections and final reply drafts tied to original comments/reviewers. The parent verifies the returned diff/head and clean commit state before publishing. Keep push, replies, review requests, bot triggers and watchers with the parent; send new CI/review issues through child analysis and authorized fixes.

## Authorization and continuation

A bare invocation authorizes the parallel analysis batch only. Finish dispositions and concrete plans for every eligible PR, then present one combined request for any approval required by respond or repository rules. Name and link the instruction requiring approval. Collection and read-only analysis need no separate approval gate.

Apply respond's authorization contract per PR. An authorized complete response workflow covers fixes, publication, replies, original-reviewer requests and babysitting together; a request limited to code fixes does not itself grant that wider scope. Carry existing batch or per-PR authorization forward without per-action approval gates. Explicit edit-only, local-only, no-commit, no-push or no-publication limits remain binding; prepare excluded steps and report them. Permission for one PR does not cover others, and delegation or this skill update grants no new authority. For an authorized batch, advance each PR as its results become ready; an unfinished analysis elsewhere is not a global barrier.

## Complete every authorized response

For each handled PR, complete respond's **Complete authorized fixes and follow through** lifecycle, including replies for dispositions without a push. The parent verifies the remote SHA; replies to every handled original comment/thread and reads back the posted replies; completes the review-request step below; resumes applicable paused bots; and, when monitoring is authorized, invokes [babysit-pr](../babysit-pr/SKILL.md) for the existing PR's current head whether or not this response pushed changes. Preserve adequate existing replies as respond directs.

**Re-request the person who requested changes.** After an authorized fix is published and its replies are posted, the parent must send a GitHub review request to every original human change-request reviewer whose feedback was addressed, as well as the other original human reviewers required by respond. Use the saved logins, not assignees, a comment mention or an inferred code owner. A local fix or pushed commit alone does not satisfy this step.

**Request review before waiting for CI.** For each PR, proceed in this order: publish the fix and perform required PR linking, verify the remote head, post/read back the replies, send/read back the review requests, then monitor CI and yield. Request review immediately while CI is queued or running; neither CI completion nor green checks are prerequisites. State that CI is still pending in the replies when applicable. Process each ready PR independently of other PRs' checks. This timing also applies when resuming an interrupted run; waiting for CI must not postpone an outstanding authorized review request. CI and human review then proceed concurrently, with their results recorded separately. Required CI still governs final verification and completion.

Immediately before requesting, refresh the published head, current requests and later reviews. Preserve an already-pending request; if the reviewer already reviewed this final head, record that review instead of sending a duplicate request, and handle any new actionable feedback separately. Otherwise send the review request within the established authorization, read GitHub's state back, and checkpoint the reviewer, target head, timestamp and observed outcome. Report each reviewer as **Requested**, **Already requested**, **Already reviewed final head**, or **Blocked** with its evidence or reason. A failed or unauthorized request remains unfinished; under explicit local-only/no-publication/no-reviewer-write limits, retain the prepared request and report the limit. Updating this skill does not send any live review requests.

The parent owns T3 link/watch/yield and notification handling for every PR. Dispatch other ready work while CI or reviews run; when a head advances, refresh its ledger and monitor that head. Delegate new substantive diagnosis/fixes, then follow the repository/babysit milestone and stop watchers before handoff as required. Keep CI, bot feedback, unresolved threads and human review separate; approval, merging and deployment require their own authorization.

If explicit authorization excludes a required remote step, prepare its concrete draft and report the limit instead of executing it or calling the lifecycle complete. Preserve blocked PRs in the ledger while other independent PRs advance.

Finish with per-PR status: analyzed/awaiting authorization, completed with exact-head evidence, skipped after recheck, or blocked with the missing action. Keep proposed fixes, posted replies/readbacks, reviewer requests, code verification, CI and human approval distinct. Creating or updating this skill does not itself run the batch.

## Validate skill changes

Run `python3 -B scripts/test_collect_feedback.py` from this skill's directory after changing the collector. The fixtures exercise selection and partial collection without querying GitHub.
