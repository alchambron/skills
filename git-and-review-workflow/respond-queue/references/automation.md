# Automation contract

This file documents triggers only. Installation creates no schedules, webhooks or coordinator jobs.

Wake on a submitted **human CHANGES_REQUESTED** review of an open PR authored by `alchambron` in `EnerZam/JouleMV`. Bot account types, `[bot]`, CodeRabbit and Copilot identities do not qualify. Discover and paginate live reviews and the complete connected open stack (ancestors and descendants); mixed ownership or incomplete API evidence fails closed. Events carry no permission and do not replace checks before dispatch, every push, every reply and re-request.

## Commands and state

Use `python3 SKILL/scripts/queue.py [--respond-all PATH --respond PATH] COMMAND`.
The default shared Mac state directory is exactly `/Users/alchambron/.codex/review-state/respond-queue` (`~/.codex/review-state/respond-queue`). `--state` exists for isolated tests; production coordinators and children use the default, across all threads. Cross-machine reservations are outside this Mac's contract.

- `plan --output FILE`: calls respond-all's collector scoped to JouleMV and imports its history filter. Writes selection JSON compatible with `ledger.py queue`. Batch identity hashes all remaining feedback URL/date pairs; edited feedback changes identity. Repeated batches are listed as skips and must resume their saved run. Partial discovery fails closed.
- `claim --phase analysis|fix --number N --batch HASH --head SHA --run-dir RUN_DIR --thread ID --host-ready`: rechecks live eligibility/head, enforces two analyzer/one fixer caps and durable round limits, returns a token. Host readiness is the coordinator's current verified attestation; omit it for busy/offline/unknown status and the run is skipped. Claims never time out.
- `guard --token TOKEN --head SHA`: requires active fixer ownership and freshly verifies open/author/reviews/stack/head. Run immediately before every external action. The publisher additionally checks its pinned head and original feedback snapshot. A guard is not a reusable permission receipt.
- `finish --token TOKEN --outcome FILE --cleanup-verified`: release after verified worker/runtime cleanup. Outcome fields: `status` (`analyzed`, `waiting_ci`, `completed`, `handoff`, `skipped`), optional `head`, `actions` (strings), `commits` (pushed SHA strings), `replies` (verified URL strings), `re_request` (reviewer-to-readback object), `ci` (`state`, `head`, timestamp/evidence), and `reason` (mandatory for handoff/skip). CI states: `pending`, `passed`, `failed`, `missing`, `unknown`, `not_required`. `not_required` is only for no-push work. Failed CI requires a next round or handoff, not completion.
- `observe-ci --number N --batch HASH --head SHA --ci FILE`: save a settled exact-head CI observation after a waiting reservation has finished. FILE contains `state`, `head` and evidence/timestamp. This command writes local evidence only; coordinator must obtain it from live exact-head checks. Use `handoff --number N --batch HASH --reason TEXT` for exhausted rounds, infrastructure or eligibility blockers with no live worker.
- `complete --number N --batch HASH --run-dir RUN_DIR`: mark a released batch completed after its matching respond ledger has settled, publication/actions are reconciled and saved CI passed (or no-push not_required).
- `digest`: offline; atomically writes `digest-index.json` and a short `digest.md` in the state directory. Runs even when GitHub is unavailable. The external coordinator copies these to the 9 AM digest input; this skill neither schedules nor sends that digest.

## Durable digest schema (version 1)

Exact index path: `/Users/alchambron/.codex/review-state/respond-queue/digest-index.json`.
Markdown: `/Users/alchambron/.codex/review-state/respond-queue/digest.md`.

Top-level object: `schema_version:1`, `generated_at` (UTC ISO timestamp), `repository:"EnerZam/JouleMV"`, `source` (recorded-evidence caveat), `prs` (one object per PR/feedback batch), `reservations` (all job records), `skips` (objects with reason and optional URL/head/command/timestamp).

Each `prs[]`: `number`, `feedback_batch` (SHA256 hex), `head`, `fix_rounds` (0..2), `status`, `actions` (array), `commits` (pushed SHAs), `replies` (verified links), `re_request` (reviewer-to-status/readback object), `ci` (state, exact head and observation evidence), `handoff_reason` (string or null), optional `updated_at`. A handed-off batch is terminal for automatic dispatch; retain its unfinished respond ledger and drafts. New feedback is a separate batch, but a live reservation on the PR blocks concurrent work.

Each `reservations[]`: `number`, `phase`, `head`, `batch_key`, `run_dir` (absolute respond-all run path), `thread`, `status`, `started_at`, optional `finished_at`. Active reservations remain visible after crashes. The lock is `queue.lock`, durable state is `state.json`; neither is an acceptance result. Digest snapshots are recorded evidence, not current CI assertions.
