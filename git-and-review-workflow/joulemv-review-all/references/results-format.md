# Saved results contract

Use `results.json` as the coordinator's resumable ledger. Collection writes `snapshot.json` and per-PR raw artifacts; review and testing populate this separate results file. Run `scripts/render_results.py --input RUN_DIR/results.json --output RUN_DIR/report` to create the illustrated report.

The top-level object contains `version: 1`, `login`, `collected_at`, optional `collection` with `complete` and `warnings`, and a `prs` array. Each PR has:

- `repository`, `number`, `url`, `title`, `base_sha`, `head_sha`. These identify the pinned reviewed revision. Unavailable SHAs remain null only for an explicitly blocked review, with the access/collection reason in `coverage_gaps`; never invent pins for an inaccessible candidate. Save `current_head_sha` and `current_base_sha` after the final refresh; a changed revision makes the corresponding outcome stale.
- `review`: `status` is `pending`, `clean`, `issues`, or `blocked`; `findings`, `standards`, `spec`, and `limits` are arrays. `clean` means no actionable static defect, never approval. Findings carry `severity`, `title`, `body`, `path`, `line`, `side`, `origin` (`static` or `live`), and evidence references where available.
- `publication`: `status` is `pending`, `verified`, `reused`, `failed`, `not_requested`, or `not_required`; record `review_url`, `new_inline_count`, `reused_threads` (an array of thread URL strings), and any `error`. Populate verified status only after GitHub readback.
- `live`: `status` is `pending`, `complete`, `blocked`, or `not_required`; `scope` identifies `pr-head-only`, a verified integration commit, or a technical test; record the runtime commit and environment. Include `cleanup` with `database_dropped`, `servers_stopped`, and `worktree_removed`, each boolean or null when unknown. A pending, blocked, or missing test result is never a pass.
- `coverage_gaps`: unavailable original specifications, prerequisite failures, incomplete scenario coverage, and distinctions between observed behavior and deployment/integration evidence.
- `scenarios`: an array of records below. Every selected PR appears even if review, publication, startup, or testing fails.

Each scenario has a unique stable `id`, `title`, `preconditions`, `route`, `steps` array, `expected`, `actual`, and `status`. Status is one of `pass`, `fail`, `blocked`, `observed`, `partial`, or `spec_gap`. Preconditions/expected/actual may be text or structured JSON. Also retain:

- `screenshots`: local image paths saved from the browser. Include only images inspected for legibility and agreement with the observed outcome. Describe missing visual evidence in `visual_evidence` or `coverage_gaps`.
- `exports`: objects with `file_name`, `path`, and optional `verification` describing inspected contents. Copy downloads into the run's evidence directory immediately.
- `cleanup`: the scenario's restoration instructions or the disposable-runtime cleanup.

`observed`, `partial`, and `spec_gap` are separate from pass/fail/blocked counts. Distinguish multiple variants with unique IDs; combine repeated attempts of the same scenario into its final result and retain the attempt history in evidence. Do not inflate counts by duplicating a scenario ID.

Store actual observations, source limits, and cleanup receipts. Never put passwords, tokens, environment-file contents, or unredacted credential-bearing logs in this ledger.
