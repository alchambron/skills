# Fixer phase

You are the **fixer** for exactly one PR. You own its code change and, when the brief grants them, its push, replies and reviewer requests. Run this phase yourself and return; start no subagents. T3 linking and PR watching stay with the parent.

Done when your commit is verified (on the remote when push is authorized) and `publish.py`'s journal reports `complete`, or each step the authorization excludes is left as a prepared artifact.

## Steps

1. **Check out the exact head** in your isolated worktree: fetch the PR's head branch from its head repository (forks included), check it out, and verify `HEAD` equals the brief's head SHA. If the remote head moved, return `stale: head moved to SHA`.
2. **Apply the approved rows only**, within the brief's file scope, following the target repository's instructions at that revision. Tightly coupled changes stay together; leave unapproved rows untouched.
3. **Validate focused.** Run the repository's focused checks for the changed area with a single worker (`--maxWorkers=1`, `-T 1` or the tool's equivalent); you hold the run's one CPU-heavy slot while they run. Full suites, lint and typecheck belong to remote CI when policy permits; serialize any required local check rather than skipping it. Give long commands `yield_time_ms` of at least 60000, or run them in the background, and check back at that cadence.
4. **Commit** when authorized, following the repository's commit conventions.
5. **Push** when authorized: re-fetch the remote branch; integrate concurrent commits without overwriting them, or return `stale`; push; verify the remote SHA equals your commit.
6. **Write `RUN_DIR/pr-NUMBER-publish.json`:** `url`, `expectedHeadSha` (the pushed SHA), `analyzedAt` (copied from the analysis report), `replies` (one per handled row: outcome, commit link, focused verification, "CI is still running" when pending, remaining limits; `key` is the row `id`), and `reviewers` (original human reviewers whose feedback was addressed, always including change-request reviewers). The schema is in `publish.py --help`.
7. **Publish** when replies and reviewer requests are authorized: `python3 ~/.codex/skills/respond/scripts/publish.py RUN_DIR/pr-NUMBER-publish.json`. Exit 3 means new feedback or a moved head: return `stale` with its reason instead of retrying old drafts.
8. **Return one line:** `PR URL, head SHA, pushed yes|no, publish complete|blocked|stale|not authorized, journal PATH, remaining objections or none`.

## Follow-up rounds

A follow-up brief names new CI failures, review comments or bot comments instead of approved rows. Diagnose them first and write `RUN_DIR/pr-NUMBER-followup-ROUND.json` in the analyzer report schema ([analyzer.md](analyzer.md#report-schema)). Then fix what the established authorization covers, and return anything that needs a new decision as `needs authorization: ROW IDS`.

## Browser checks

For browser work in T3 Code, follow [T3 Code Navigator](../../../testing-and-quality/browser-testing/SKILL.md#t3-code-navigator): use the built-in `preview_*` browser only, reuse owned tabs, and report unavailable browser evidence as **Blocked**. Use the target project's own URLs, setup and credentials.
