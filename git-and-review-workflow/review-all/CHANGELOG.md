# Changelog

## 1.4.0

- `scripts/render_results.py` now writes `summary.md` alongside `scenario-guide.html`, `inline-report.html`, and `results.json`, and supports `--print-markdown` so coordinators (including `review-queue`) can emit the complete review verdict, `file:line` findings, live acceptance scenarios, GitHub review links, and HTML report links inline as Markdown in the T3 thread.
- Updated `SKILL.md` and `references/results-format.md` to document `summary.md` inline delivery and clarify that same-head `discussion` re-requests must either post a fresh review or escalate with an explicit ping to Alexis rather than silently ending as `publication: reused`.
