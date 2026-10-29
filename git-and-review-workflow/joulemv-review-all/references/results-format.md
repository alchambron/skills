# Saved results contract

Use `results.json` as the coordinator's resumable ledger. Collection writes `snapshot.json` and per-PR raw artifacts; initialize the ledger from that snapshot and checkpoint outcomes with `scripts/ledger.py`. The helper locks updates, validates them, and atomically replaces the ledger. Child agents write separate payload files; the coordinator records them. Run `scripts/render_results.py --input RUN_DIR/results.json --output RUN_DIR/report` to create the illustrated report.

The top-level object contains `version: 1`, `login`, `collected_at`, optional `collection` with `complete` and `warnings`, and a `prs` array. Each PR has:

- `repository`, `number`, `url`, `title`, `base_sha`, `head_sha`. These identify the pinned reviewed revision. Unavailable SHAs remain null only for an explicitly blocked review, with the access/collection reason in `coverage_gaps`; never invent pins for an inaccessible candidate. Save `current_head_sha` and `current_base_sha` after the final refresh; a changed revision makes the corresponding outcome stale. Freshness is `current` only after both refreshed SHAs match, `stale` when either differs, and `unknown` when refresh evidence is missing. Only current outcomes contribute to current counts.
- `review`: `status` is `pending`, `clean`, `issues`, or `blocked`; `findings`, `standards`, `spec`, and `limits` are arrays. `clean` means no actionable static defect, never approval. Findings carry `severity`, `title`, `body`, `path`, `line`, `side`, `origin` (`static` or `live`), and evidence references where available.
- `publication`: `status` is `pending`, `verified`, `reused`, `failed`, `not_requested`, or `not_required`; record `review_url`, `new_inline_count`, `reused_threads` (an array of thread URL strings), and any `error`. Populate verified status only after GitHub readback.
- `live`: `status` is `pending`, `complete`, `blocked`, or `not_required`; `scope` is `pr-head-only`, `integration`, or `technical`. Record `runtime_commit` as a full SHA and `environment` as a nonempty description or object. For `pr-head-only`, the runtime commit must equal `head_sha`. For `integration`, also record `integration_parents` containing the pinned base and head; verify these from the actual commit. Technical checks also require `runtime_commit` to equal `head_sha`; identify that tested source revision and the actual test environment. A different revision is displayed as incomplete evidence.
- `live.cleanup`: `database_dropped`, `servers_stopped`, and `worktree_removed` are booleans, or null when unknown. Missing/failed cleanup remains a separate action without erasing observed behavior. Technical checks that create no runtime resources may set `cleanup_required: false` with a nonempty `cleanup_reason`. A complete claim requires recorded scenarios, runtime provenance and finished acceptance coverage. The renderer preserves original statuses while displaying unsupported claims as incomplete or partial; a missing screenshot alone does not invalidate observed behavior.
- `project_type` and `project_label`: flat presentation metadata derived from `repository` by `scripts/project_profiles.py`. Exact known owner/repository matches are case-insensitive: `EnerZam/JouleMV` → `joulemv`, `EnerZam/WebSynco_React_Front` → `websynco-react-front`, and `EnerZam/WebSynco` → `websynco-backend`. Other repositories use `other` / `Repository-specific`, with a separate report group for each repository. Version 1 ledgers without these fields remain valid; the renderer infers them and replaces conflicting hints from the canonical helper. The resumable identity remains the exact `repository` + `number`; matching PR numbers across repositories stay distinct.
- Optional `runtime_provenance`: structured per-PR runtime evidence, displayed alongside `live`. When applicable, record `frontend` and `backend` objects with `repository` and full `sha`, `schema_mode`, and `database` with non-secret `source_identity` and `owned_clone_identity`. Include resolved writable filesystem roots, process identities, and ownership/cleanup receipts. Record unavailable values as null with the reason in `coverage_gaps`. These are evidence of what ran, separate from the reviewed base/head pins and the repository-derived project profile; do not use this metadata as proof of integration or deployment. Use the selected project's runtime reference for actual setup and ownership rules. Never include connection strings or credentials in database identities.
- `coverage_gaps`: unavailable original specifications, prerequisite failures, incomplete scenario coverage, and distinctions between observed behavior and deployment/integration evidence.
- `revision_history`: archived pins, evidence and outcomes invalidated by revision drift. `pending_cleanup` retains old runtime receipts whose owned resources still need cleanup. Preserve these until absence is verified.
- `scenarios`: an array of records below. Every selected PR appears even if review, publication, startup, or testing fails.

Plan scenarios during static review, including for PRs with defects. Record unexecuted/deferred scenarios as `blocked` and explain their dependency or prerequisite in `actual`. Update the same ID when execution becomes possible.

