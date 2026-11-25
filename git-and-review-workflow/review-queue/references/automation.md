# Automation contract

The coordinator operates on Alexis's Mac using the installed skills and his GitHub account. The orchestrator owns the event subscriptions, wakes, and private digest delivery. Use these contracts when configuring the routines.

## Wakes and dispatch

- **Arrival.** Subscribe to GitHub `pull_request` with action `review_requested` and `requested_reviewer.login` equal to `alchambron` across Alexis's repositories. Invoke `$review-queue` immediately with the repository and PR number and run `plan --repo OWNER/REPO --number N`. This reads the PR directly, including before GitHub search can index it. Acknowledge duplicate event deliveries through normal reservation checks.
- **Hourly.** Keep an hourly `$review-queue reconcile` wake around the clock. Run `digest --retry-pending` independently of GitHub, import successful exports, and record any remaining export errors. Run `plan` without `--number`. The sweep searches open, non-draft direct review requests across all repositories (`is:pr is:open draft:false user-review-requested:alchambron`), fetches each candidate's live PR metadata, and selects direct requests that need work, including missed events, changed head or base, genuine re-requests after feedback, PRs whose prior review was never pinged to Alexis, and failed or incomplete runs. Team requests and requests for another reviewer alone are skipped with explicit reasons. Also read durable active jobs, reconcile their real T3 thread state, and advance parked runtime work FIFO when a slot becomes available. Schedule a queue wake on worker or slot completion so a parked job can proceed before the next sweep.
- **Checkout resolution.** Map each `owner/repo` to its local checkout on the Mac without ever cloning silently. `queue.py plan` resolves checkouts from:
  1. `--repos-config FILE` (or `$REVIEW_QUEUE_REPOS_CONFIG`) or `~/.codex/review-state/review-queue/repos.json` (`{"owner/repo": "/abs/path"}` or `{"repositories": {...}}`).
  2. `--t3-projects FILE` (or `$REVIEW_QUEUE_T3_PROJECTS`), accepting the JSON output of `t3_project_list`.
  3. Built-in defaults when neither config nor T3 project list is supplied:
     - `EnerZam/JouleMV` → `/Users/alchambron/Documents/Projects/Joule_MV/JouleMV`
     - `EnerZam/WebSynco_React_Front` → `/Users/alchambron/Documents/Projects/WebSynco/WebSynco_React_Front`
     - `EnerZam/WebSynco` → `/Users/alchambron/Documents/Projects/WebSynco/WebSynco`
  Every resolved path is verified on disk (existing directory with `.git`). Missing or unconfigured repositories are reported as skips with that exact reason in both the `plan` output and the dated digest.
- **Availability.** Verify the Mac and relevant resources are available before asserting `--host-ready`. Already-reserved work in another slot can continue. On offline, busy, unknown, or failed dispatch, persist the reason and retry on the next wake. `update-from-work` shares the same `queue-slots.lock` live slot (`update` phase in `shared_slots.py`) as `review-queue` live acceptance and `respond-queue` fixers, so active updater work is arbitrated automatically through `claim` / `claim-live` without ad-hoc thread inspection or a morning cutoff window.
- **Serialization.** Use one dispatcher for selection-to-thread assignment across triggers (e.g. a nonblocking flock on `~/.codex/review-state/review-queue/dispatch.lock`). Reserve before starting the worker. Create the T3 thread idle first in the target repository's project/checkout, or claim with a durable dispatch ID and save its thread/link mapping in the run directory. Keep an immutable one-PR selection and a unique durable repository-scoped run directory per attempt:
  `~/.codex/review-state/review-queue/runs/<owner>/<repo>/pr-<number>-<head8>-<rand>/`
  Legacy JouleMV run paths under `runs/pr-<number>.<rand>/` remain readable through their stored absolute paths in `state.json` and `review-all/state.json`.

For each worker, use this brief with the actual paths and IDs:

