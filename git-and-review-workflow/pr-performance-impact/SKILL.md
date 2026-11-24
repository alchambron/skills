---
name: pr-performance-impact
description: Measure and score a pull request's overall app performance impact (%) and code structure (/10) using bundled static, bundle, SQL, API, and Playwright browser tools, presented as a short low-text scorecard. Use when asked for the performance impact of a PR or invoked as $pr-performance-impact or /pr-performance-impact.
---

# PR Performance Impact

Produce a short, read-only, tool-backed scorecard for one pull request: estimated overall app performance impact (`%`) and code structure score (`/10`). Keep the final response compact and grounded in measured output from the bundled scripts.

## Bundled precision tools (`scripts/`)

Run the unified orchestrator from this skill's directory against the target repository (read-only; never modifies your checkout):

```bash
python3 <skill-dir>/scripts/analyze_pr.py <PR_NUMBER_OR_URL> --repo . --check-updates --json
```

Omit `<PR_NUMBER_OR_URL>` to target the current branch's PR. Optional flags:
- `--check-updates` — reads the local `VERSION` file (`MAJOR.MINOR.PATCH`) and checks `alchambron/skills/skill-versions.json` on `main` via `gh api` (read-only, non-blocking). If a newer release exists, mention the update notice in 1 short line without installing anything.
- `--pr-url <url> --base-url <url> --routes /,/dashboard --login` — run live Playwright Web Vitals benchmarking on specific routes (also reads `PR_PERF_BASE_WEB_URL`, `PR_PERF_PREVIEW_HOST_REGEX`).
- `--pr-api <url> --base-api <url> --endpoints /api/...` — run live HTTP latency benchmarking (`median` / `p95` ms and payload bytes; also reads `PR_PERF_BASE_API_URL` and optional `PR_PERF_AUTH_TOKEN`).
- `--no-bundle` / `--no-browser` / `--no-api` — skip individual tools when not needed.

What `analyze_pr.py` runs under the hood:
1. **`scripts/code_metrics.py`** — Compares base vs head git blobs for cyclomatic branching delta, max nesting depth delta, function length delta, duplicate blocks, caller fan-in reach across the repo, and backend/frontend performance pattern deltas (N+1 DB calls in loops, caching annotations, `FetchType.EAGER`, batch ops, lazy imports, heavy libraries, polling, raw `fetch`/unmemoized selectors).
2. **`scripts/api_sql_probe.py`** — Scans changed SQL/Java `@Query` blocks for index additions/drops, leading-wildcard `LIKE`, locking DDL, and unfiltered `*_nls_res` joins; plus live multi-run HTTP API latency & payload benchmarking when baseline and PR API URLs are reachable.
3. **`scripts/bundle_diff.mjs`** — Builds PR base and head in ephemeral sparse git worktrees (reusing existing `node_modules`, auto-cleaned) to measure exact Vite production **initial entry gzip KB/%**, **total JS gzip & brotli KB/%**, CSS gzip, and top chunk diffs.
4. **`scripts/browser_bench.mjs`** — Uses Playwright + Chromium CDP across multiple runs (1 warmup + N measured) to compare **TTFB, FCP, LCP, CLS, TBT (long tasks), JS heap MB, API request count, and median API ms** between baseline and PR preview URLs.

## Sanity-check the numbers

1. Inspect the JSON output from `analyze_pr.py` and skim the PR diff (`gh pr diff` or PR read tool) to verify the scripts did not miss semantic changes (for example, an algorithmic O(n²)→O(n) fix or a cache invalidation bug that regexes cannot see).
2. Adjust the overall app `%` range or structure score `/10` only when the actual code evidence justifies it, and keep measured metrics verbatim (exact KB, ms, or `%`).
3. Keep this skill strictly read-only: never edit repo files, commit, push, post PR comments, or submit reviews.

## Output format (keep it short)

Reply in the user's language using this exact compact scorecard (~12 lines max; no walls of text or per-file dumps):

```markdown
**PR #<number>** — <short title>

| Metric | Score |
| --- | --- |
| **Overall app performance** | `~+X% to +Y%` *(optional: `~+A% to +B%` on <flow>)* · <Low/Medium/High> confidence (<basis>) |
| **Structure** | **X/10** (<Better / Neutral / Worse>) |

**Measured & observed:**
- <1 short bullet with exact tool numbers: e.g., bundle entry gzip delta, LCP/API ms, or complexity/SQL delta>
- <1 short bullet on structure: e.g., branching/nesting/duplication delta or architectural clarity>
- <optional 3rd bullet only if another concrete factor matters>

**Tools:** <1 line listing which tools ran vs skipped, e.g., `code_metrics ✓ · sql_jpa ✓ · bundle_diff ✓ (-12.4 KB gzip) · browser_bench skipped (no preview URL)`>
**Watchout:** <1 short risk/caveat, or `None`>
```
