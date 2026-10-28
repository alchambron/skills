---
name: respond
description: Analyze PR feedback in subagents and, when authorized, fix it in parallel, reply to reviewers, request their review again, and babysit CI. Use for PR comments, reviewer requests, or feedback plans.
---

# Respond to PR feedback

Use the PR number supplied by the user, if any. Otherwise, find the PR for the current branch with `gh pr view`. If neither identifies a PR, ask for its number. Read the repository's agent instructions before examining code. In JouleMV, use root `AGENT.md` and, for feedback touching `frontend/`, `frontend/AGENT.md` and `frontend/.claude/skills/INDEX.md`.

A bare `/respond` or `$respond` requests a plan only. Determine the authorized response workflow from the user's request and existing session instructions. When that authorization covers the full workflow, complete verified fixes, publication, original-comment replies, reviewer re-requests and babysitting without asking again. A request limited to code fixes does not itself authorize publication or reviewer-facing actions.

Establish which action categories are covered: code changes, commits/pushes, replies, reviewer requests, CI actions and thread resolution. A single workflow authorization can cover them together; reuse that scope assessment unless the user's instructions or task scope change, rather than adding per-action approval gates. Explicit code-only, local-only, no-commit, no-push or no-post limits remain binding; replies alone do not authorize code changes. Prepare excluded steps as drafts or a concrete handoff, report the blocked steps, and continue independent permitted work. Follow repository approval rules within the established scope.

