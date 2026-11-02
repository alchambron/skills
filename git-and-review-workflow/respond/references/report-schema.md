# Report schema

The analyzer and follow-up fixer write one JSON object directly, using a file write or JSON passed on stdin. The shared scripts perform validation, publication and Markdown rendering; report construction needs no per-PR builder program.

Use `PR_DIR=RUN_DIR/prs/OWNER/REPO` and `PREFIX=PR_DIR/pr-NUMBER` for reports, raw evidence and publication plans. Create the directory first. Repository identity is part of every artifact path, including in mixed-repository batches. Keep each report's `url`, `repository` and `number` consistent.

```json
{
  "repository": "OWNER/REPO", "url": "PR URL", "number": 0, "title": "",
  "headSha": "", "baseSha": "", "liveTargetSha": "",
  "analyzedAt": "ISO UTC time before feedback collection began",
  "humanFeedbackSnapshot": {"copy": "the exact object emitted by feedback_snapshot.py"},
  "labels": [], "labelsCollectedAt": "",
  "summary": "one or two sentences", "needsCodeChange": true,
  "plan": [], "verification": [], "blockers": [],
  "reviewers": [{"login": "", "reviewId": 0, "reviewUrl": "", "state": "", "reviewedHead": "",
                 "commentIds": [], "changeRequest": false, "request": true}],
  "comments": [{"id": "", "url": "", "threadId": "", "reviewer": "", "status": "open",
                "summary": "", "disposition": "Fix|Answer|Defer|No change", "importance": "", "value": "",
                "solution": "", "evidence": [], "verification": "",
                "draftReply": "", "replyKind": "inline|conversation", "inReplyTo": 0, "publish": true}],
  "checks": {"head": "", "state": "", "failing": [], "pending": []},
  "coverage": {"humanUntouched": 0, "humanOther": 0, "botRows": 0, "botSummarized": 0,
               "authorComments": 0, "resolvedThreads": 0},
  "coverageInventory": "PREFIX-coverage.json",
  "includeCoverageAppendix": false,
  "rawEvidence": "PREFIX-raw"
}
```

`id` is the original database ID, or `review-ID` for a review body. An inline reply targets the first comment's numeric ID. Fold replies into the original row. A coverage inventory lists each omitted original item as `{id, url, category, reason}`; it proves collection coverage without expanding the analysis. Set `includeCoverageAppendix: true` for an explicit exhaustive-table request; the renderer loads that inventory beside the report. Use `publish: false` for summary/context-only rows and existing answers.

Record every original human reviewer. `changeRequest: true` identifies a `CHANGES_REQUESTED` reviewer even if GitHub's later state changes. `request: false` excludes an unaddressed reviewer from a new request. Reviewer requests require the established authorization.

`humanFeedbackSnapshot` comes from the original collected REST evidence, with the PR author's login as `--viewer`:

```bash
python3 ~/.codex/skills/respond/scripts/feedback_snapshot.py --url "$PR_URL" --viewer "$AUTHOR" --raw "$RAW"
```

Copy the emitted object unchanged into the analysis and all derived publication plans. It detects human edits, including review-body edits without an edit timestamp. Missing original evidence requires fresh analysis and renewed drafts; capturing live feedback to bless an old plan would erase the freshness guarantee.

Keep labels verbatim; rendering derives Risk and Priority. Keep local tests, current-head CI, human approval, bot review, mergeability and deployment as separate evidence. A new human item or edited body makes an old publication plan stale.