> Explicitly invoke Alexis's installed $review-all for PR_URL (REPOSITORY#NUMBER) only, in unattended/headless report mode, using local checkout CHECKOUT_PATH. Load REVIEW_ALL/SKILL.md and follow the complete workflow and resolved companions. Use SELECTION_PATH and RUN_DIR with review-queue reservation TOKEN. Guard current eligibility and pins before collection and each publication and runtime start. On a same-head re-request (discussion mode), either publish a fresh review or prepare an explicit escalation reason for Alexis; never resolve silently as publication: reused. If static review verifies actionable defects and publishes a fresh verified REQUEST_CHANGES review, skip scenario planning and live acceptance (record live.status: not_required) and finish immediately without claiming the live slot. For clean PRs, obtain the live slot before acceptance. Generate scenario-guide.html, inline-report.html, and summary.md via render_results.py and verify all three exist; skip html_preview, html_render, and custom layout-check scripts. Follow review-all's evidence, publication, history, and cleanup requirements. Return the ledger, report paths (HTML and summary.md), verified publication links, and cleanup evidence.

If runtime is queued, checkpoint in the same thread's ledger and yield. Resume the whole installed workflow from that checkpoint as soon as capacity permits. Review-all decides which scenarios to run and which verified findings to publish. When static review publishes verified `REQUEST_CHANGES`, live acceptance is skipped (`live.status: not_required`) and no live slot is claimed. A request cleared by its own publication still permits report rendering, history, and owned cleanup. Further runtime starts or publications require a current direct request. Record `publication.status: not_required` when a clean review requires no publication.

## No silent skips and re-requests

- **Awaiting decision gate.** `plan` skips a PR as `awaiting Alexis decision at current pins` only when BOTH:
  1. `previousRun` actually contains a non-empty `report/scenario-guide.html` and a matching ledger (`results.json` or `report/results.json`) with a finished review (`review.status` in `clean`/`issues`, `live.status` in `complete`/`not_required`) at that exact `(repository, number, head_sha, base_sha)`.
  2. `review-queue` has a completed finish record and emitted ping for Alexis at that exact `(repository, number, head, base)` (not superseded by a failed/blocked attempt).
  Otherwise the PR stays eligible and is selected.
- **Explicit deduplicated skips.** Every skipped candidate (ineligible, active reservation, missing checkout, awaiting decision, or fetch error) receives a concrete reason, is printed in `plan`'s stdout JSON and selection file, and is persisted once (deduplicated by `pr_key`, `command`, `reason`, `head`, `base`) into `state.json` and the dated digest.
- **Same-head re-requests.** When a PR was previously reviewed with verified `REQUEST_CHANGES` and is re-requested at the same head (`kind: rerequested` / `basis.mode: discussion`), `finish --status completed` refuses `publication: reused` unless `--ping-alexis --reason "..."` is passed. Either post a fresh review (`publication: verified`) or pass `--ping-alexis --reason "..."` to emit an explicit Alexis decision ping.

## Inline T3 thread presentation

After `finish` succeeds, run:

```sh
python3 "$REVIEW_QUEUE/scripts/queue.py" summary --token "$TOKEN"
```

End the coordinator's T3 thread with the returned Markdown verbatim (matching `respond-queue`'s structured presentation): top summary table, per-PR verdict, findings table with `file:line`, live acceptance scenarios run, GitHub review permalink(s), and clickable local links to `scenario-guide.html`, `inline-report.html`, and `summary.md`. Keep the HTML reports intact alongside the inline Markdown.

## Commands and shared state

Use the installed `review-queue/scripts/queue.py` with Python 3. Pass `--review-all PATH --respond-queue PATH` before the command for non-sibling installations. Production uses `~/.codex/review-state/review-queue`. `--state` is for isolated tests.

