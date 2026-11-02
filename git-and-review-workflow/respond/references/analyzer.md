# Analyzer phase

Analyze exactly one PR, read-only, in this context. Write only assigned run artifacts. Return one line after every untouched human item has a row, the inventory accounts for the other original items, and the report is saved. The parent owns orchestration; this phase needs this reference, the [report schema](report-schema.md), and applicable target-repository rules.

1. **Pin scope.** Use the explicit repository and head SHA, including for forks. If the live head differs, record the change before analysis. Read source with `git show SHA:path` or the contents API pinned to that SHA. Fetch missing objects without changing checkout or refs; use the API for another repository.
2. **Load relevant rules.** Read the target revision's agent instructions. JouleMV requires root `AGENT.md`, Feature Map discovery for affected behavior, and frontend instructions/index only when the feedback touches the frontend. Repository collection/validation rules apply; compact row scope below is the default. An explicit user request for exhaustive rows adds an appendix rather than expanding the parent's context.
3. **Collect to disk.** Set `analyzedAt` before collection. Use `PR_DIR=RUN_DIR/prs/OWNER/REPO`, `PREFIX=PR_DIR/pr-NUMBER` and `RAW=PREFIX-raw`; create the directories. Collect all pages of REST reviews, issue comments and review comments into `reviews.json`, `issue-comments.json` and `review-comments.json`. Also collect PR metadata, files, and GraphQL `reviewThreads` with `isResolved`, `isOutdated`, path/line and comments. Paginate both the threads connection and nested comments. Retain raw bodies in files; inspect projected IDs, authors, dates, links and short excerpts.
4. **Capture freshness.** Run `feedback_snapshot.py --url PR_URL --viewer AUTHOR_LOGIN --raw RAW`. Save its object as `humanFeedbackSnapshot`; the schema describes how to preserve it. Derive it from the collected evidence, never a later live refresh.
5. **Build scoped rows** and a coverage inventory, using [the schema](report-schema.md). Write the JSON report directly as `PREFIX-analysis.json`; shared scripts handle rendering. A row needing more source gets exact-head investigation, not a repository-wide audit.
6. **Read checks once.** Distinguish passed, failed, pending, skipped and missing at the exact head. Inspect a log only when a failed check bears on a row. This phase runs no tests, builds, installs or services.
7. **Checkpoint and return.** In one `ledger.py apply`, record the analysis path and original human reviewers. Return `PR URL, head SHA, rows N (Fix a / Answer b / Defer c / No change d), report PATH, blockers or none`.

## Row scope

- Untouched human feedback named in the brief: one row per original item/thread, replies folded in.
- Other open human threads: one row each; already answered items have `publish: false`.
- Bots: full rows for actionable unresolved current-head findings or new actionable findings. Older/context-only feedback goes in the coverage inventory, optionally one non-publishing summary per bot. Recency alone does not make a bot walkthrough actionable.
- Author comments: evidence within another row, otherwise inventory entries.
- Resolved, outdated and superseded history: inventory entries with reasons; a resolution flag alone is not verification.

Every item remains accounted for. Ambiguous substantive feedback gets a row. An explicit full-history request renders inventory rows in an appendix; it does not authorize fresh replies to history. Local rules requiring every item to be accounted for are satisfied through rows plus inventory; if an explicit rule requires a full table, write that appendix on disk and keep the parent return compact.

Judge importance (action before merge) separately from value (valid improvement). Give each row Fix, Answer, Defer or No change, with a concrete reason and verification. Unverified reported behavior is **Needs verification**, with the missing check named. Draft replies for actual addressed feedback; summary and inventory entries are non-publishing.

## Collection commands

```bash
gh pr view "$PR" --repo "$REPO" --json number,url,title,author,headRefName,headRefOid,baseRefName,baseRefOid,reviewRequests,labels > "$RAW/pr.json"
gh api --paginate --slurp "repos/$REPO/pulls/$PR/reviews?per_page=100" > "$RAW/reviews.json"
gh api --paginate --slurp "repos/$REPO/issues/$PR/comments?per_page=100" > "$RAW/issue-comments.json"
gh api --paginate --slurp "repos/$REPO/pulls/$PR/comments?per_page=100" > "$RAW/review-comments.json"
gh pr diff "$PR" --repo "$REPO" --name-only > "$RAW/files.txt"
```

GraphQL: query `repository(owner:OWNER,name:REPO).pullRequest(number:PR).reviewThreads(first:100,after:CURSOR)` with `pageInfo {hasNextPage endCursor}` and thread `id isResolved isOutdated path line`. Include `comments(first:100) {pageInfo {hasNextPage endCursor} nodes {databaseId url author {login} createdAt updatedAt body}}`. Continue each nested connection through `node(id:THREAD_ID) { ... on PullRequestReviewThread { comments(first:100,after:CURSOR) { ... } } }` until complete. Save pages under RAW; a failed page is a coverage blocker, not an empty result.
