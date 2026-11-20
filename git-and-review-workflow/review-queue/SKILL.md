---
name: review-queue
description: Start Alexis's directly requested EnerZam/JouleMV PR reviews on arrival and reconcile them hourly. Invoke the installed review-all skill per PR and export the private digest.
---

# Automatic review queue

Alexis authorizes operation of this queue on his GitHub account for open, non-draft EnerZam/JouleMV PRs where `alchambron` is currently a directly requested reviewer. An authorized queue wake-up invokes the installed [review-all](../review-all/SKILL.md) with that one PR's scope, including its verified REQUEST_CHANGES publication and isolated local testing. Never approve, merge, deploy, edit PR source, or send messages. Creating or installing this skill grants no new permissions and creates no routines.

Read [automation.md](references/automation.md) for trigger, command, recovery, and digest contracts.

1. Resolve Alexis's installed `review-all` and `respond-queue` folders under `~/.codex/skills`. On each review-all invocation or resumption, read its installed `SKILL.md` and run its dependency and update check. Follow its resolved companions and honor their constraints. Keep the same review-all history and heavy-lock roots across threads.
2. On a review-request event, immediately plan that PR. On hourly reconciliation, paginate the current open PRs and check parked or interrupted jobs. GitHub's current direct request and pinned base/head govern admission. A request for another reviewer or team alone gives no eligibility. Leave completed same-pin work for Alexis's decision and reuse evidence per review-all. Preserve failed or interrupted work and retry while eligible.
3. Claim a static slot immediately before dispatch into a durable, per-PR T3 thread. Run the actual installed `$review-all` scoped to the one PR and supply the queue's single-PR selection. Follow all its stages, including independent Standards and Spec work, verified publication, scenario planning, real acceptance, cleanup, history, and the same illustrated HTML. The queue never makes a review or publication decision. Check the reservation, current direct request and both pins before starting work or invoking the publisher.
4. When review-all needs acceptance, release the static slot while retaining ownership of the PR, then claim the sole live slot as soon as host and shared capacity allow. Resume the same review-all ledger immediately. There is no nightly start window or update-from-work completion-file dependency. Guard again before runtime start and publication. Replan on revision drift. If GitHub clears the direct request after publication, preserve the verified feedback and record any remaining blocked coverage in the report. Reconcile the saved publication before any repeat.
5. Save the canonical review-all ledger and illustrated report outside disposable worktrees. With the worker stopped and owned runtime cleanup verified, finish the queue reservation. Finish emits the per-PR outcome, report path and ping into the local dated digest. The coordinator imports it into Alexis's existing 09:00 Toronto digest. No separate ping is sent.