For any authorized browser work in T3 Code, load and follow [T3 Code Navigator](../../testing-and-quality/browser-testing/SKILL.md#t3-code-navigator): use the built-in `preview_*` browser exclusively, reuse owned tabs, and report unavailable browser evidence as **Blocked** without launching external Chrome or another browser. Include that requirement and the shared skill path in every browser-capable delegated or launched-thread brief. This does not expand this skill's execution or publication scope.

## Coordinate subagents

The main thread coordinates the work. Delegate all substantive feedback analysis to read-only analyzer subagents and all authorized code changes to fixer subagents. The parent collects evidence, reconciles findings, integrates changes, publishes, and babysits the PR. Give each child an explicit **analyzer** or **fixer** role, exact PR/head and base, assigned comments, permitted actions, and expected result. When called from `respond-all`, that batch parent owns this dispatch. A child reading this skill executes its assigned phase directly and returns; it does not invoke the batch workflow or recursively delegate this workflow.

For multiple PRs, the batch coordinator launches analysis concurrently and dispatches authorized fixes as each PR's analysis is ready. Use available concurrency slots; CI waiting on one PR must not block another PR's analysis or fixes. Within one PR, analyze independent feedback groups concurrently. Run fixers concurrently when they own disjoint files in isolated checkouts; assign tightly coupled changes to one fixer. Keep shared or generated-file integration with one owner.

Discover the live provider/model catalog through T3 `orchestrator_capabilities` when available. Use native subagents when they support the selected same-provider model; otherwise use T3 `delegate_task`. In T3, a child needing its own checkout uses `delegate_task` with `isolation:"worktree"`; outside T3, give native children separate checkouts through the supported workspace mechanism. It starts from the parent's committed state, so explicitly instruct it to fetch and check out the exact assigned PR head before work. Retain task IDs and use task status/cancel tools to manage children. Give each checkout one writer; the parent integrates returned commits or patches into the PR's designated checkout. Keep publication with the parent unless a single publishing owner is explicitly assigned. All work remains in the current user thread; child tasks are not new top-level conversations.

For each T3 analysis, fix, or follow-up round, call delegate_task again with the full brief, prior evidence, replies and unresolved objections. Give the round a distinct clientRequestId that remains stable across retries; do not use the backing childThreadId to start another round. Do not create ordinary top-level threads for delegated work.

If no supported child mechanism is available, report the blocked phase and preserve it in the ledger; do not substitute parent analysis or implementation for the requested child work. Independent coordination and publication of already verified child results may continue. Delegation preserves existing authorization; it does not create permission to publish or expand scope.

## Establish the current feedback

1. The parent identifies and links the PR to the current T3 thread when linking tools are available, then gives read-only analyzers the following collection and investigation work. Fetch current head SHA, base SHA, original human reviewers, reviews, general comments, and inline comments. If the user supplied a number, set `PR` to it before running this block; otherwise the first line finds the current branch's PR. For an assigned repository/PR, use the supplied REPO and PR instead of inferring them from the checkout. Qualify GitHub operations with that repository. Project only fields needed for analysis:

   ```bash
   PR=${PR:-$(gh pr view --json number --jq .number)}
   REPO=${REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}
   gh pr view "$PR" --repo "$REPO" --json number,url,title,headRefName,headRefOid,baseRefName,baseRefOid,reviewRequests,labels
   gh api --paginate "repos/$REPO/pulls/$PR/reviews?per_page=100" --jq '.[] | {id, url: .html_url, author: .user.login, state, body, submitted_at, commit_id}'
   gh api --paginate "repos/$REPO/issues/$PR/comments?per_page=100" --jq '.[] | {id, url: .html_url, author: .user.login, body, created_at, updated_at}'
   gh api --paginate "repos/$REPO/pulls/$PR/comments?per_page=100" --jq '.[] | {id, url: .html_url, author: .user.login, body, path, line, original_line, in_reply_to_id, created_at, updated_at, commit_id}'
   gh pr diff "$PR" --repo "$REPO" --name-only
   ```

   Inspect the relevant diff hunks and current code after identifying affected files. The REST inline-comment list does not include thread resolution. Fetch it with:

   ```bash
   gh api graphql --paginate -F owner="${REPO%/*}" -F name="${REPO#*/}" -F pr="$PR" -f query='
   query($owner:String!,$name:String!,$pr:Int!,$endCursor:String) {
     repository(owner:$owner,name:$name) { pullRequest(number:$pr) {
       reviewThreads(first:100,after:$endCursor) {
         pageInfo { hasNextPage endCursor }
         nodes { id isResolved isOutdated path line
           comments(first:100) { pageInfo { hasNextPage endCursor }
             nodes { databaseId url author { login } createdAt body } } }
       }
     } }
   }'
   ```

   If a thread's `comments.pageInfo.hasNextPage` is true, set `THREAD_ID` to that thread's `id` and fetch its replies:

   ```bash
   THREAD_ID=${THREAD_ID:?Set THREAD_ID to a review thread node ID}
   gh api graphql --paginate -F thread="$THREAD_ID" -f query='
   query($thread:ID!,$endCursor:String) {
     node(id:$thread) { ... on PullRequestReviewThread {
       comments(first:100,after:$endCursor) {
         pageInfo { hasNextPage endCursor }
         nodes { databaseId url author { login } createdAt body }
       }
     } }
   }'
   ```
2. Check review-thread resolution and later replies. Separate open human requests from resolved threads, superseded requests, bot suggestions, and questions already answered. Do not count replies as new independent findings.
3. Read the PR's merge-base diff and the relevant current code. Verify whether each requested change is still needed; a comment may refer to an older commit. Do not dismiss a reviewer's reported behavior merely because a test or static reading does not reproduce it; mark it **Needs verification** and name the missing check.

Each analyzer returns the observed head/base SHAs; every original comment's stable ID, thread ID and URL; original human reviewer logins; dispositions with source/test evidence; proposed fixes and focused verification; and draft replies. Capture reviewers from the original comments and submitted reviews before publication, even when the current requested-reviewer list is empty. The parent reconciles overlapping results into one complete per-PR ledger before dispatching that PR's fixes; other PRs can progress independently. Refresh affected evidence if the PR head advances.

## Show PR labels in the plan

Refresh the PR's GitHub labels before presenting an analysis plan or final outcome table; labels can change without a new head commit. Save their exact names and collection time in the per-PR ledger and include them in analyzer handoffs. If a refresh fails, mark the labels **Unavailable** or explicitly identify a timestamped older snapshot.

Show PR-level **Risk** and **Priority** in separate columns beside the feedback and proposed solution. In JouleMV, display the exact `risk:limited`, `risk:disruptive`, or `risk:critical` label and `priority:low`, `priority:medium`, or `priority:high` label. Risk describes deployment consequences; priority describes merge urgency. Keep both separate from each comment's importance and value. Preserve labels from other repositories verbatim, including legacy priority labels such as `high` or `Urgent Ticket`; identify a legacy label rather than silently translating it.

Show all remaining labels in **PR / other labels**, using `None` when there are none. Use **Not labeled** when a risk or priority label is absent; absence does not imply limited risk or low priority. If several labels conflict in one category, show them all and mark **Conflicting labels**. Any proposed reassessment belongs explicitly in the plan, separate from the observed labels. Displaying labels does not authorize changing them or running a labeling workflow.

## Plan each comment

Present every review comment in a compact table, including comments that are resolved, superseded, or not actionable. Keep one row per original comment or thread; fold replies into that row. Link each comment and briefly state its current status in the Comment cell. Use these columns:

| PR / other labels | Risk | Priority | Comment | Important? | Valuable? | Solution for this comment |
| --- | --- | --- | --- | --- | --- | --- |
| [#number](PR URL); remaining labels or None | Current risk label or Not labeled | Current priority label or Not labeled | [Reviewer: concise summary](comment URL) (open/resolved/already addressed) | Important / Not important — brief reason | Valuable / Not valuable — brief reason | Fix / Answer / Defer / No change: specific action and how to verify it. |

Judge **importance** by whether the issue needs action before merge, especially for correctness, security, data loss, or a meaningful user regression. Judge **value** by whether the feedback is valid and improves the PR, even when it is not merge-blocking. If evidence is insufficient, say “Needs verification” in the relevant cell and name the check in the solution. Do not equate a resolved thread with a verified fix.

Give every comment a disposition: **Fix**, **Answer**, **Defer**, or **No change**, with a reason. After the table, give a short ordered implementation plan and verification steps for the comments that need work. Order blockers first, group by area (backend, `frontend/`, or shared/config), and note dependencies, conflicting feedback, broader patterns, and accompanying tests, docs, translations, or config. When a solution needs more than a few sentences, add a numbered explanation below keyed to that row. Draft a reviewer reply for comments needing an answer, deferral, or explanation. State which items are already addressed and which need a reviewer answer. Avoid claiming a comment is resolved or a fix is deployed solely from local code or tests.

## Complete authorized fixes and follow through

1. Dispatch fixer subagents with the agreed dispositions, exact starting head, file ownership, relevant repository instructions, and verification scope. Each returns its commit or patch, changed files, focused check results, remaining risks, and proposed replies. Integrate all changes, check their combined behavior, then refresh the remote head before committing and pushing through the designated publisher. Incorporate concurrent changes before publication; do not overwrite another contributor's updates. Return integration conflicts to a fixer instead of taking over implementation in the parent.
2. Within the established authorization scope, **reply to every handled original comment or thread** after publishing a fix or completing an Answer, Defer or No change disposition, with its outcome, relevant commit, verification, and any remaining limitation. Include reasoned answers for deferred and no-change items. Post inline replies in their original threads; reply to general comments or review summaries in the PR conversation with a link to the original. Preserve existing adequate replies rather than posting duplicates. Read each posted reply back and record its URL and observed thread state. Resolve threads only within the user's authorization and after verification; a reply is still required when a fix is implemented.
3. **Request review again from the original human reviewers** whose feedback was addressed, using GitHub review requests rather than issue assignees. Re-fetch current requests and later reviews first: preserve an existing request, and avoid re-requesting someone who already reviewed the final head. Read the resulting review-request state back. Record failures or unavailable reviewers explicitly. When a paused bot must review the new head, trigger its supported review command and distinguish bot review from human approval.
4. **Babysit the published PR**, using the available `babysit-pr` skill and current-head CI/review evidence. Dispatch currently ready independent child work before T3 watch/yield so those children keep working while the parent waits. With T3 tools, register `watch_pull_request` for each published PR and end the turn once other ready work is dispatched or completed; T3 wakes the current thread on changes. Do not poll an app-owned watcher. Watcher notifications return to the parent, which delegates new substantive analysis and fixes to children while other PRs continue. Continue until required checks and actionable feedback are handled, or report a concrete external blocker. Keep pending/skipped checks, human approval, and merge state distinct. Before handing the PR back to the user, call `unwatch_pull_request` and verify all worked PRs are linked. This workflow does not authorize merging.

## Record outcomes after authorized work

Revisit every row after implementing or answering feedback. Report what actually happened, including items deferred or left unchanged:

| PR / other labels | Risk | Priority | Comment | Outcome and evidence | Reviewer reply and thread status |
| --- | --- | --- | --- | --- | --- |
| PR link; remaining labels or None | Current risk label or Not labeled | Current priority label or Not labeled | Link to the original comment | Fixed / answered / deferred / no change / blocked; cite code, focused verification, and commit when available | Quote or link the posted reply, or label it **Draft**; report the observed thread status. |

Keep proposed work separate from completed work. If a fix was not verified, state the remaining check. If a reply was posted or a thread was marked resolved, read it back from GitHub before reporting that action as complete. Every original comment must have a disposition and an outcome; do not mark a fix complete from a plan alone.

Include the PR URL and final published head, posted reply URLs, observed thread states, original-reviewer request outcomes, current-head CI status, separate human and bot review states, merge/conflict state, active watches or handoff, and any human/external blocker in the final ledger. A pushed fix alone does not complete the authorized response workflow.