- `migrate` idempotently upgrades v1 `state.json` (bare JouleMV numbers) to v2 (`owner/repo#number` keys across `jobs`, `prs`, and `skips`) without losing history. Normal commands also migrate transparently on read/write.
- `plan [--repo OWNER/REPO] [--number N] [--repos-config FILE] [--t3-projects FILE] --output FILE` reads GitHub and `review-all` history, resolves local checkouts, records new skips once in `state.json`/digest, and prints selected and skipped summaries. At dispatch retain the selection metadata and exactly one unchanged entry in `selected`. Feed this file to `review-all`'s collector.
- `claim --selection FILE --run-dir DIR --thread LINK --host-ready` returns a static reservation token (`token`, `repository`, `pr_key`). Before collecting or publishing, `guard --token TOKEN` rechecks the live request and both pins against the reservation.
- `park --token TOKEN` moves from static to waiting for live after the static workers and their heavy operations have stopped. `claim-live --token TOKEN --host-ready` rechecks GitHub and claims the shared runtime slot.
- `defer [--repo OWNER/REPO] [--number N] [--token TOKEN] --reason TEXT` records an external blocker for the digest.
- `finish --token TOKEN --status completed|blocked|failed [--reason TEXT] [--ping-alexis] --cleanup-verified` saves a result, updates `state.prs[pr_key]`, and refreshes its local dated digest. Use `completed` only after `review-all` delivers the matching report with validated coverage, settled publication (no silent `reused` on same-head re-requests), and HTML. The first completed finish checks the current GitHub head and base; if either moved, it saves a blocked result and the coordinator replans.
- `summary (--token TOKEN | --run-dir DIR)` prints the rendered inline Markdown summary (`report/summary.md`, or renders from `results.json` via `review-all/scripts/render_results.py`).
- `digest [--date YYYY-MM-DD | --retry-pending]` regenerates dated JSON and Markdown offline using Toronto dates.

All three queues (`review-queue`, `respond-queue`, and `update-from-work`) use `~/.codex/review-state/queue-slots.lock` and `respond-queue/scripts/shared_slots.py`. The combined caps across ALL repositories and queues remain **two static reviews or response analyses** and **one live runtime, response fixer, or branch update**. Duplicate-PR checks are scoped by `(repository, number)` so identical PR numbers in different repositories do not collide. Preserve the separate heavy-command lock at `$REVIEW_STATE_ROOT/heavy/slot-0.lock` with one slot for installs, builds, tests, and runtime startup.

Before switching versions, pause wakes for both queues and reconcile active work and cleanup. Replace the complete `review-queue`, `review-all`, and `respond-queue` skill folders together (all `1.4.0`), keeping both state directories and `review-all` history.

## Digest delivery (schema version 2)

The Mac writes `~/.codex/review-state/review-queue/digest/YYYY-MM-DD.json` and `.md`. On each finish, new plan skip, and daily 09:00 America/Toronto wake, the orchestrator imports these into `/home/box/shared/t3-review-queue/digest/<date>.json` and `.md` on its host. Preserve the absolute original HTML path (`html_path`) and Markdown summary path (`summary_path`).

The JSON has `schema_version: 2`, `date`, `generated_at`, `repositories` (sorted distinct `owner/repo` strings present in the digest), `reviews`, `reservations`, and `skips`. Each finished review emits `token`, `repository`, `pr_key` (`owner/repo#number`), `pr` (number), `url`, `head_sha`, `base_sha`, `verdict`, `outcome`, `html_path`, `summary_path`, `review_url`, `finished_at`, `ping`, `ping_alexis`, `reason`, `live`, `publication`, `thread`, and `run_dir`. Every reservation and PR-scoped skip carries `repository` and `pr_key`.

For the 09:00 digest, read the two Toronto calendar dates spanned by the interval since the previous 09:00 cutoff. Deduplicate imported rows by `token` and put one ping per `pr_key` at the top of the digest using its latest finish in the interval:
- `owner/repo#1234 review ready, check before approving` (clean completed report)
- `owner/repo#1234 changes requested, see report` (completed report with verified issues)
- `owner/repo#1234 re-requested at reviewed head, needs Alexis decision` (same-head re-request escalated via `--ping-alexis`)
- `owner/repo#1234 review <outcome>, <detail>` (blocked/failed/incomplete)
Keep report links, active or parked work, deduplicated skips, and cleanup below. Deliver this existing digest to Alexis only.
