# Fixer phase

Own exactly one PR's approved code change and authorized publication in an isolated checkout. Run the work in this context. T3 linking and watching belong to the parent. Load this reference and target-repository rules; use [report-schema.md](report-schema.md) for a follow-up report.

1. **Verify the assigned head.** Fetch the head branch from its actual repository, forks included, check it out and verify `HEAD`. A moved remote head returns `stale`; avoid overwriting concurrent commits.
2. **Apply approved rows only.** Honor file scope, target-revision instructions and explicit authorization categories. Keep excluded actions as drafts. You hold the run's one fixer/CPU-heavy slot.
3. **Validate focused.** State the regression question, then run affected checks with one worker. Full validation follows repository CI policy. Use the tool's supported long-command yield, then follow its returned wait instruction; a script taking time does not justify frequent polling.
4. **Commit and push when authorized.** Recheck the remote, integrate safely or return stale, push, and read back the exact remote SHA.
5. **Write `PREFIX-publish.json` directly.** `PREFIX=RUN_DIR/prs/OWNER/REPO/pr-NUMBER`. Include `url`, `expectedHeadSha`, original `analyzedAt`, unchanged `humanFeedbackSnapshot`, `replies` and `reviewers`. Replies contain the row ID as `key`, reply kind/target, outcome, commit link, focused evidence and any pending CI. Include original human reviewers whose feedback was addressed, including change-request reviewers. `publish.py --help` gives the plan shape.
6. **Publish when authorized.** Run `publish.py PREFIX-publish.json`. It verifies feedback freshness, posts idempotently, reads back replies/requests and records a namespaced journal. Exit 3 is stale: return its reason. Re-analysis uses fresh evidence and revised drafts; a new snapshot alone must not bless old drafts. Legacy reports can reconstruct snapshots only from their original complete raw evidence.
7. **Checkpoint once.** Record report/journal paths, head, focused evidence and remaining actions through `ledger.py apply`. Return `PR URL, head SHA, pushed yes|no, publish complete|blocked|stale|not authorized, journal PATH, remaining objections or none`.

Finish with verified authorized actions or a concrete handoff for every outstanding step. Publication is distinct from passing CI and human approval.

## Follow-up rounds

After CI settles, address the collected new failures and actionable human/bot feedback together. Write `PREFIX-followup-ROUND.json` in the report schema before implementing covered changes. New scope needing a decision returns `needs authorization: ROW IDS`. Preserve the batch's two-round limit; a third round belongs in a separate handoff.

## Browser checks

When browser work is needed in T3 Code, follow [T3 Code Navigator](../../../testing-and-quality/browser-testing/SKILL.md#t3-code-navigator), use the supported shared browser and report unavailable evidence as blocked. Use target-project setup and credentials.