Each scenario has a stable `id` unique within its PR, `title`, `preconditions`, `route`, `steps` array, `expected`, `actual`, and `status`. Its full identity is `repository` + PR `number` + scenario `id`; another PR may reuse the same scenario ID. Status is one of `pass`, `fail`, `blocked`, `observed`, `partial`, or `spec_gap`. Preconditions/expected/actual may be text or structured JSON. Also retain:

- `screenshots`: local image paths saved from the browser. Include only images inspected for legibility and agreement with the observed outcome. Describe missing visual evidence in `visual_evidence` or `coverage_gaps`.
- `exports`: objects with `file_name`, `path`, and optional `verification` describing inspected contents. Copy downloads into the run's evidence directory immediately.
- `cleanup`: the scenario's restoration instructions or the disposable-runtime cleanup.

`observed`, `partial`, and `spec_gap` are separate from pass/fail/blocked counts. Distinguish multiple variants with unique IDs; combine repeated attempts of the same scenario into its final result and retain the attempt history in evidence. Do not inflate counts by duplicating a scenario ID.

The renderer adds `report` metadata: batch `pr_count`, `scenario_count`, scenario `counts`, `non_stale_counts`, `stale_pr_count`, `unpinned_pr_count`, and `warnings`. `report.projects` contains canonical project groups, each with `id`, `project_type`, `project_label`, original-array `pr_indexes`, the same counts, and `repositories` with their own `repository`, `pr_indexes`, and counts. This metadata is recomputed on every render. The project selector filters the overview and PR/scenario selectors; the entire-batch totals and collection warnings remain labeled separately. A project with blocked PRs and no scenarios remains visible with zero scenario counts. An empty batch has no project groups.

Counts describe display outcomes: a recorded pass with incomplete evidence becomes `partial`, and an unpinned PR's scenarios become `blocked`. Historical stale outcomes remain in `counts`; `current_counts` and its compatibility alias `non_stale_counts` include only matching final refreshes. Unknown freshness is excluded alongside stale and unpinned PRs. Project filtering preserves these semantics and never changes the saved review, publication, or scenario status.

Store actual observations, source limits, and cleanup receipts. Never put passwords, tokens, environment-file contents, or unredacted credential-bearing logs in this ledger.

## Checkpoint and resume

Use the helper's `--help` for current options. Initialize once from the selected snapshot:

```sh
python3 "$SKILL_DIR/scripts/ledger.py" init --snapshot "$RUN_DIR/snapshot.json" --output "$RUN_DIR/results.json"
```

Write each section's JSON payload to an owned file, then record it against the exact pinned revision:

```sh
python3 "$SKILL_DIR/scripts/ledger.py" record --input "$RUN_DIR/results.json" \
  --repository "$REPOSITORY" --number "$PR_NUMBER" \
  --base-sha "$BASE_SHA" --head-sha "$HEAD_SHA" \
  --section scenario --data "$RUN_DIR/scenario-result.json"
```

Sections are `review`, `publication`, `live`, `scenario`, `coverage_gaps` and archived `cleanup`. Section payloads replace that section; a scenario upserts its stable ID. A pin mismatch refuses the write. For inaccessible pins, pass the literal `unavailable` instead of a SHA. To resolve an archived runtime, use `--section cleanup` with its old base/head pins and a payload containing `database_dropped`, `servers_stopped` and `worktree_removed`, each boolean or null. Partial receipts stay pending; all three true clears the pending item while retaining history. Record publication only after the publishing skill's full GitHub readback. Save reference URLs even when a submission outcome is uncertain, so recovery checks them before another write.

```sh
python3 "$SKILL_DIR/scripts/ledger.py" resume --input "$RUN_DIR/results.json"
```

Resume makes read-only GitHub requests for the existing ledger's PRs, refreshes both pins and reconciles saved review/thread references. It never publishes. On revision drift, it archives prior outcomes and resets affected stages while retaining outstanding cleanup. Reconcile equivalent existing threads before publishing on the new revision. Failed refresh clears freshness assertions; failed publication reconciliation retains links and the error. An ambiguous response is a reason to investigate, not authorization to retry publication. Resume preserves closed/merged targets visibly; do not continue publication or runtime work on those targets without a new user request.

The helper's permalink/SHA check supplements the publishing skill's detailed body, anchor and comment readback. It does not replace that verification or the coordinator's decision that an older/outdated reused thread still applies. A matching permalink never upgrades an ambiguous submission to verified. Render from the coordinator ledger and keep the generated report ledger separate.

The helper exits 2 on invalid input or unresolved refresh/publication failures. Read its JSON result and the saved errors before continuing; matched references on a pending publication still require full publisher readback.
