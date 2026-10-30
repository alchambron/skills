# Analyzer phase

You are the read-only **analyzer** for exactly one PR. Run this phase yourself and return; start no subagents and no batch workflow. Your only writes are files inside the assigned run directory: no checkout or ref changes, GitHub writes, installs, builds, tests or services.

Done when every untouched human item named in the brief maps to a row, `coverage` accounts for every other item, and `pr-NUMBER-analysis.json` is written.

## Steps

1. **Pin the target.** Name the repository in every query (`--repo OWNER/REPO` or an explicit API path); your checkout's branch is not the PR. If the live head differs from the brief's head, analyze the live head and record the scope change in `blockers`. Read source at the exact SHA with `git show SHA:path` (fetch missing objects with `git fetch --no-write-fetch-head origin SHA`) or the contents API pinned to the SHA. For a repository other than your checkout's, use the API only.
2. **Load target rules at the head SHA.** Read the repository's agent instructions (JouleMV: root `AGENT.md`; add `frontend/AGENT.md` and `frontend/.claude/skills/INDEX.md` only for feedback touching `frontend/`). Follow their exploration rules, such as JouleMV's Feature Map, when a row needs code beyond the commented lines. Elsewhere, derive conventions from the repository's code, docs and CI.
3. **Collect feedback to disk** with [the commands below](#collect-feedback). Inspect it through `jq` projections with truncated bodies; raw JSON stays in files.
4. **Build rows** within [row scope](#row-scope), each with the [row fields](#report-schema).
5. **Read current-head checks once**: passed, failed, pending, skipped or missing. Open a job log only when a failing check bears on a row.
6. **Write `RUN_DIR/pr-NUMBER-analysis.json`** in one `json.dump`. The parent renders Markdown from it with `render_report.py`, so a single JSON file is the whole report. When the brief names a ledger, also record each original human reviewer there in one call: `python3 ~/.codex/skills/respond-all/scripts/ledger.py apply RUN_DIR` with one `{"pr": URL, "reviewer": {...}}` operation per reviewer (the `reviewers` fields below).
7. **Return one line:** `PR URL, head SHA, rows N (Fix a / Answer b / Defer c / No change d), report PATH, blockers or none`.

## Row scope

- **Untouched human feedback**, the items the brief lists or points to (posted after the author's last action): one full row per original comment, review body or inline thread, replies folded in.
- **Other open human threads**: one row each with current status. When an existing author reply already answers it, say so and set `publish: false`.
- **Bots**: a full row for an unresolved inline thread on the current head, or a comment posted after the author's last action. Everything older from a bot collapses into one summary row per bot.
- **The author's own comments**: evidence inside other rows (an existing reply), never rows themselves.
- **Resolved threads**: counted in `coverage`. A resolved thread is not proof of a fix.

Judge each row on two separate axes. **Importance**: does it need action before merge (correctness, security, data loss, meaningful regression)? **Value**: is the feedback valid and does it improve the PR, even if not merge-blocking? Give one **disposition**: Fix, Answer, Defer or No change, with the reason. Reported runtime behavior you cannot reproduce statically is **Needs verification**: name the missing check rather than dismissing it. Draft a reply for every row that needs an answer, deferral, explanation or fix outcome; a draft for a review body or conversation comment links the original.

## Report schema

```json
{
  "repository": "OWNER/REPO", "url": "PR URL", "number": 0, "title": "",
  "headSha": "", "baseSha": "", "liveTargetSha": "", "analyzedAt": "ISO time feedback was collected",
  "labels": ["exact names"], "labelsCollectedAt": "",
  "summary": "one or two sentences", "needsCodeChange": true,
  "plan": ["ordered steps: blockers first, grouped backend / frontend / shared"],
  "verification": ["focused checks"], "blockers": [],
  "reviewers": [{"login": "", "reviewId": 0, "reviewUrl": "", "state": "", "reviewedHead": "",
                 "commentIds": [], "changeRequest": false, "request": true}],
  "comments": [{"id": "", "url": "", "threadId": "", "reviewer": "", "status": "open|resolved|outdated|answered",
                "summary": "", "disposition": "Fix|Answer|Defer|No change", "importance": "", "value": "",
                "solution": "", "evidence": ["path:line at SHA"], "verification": "",
                "draftReply": "", "replyKind": "inline|conversation", "inReplyTo": 0, "publish": true}],
  "checks": {"head": "", "state": "", "failing": [], "pending": []},
  "coverage": {"humanUntouched": 0, "humanOther": 0, "botRows": 0, "botSummarized": 0,
               "authorComments": 0, "resolvedThreads": 0},
  "rawEvidence": "RUN_DIR/pr-NUMBER-raw"
}
```

- `id` is the original comment's database ID, or `review-ID` for a review body. An inline row sets `inReplyTo` to its thread's first comment ID.
- `reviewers` lists every original human reviewer. `changeRequest: true` marks a `CHANGES_REQUESTED` author: these logins are re-requested after their feedback is addressed even if GitHub's state later changes. `request: false` excludes a reviewer from re-request.
- `analyzedAt` is load-bearing: `publish.py` treats any human comment after it as stale.
- Label names stay verbatim; the renderer derives Risk and Priority.

## Collect feedback

```bash
REPO=OWNER/REPO PR=NUMBER RAW="$RUN_DIR/pr-$PR-raw"; mkdir -p "$RAW"
gh pr view "$PR" --repo "$REPO" --json number,url,title,headRefName,headRefOid,baseRefName,baseRefOid,reviewRequests,labels > "$RAW/pr.json"
gh api --paginate --slurp "repos/$REPO/pulls/$PR/reviews?per_page=100" > "$RAW/reviews.json"
gh api --paginate --slurp "repos/$REPO/issues/$PR/comments?per_page=100" > "$RAW/issue-comments.json"
gh api --paginate --slurp "repos/$REPO/pulls/$PR/comments?per_page=100" > "$RAW/review-comments.json"
gh pr diff "$PR" --repo "$REPO" --name-only > "$RAW/files.txt"
gh api graphql --paginate --slurp -F owner="${REPO%/*}" -F name="${REPO#*/}" -F pr="$PR" -f query='
query($owner:String!,$name:String!,$pr:Int!,$endCursor:String) {
  repository(owner:$owner,name:$name) { pullRequest(number:$pr) {
    reviewThreads(first:100,after:$endCursor) {
      pageInfo { hasNextPage endCursor }
      nodes { id isResolved isOutdated path line
        comments(first:100) { pageInfo { hasNextPage endCursor }
          nodes { databaseId url author { login } createdAt body } } }
    } } }
}' > "$RAW/threads.json"
# Skim: id, author, type, date, first 160 characters.
jq -r '.[][] | [.id, .user.login, .user.type, (.submitted_at // .created_at), ((.body // "") | gsub("\\s+"; " ") | .[0:160])] | @tsv' "$RAW/reviews.json" "$RAW/issue-comments.json" "$RAW/review-comments.json"
```

When a thread's `comments.pageInfo.hasNextPage` is true, page its replies with `node(id: THREAD_ID) { ... on PullRequestReviewThread { comments(first:100, after:$endCursor) { ... } } }` into another file in `$RAW`.
