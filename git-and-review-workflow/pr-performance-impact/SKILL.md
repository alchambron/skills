---
name: pr-performance-impact
description: Scientifically benchmark a pull request's real performance impact (%) and code structure (/10) using interleaved A/B Vite, Playwright, HTTP API, and PostgreSQL EXPLAIN ANALYZE tools with zero static % estimation. Use when asked for the performance impact of a PR or invoked as $pr-performance-impact or /pr-performance-impact.
---

# PR Performance Impact (v2.0.0 — Strictly Measured)

Produce a short, read-only, strictly measured scorecard for one pull request: verified performance impact (`%`, with `n` and `p`-value) and code structure score (`/10`).

**Zero estimation rule:** Never guess or fabricate a runtime performance `%` from reading the diff. Every performance percentage must come directly from an executed benchmark. If no runtime benchmark ran for an axis, report **`Not measured`**. Only claim a speedup or regression when statistically significant (`p < 0.05`, `|delta| >= 2%`, `|Cliff's delta| >= 0.33`) or exact deterministic bytes (`|entry gzip| >= 0.5%`).

## Bundled measurement tools (`scripts/`)

Run the unified orchestrator from this skill's directory against the target repository (read-only; never modifies your checkout):

```bash
python3 <skill-dir>/scripts/analyze_pr.py <PR_NUMBER_OR_URL> --repo . --check-updates --json
```

Omit `<PR_NUMBER_OR_URL>` to target the current branch's PR. Optional flags:
- `--runs <N>` — interleaved A/B pairs per benchmark (default `7`, plus 2 discarded warmups).
- `--pr-url <url> --base-url <url> --routes /,/dashboard --login` — interleaved live Playwright Web Vitals & API benchmark.
- `--pr-api <url> --base-api <url> --endpoints /api/...` — interleaved live HTTP API latency & payload benchmark.
- `--pg-dsn <uri> --sql-base <sql|@file> --sql-pr <sql|@file>` — interleaved PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` inside `BEGIN; ... ROLLBACK;` (strictly read-only).
- `--bench-tests` — interleaved Vitest execution timing on modified test files shared by base and head.
- `--check-updates` — reads local `VERSION` and checks `alchambron/skills/skill-versions.json` on `main` (read-only, non-blocking).

What runs under the hood:
1. **`scripts/bundle_diff.mjs`** — Builds PR base and head in ephemeral sparse git worktrees via real `vite build` (auto-cleaned), measures exact raw/gzip/brotli/entry-chunk bytes, then serves both built `dist/` folders on `127.0.0.1` with gzip and runs **interleaved A/B Playwright Chromium** measurements (`ScriptDuration` V8 CPU, `TaskDuration`, `JSHeapUsedSize`, `FCP`, `DOMInteractive`, `TBT`) with Mann-Whitney U `p`-values.
2. **`scripts/browser_bench.mjs`** — Interleaved A/B live Playwright Web Vitals (`TTFB`, `FCP`, `LCP`, `TBT`, `CLS`), V8 CPU, JS heap, and API timing against live baseline vs. PR preview URLs.
3. **`scripts/api_sql_probe.py`** — Interleaved A/B HTTP API latency (`median`, `IQR`, `p95`, `p`-value) and read-only PostgreSQL `EXPLAIN (ANALYZE, BUFFERS)` query execution timing.
4. **`scripts/unit_bench.py`** — Opt-in interleaved A/B Vitest timing on shared modified unit tests.
5. **`scripts/code_metrics.py`** — Exact before/after static structure metrics (branching complexity, max nesting depth, function length, duplicate blocks, companion tests) used **only** for the `/10` structure score — never for runtime `%`.
6. **`scripts/stats_util.py`** — Pure-stdlib Mann-Whitney U test (tie-corrected), Cliff's delta, median, IQR, and p95.

## Rules

- Report measured tool outputs verbatim (exact KB, ms, `%`, `n`, and `p`-value).
- If `verdict.status` is `not_measured`, state **`Not measured`** and name the exact missing runtime target (e.g., pass `--pr-api` or `--pg-dsn` + `--sql-base/--sql-pr`). Never substitute an AI-guessed `%`.
- Keep this skill strictly read-only: never edit repo files, commit, push, post PR comments, or submit reviews.

## Output format (keep it short)

Reply in the user's language using this exact compact scorecard (~12 lines max):

```markdown
**PR #<number>** — <short title>

| Metric | Result |
| --- | --- |
| **Measured performance** | `<exact % delta / No significant change / Not measured>` |
| **Structure** | **X/10** (<Better / Neutral / Worse>) |

**Measurements (base → PR):**
- **Bundle (exact):** entry gzip `<base> → <head> KB` (`<±X.XX%>`), total JS gzip `<±X.X KB>` *(or `N/A — no frontend`)*
- **Runtime (n=<N> interleaved):** <exact V8 CPU / FCP / Heap / Live LCP / API ms / SQL EXPLAIN ms with `%` and `p=` — or `Not measured (<reason>)`>
- **Structure:** `<branches delta>`, `<nesting delta>`, `<dup blocks delta>`, `<test files>`

**Verdict:** <1 concise scientific sentence; mention update notice only if `--check-updates` found a newer release.>
```
