# Changelog

## 1.5.0

- Added the WebSynco React Native checkout to the built-in repository map.
- Marked eligible PRs with missing, invalid, or unmapped checkouts as `needs_attention`. Plan output and the dated digest now carry a reason and a one-line ping with the PR link and the required checkout fix.

## 1.4.0

### Multi-repository support
- Replaced single-repo JouleMV pull-list sweeps with cross-repository GitHub search (`is:pr is:open draft:false user-review-requested:alchambron`), while preserving direct single-PR event reads via `plan --repo OWNER/REPO --number N`.
- Keyed all queue state (`jobs`, `prs`, `skips`) and digest entries by `owner/repo#number` (`pr_key`), never by PR number alone.
- Added idempotent v1 → v2 `state.json` migration (`migrate_state` / `queue.py migrate`) that preserves all legacy JouleMV history, assigns `repository: "EnerZam/JouleMV"` and `pr_key` to legacy entries, migrates bare-number `prs` keys, and backfills `ping`/`pinged_at` on completed legacy jobs.
- Added local checkout resolution from `--repos-config` / `$REVIEW_QUEUE_REPOS_CONFIG` / `<state>/repos.json`, `--t3-projects` / `$REVIEW_QUEUE_T3_PROJECTS`, or built-in Air defaults (`EnerZam/JouleMV`, `EnerZam/WebSynco_React_Front`, `EnerZam/WebSynco`). Unmapped or missing checkouts are reported as explicit skips without cloning.
- Documented repository-scoped run directory layout (`runs/<owner>/<repo>/pr-<number>-<head8>-<rand>/`) while keeping legacy JouleMV paths readable through stored absolute paths.
- Updated dated digest schema to `schema_version: 2` (`repositories` list, per-entry `repository` + `pr_key`, repo-qualified pings, `review_url`, `summary_path`, and `pr_key`-scoped ping deduplication).
- Preserved shared concurrency caps (`static: 2`, `live: 1` via `respond-queue/scripts/shared_slots.py` 1.4.0) across all repositories combined while scoping duplicate-PR checks by `(repository, number)`.

### No silent skips
- Fixed the JouleMV `#1892` bug: a PR is skipped as `awaiting Alexis decision at current pins` only when a verified finished review exists in `previousRun`'s ledger at that exact `(repository, number, head_sha, base_sha)` **and** Alexis was pinged for it in `review-queue`; otherwise it remains eligible and is selected.
- Every skipped candidate receives a concrete reason, is reported in `plan` stdout JSON and the selection file, and is persisted once (deduplicated) into `state.json` and the dated digest.
- Enforced same-head re-request handling: `finish --status completed` rejects silent `publication: reused` on `rerequested` / `discussion` jobs unless `--ping-alexis --reason "..."` is supplied to emit an explicit Alexis decision ping; otherwise a fresh review must be published.

### Inline T3 thread summary
- Added `queue.py summary (--token TOKEN | --run-dir DIR)` to emit `review-all`'s rendered inline Markdown summary (`report/summary.md`: summary table, verdict, `file:line` findings, live scenarios, GitHub review links, and HTML report links) so the coordinator ends its T3 thread with the review result visible inline alongside the saved HTML reports.
