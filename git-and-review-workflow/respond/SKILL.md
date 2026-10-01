---
name: respond
description: Analyze or address review feedback on a GitHub pull request. Use when asked to respond to PR comments, assess reviewer requests, or plan fixes for review feedback.
---

# Respond to PR feedback

Use the PR number supplied by the user, if any. Otherwise, find the PR for the current branch with `gh pr view`. If neither identifies a PR, ask for its number. Read the repository's agent instructions before examining code. In JouleMV, use root `AGENT.md` and, for feedback touching `frontend/`, `frontend/AGENT.md` and `frontend/.claude/skills/INDEX.md`.

A bare `/respond` or `$respond` requests a plan only. If the user has already authorized fixes or reviewer replies, complete that work after planning and verification. Follow repository approval rules. Push, post replies, or change review state only within the user's authorization.

## Establish the current feedback

1. Fetch PR metadata, reviews, general comments, and inline comments from the current repository. If the user supplied a number, set `PR` to it before running this block; otherwise the first line finds the current branch's PR. Project only fields needed for analysis:

   ```bash
   PR=${PR:-$(gh pr view --json number --jq .number)}
   REPO=$(gh repo view --json nameWithOwner --jq .nameWithOwner)
   gh pr view "$PR" --json number,url,title,headRefName,baseRefName
   gh api --paginate "repos/$REPO/pulls/$PR/reviews?per_page=100" --jq '.[] | {id, url: .html_url, author: .user.login, state, body, submitted_at, commit_id}'
   gh api --paginate "repos/$REPO/issues/$PR/comments?per_page=100" --jq '.[] | {id, url: .html_url, author: .user.login, body, created_at, updated_at}'
   gh api --paginate "repos/$REPO/pulls/$PR/comments?per_page=100" --jq '.[] | {id, url: .html_url, author: .user.login, body, path, line, original_line, in_reply_to_id, created_at, updated_at, commit_id}'
   gh pr diff "$PR" --name-only
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

## Plan each comment

Present every review comment in a compact table, including comments that are resolved, superseded, or not actionable. Keep one row per original comment or thread; fold replies into that row. Link each comment and briefly state its current status in the Comment cell. Use these columns:

| Comment | Important? | Valuable? | Solution for this comment |
| --- | --- | --- | --- |
| [Reviewer: concise summary](comment URL) (open/resolved/already addressed) | Important / Not important — brief reason | Valuable / Not valuable — brief reason | Fix / Answer / Defer / No change: specific action and how to verify it. |

Judge **importance** by whether the issue needs action before merge, especially for correctness, security, data loss, or a meaningful user regression. Judge **value** by whether the feedback is valid and improves the PR, even when it is not merge-blocking. If evidence is insufficient, say “Needs verification” in the relevant cell and name the check in the solution. Do not equate a resolved thread with a verified fix.

Give every comment a disposition: **Fix**, **Answer**, **Defer**, or **No change**, with a reason. After the table, give a short ordered implementation plan and verification steps for the comments that need work. Order blockers first, group by area (backend, `frontend/`, or shared/config), and note dependencies, conflicting feedback, broader patterns, and accompanying tests, docs, translations, or config. When a solution needs more than a few sentences, add a numbered explanation below keyed to that row. Draft a reviewer reply for comments needing an answer, deferral, or explanation. State which items are already addressed and which need a reviewer answer. Avoid claiming a comment is resolved or a fix is deployed solely from local code or tests.

## Record outcomes after authorized work

Revisit every row after implementing or answering feedback. Report what actually happened, including items deferred or left unchanged:

| Comment | Outcome and evidence | Reviewer reply and thread status |
| --- | --- | --- |
| Link to the original comment | Fixed / answered / deferred / no change / blocked; cite code, focused verification, and commit when available | Quote or link the posted reply, or label it **Draft**; report the observed thread status. |

Keep proposed work separate from completed work. If a fix was not verified, state the remaining check. If a reply was posted or a thread was marked resolved, read it back from GitHub before reporting that action as complete. Every original comment must have a disposition and an outcome; do not mark a fix complete from a plan alone.
