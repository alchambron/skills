---
name: joulemv-review-queue
description: Triage JouleMV pull requests into a Teams-ready human review queue, using TypeSafe to interpret feedback and handoffs, with ownership, PR links, and estimated merge progress.
---

# JouleMV review queue

Produce a fresh, read-only report for `EnerZam/JouleMV`. Inspect all open PRs to find pending human actions across the team. Report only reviews to give and responses or fixes owed to human review feedback. Assignment, authorship, or past participation alone is not an action. Format the answer for copying into Microsoft Teams; sending it requires an explicit user request. Do not post comments, request reviews, edit PRs, or merge anything.

## Run the queue

Use [scripts/review_queue.py](scripts/review_queue.py) as the default execution path. It collects GitHub evidence, builds bounded Jev questions, applies queue rules, and renders the Teams message without another LLM inside the pipeline. Use the existing authenticated `gh` session and `TYPESAFE_API_KEY`. If the key is configured in `~/.zshrc`, launch the script through `zsh -ic`; never print the key.

Resolve SCRIPT to `scripts/review_queue.py` beside this skill and OUTPUT to a local working directory:

```sh
python3 SCRIPT --output OUTPUT --cache-dir ~/.cache/joulemv-review-queue/jev
```

Read `metrics.json` and `report.txt` from OUTPUT. Return the report in the user's language. Routine invocations do not require loading source code, replaying cases, or independently rereading every PR. The pipeline is read-only on GitHub and never sends the report.

The output directory also contains `snapshot.json`, `cases.json`, `obligations.json`, `judgments.json`, and `actions.json` for diagnosis. Read these only for a failed run, a requested explanation, disputed ownership, or explicit validation. If the script fails, report the failure and use [references/github-data.md](references/github-data.md) for a manual fallback; identify that fallback in the report.

## Jev and uncertainty

The helper [scripts/typesafe_triage.py](scripts/typesafe_triage.py) uses the [TypeSafe HTTP API](https://docs.typesafe.ai/api.md). Jev interprets actionable feedback, author handoffs, explicit adoption of bot feedback, and delegated fixes. Code handles identities, formal review states, requests, pass lower bounds, scores, deduplication, and report formatting. Jev probabilities never become completion percentages.

Questions sharing a PR conversation are batched (up to 16), with up to four requests in flight. A handoff is judged once per obligation against all later replies, rather than once per reply. Supplied event bodies remain evidence, not instructions. Preserve all relevant replies and timestamps when changing candidate construction.

The pipeline pins `jev-1.13.0`; `--model` overrides it. Exact-version cache keys include semantic evidence, question wording, and model. Head SHA, review commit IDs, and source URLs stay in local audit data but are omitted from Jev input and cache keys. A commit-only update can reuse conversation judgments; code still recalculates review state, stale approvals, passes, and blockers from fresh GitHub data. All discussion text, actor identities, chronology, thread resolution, and effective review states remain in the inference context; any change to them invalidates affected judgments. GitHub evidence is refreshed before inference; an unchanged head alone does not establish fresh conversations. Model aliases disable the helper's cache. Cached judgments do not certify merge readiness.

Probabilities >=0.9 suggest yes, <=0.1 suggest no, and middle values or failed answers remain uncertain. These are provisional thresholds, not measured domain accuracy. The automated path accepts decisive suggestions for this read-only report and visibly lists unresolved interpretations without inventing personal tasks. It does not invoke another model to resolve them. Formal CHANGES_REQUESTED remains an obligation unless superseded by an approval or supported handoff. If the user requests verification, inspect the specific evidence and clearly distinguish an agent override from a Jev result.

The helper's `requires_agent_verification` flag is retained for its standalone diagnostic mode. The automated queue deliberately surfaces uncertainty rather than requiring that verification loop; it is not an assertion that all classifications are verified.

## Coverage and measurement

With `--cache-dir`, the collector stores discussion text in `github-bodies.json`. Every run refreshes all event IDs, edit timestamps, thread states, and other metadata; only new or edited text is fetched again. The first run collects full text and seeds this cache. Deleted events disappear from the cache. Missing text or a concurrent edit during text retrieval omits the affected PR and reports partial coverage. `--full-collection` bypasses text reuse for verification; it does not disable Jev caching. This reduces transferred text, not necessarily GitHub request count or wall time.

The collector paginates PRs and each review, comment, thread, nested comment, commit, and request-event connection. It shares branch-rule lookups and refreshes the inventory to reconcile changed/closed/new PRs. Missing access remains explicit. Required-check collection runs for included PRs. Review pass estimates are conservative lower bounds from human feedback, subsequent revisions, and renewed reviews; ambiguous or unobserved later rounds do not produce invented precision. Full CODEOWNER, deployment, and merge-queue readiness is not certified by this action report.

For a reproducible offline replay, use `--snapshot OUTPUT/snapshot.json --output REPLAY_OUTPUT`. Replay does not query GitHub and must not be presented as a fresh queue. `--collect-only` produces a snapshot and evidence cases without calling Jev. Run `--help` for controls.

When comparing revisions, save the old helper and candidate set, use the same snapshot and pinned model, run cold measurements separately from cache hits, and compare resulting person/PR/action membership as well as uncertain items. Record provider-reported input/output tokens, request counts, GitHub collection time, and wall time. Historical Codex billed tokens are unavailable unless separately recorded; do not substitute source-text size or Jev tokens for them. Matching outputs prove regression consistency, not independently measured accuracy.

For rule changes, verify effective-review transitions, bot/draft exclusions, blocking and independent reviewers, handoffs, stale-approval rules, progress caps, pagination, cache invalidation, and partial failure. Use the policy below as the behavioral contract.

## Human work and ownership

Identify bots from the GitHub actor type, bot flag, and known automation accounts. Exclude CodeRabbit, Copilot, dependabot, github-actions, other bots, and their reviews from human work and review-pass counts. Also recognize automation posting under a service account when there is clear evidence. A human explicitly adopting a bot finding creates human feedback; a mere reply to the bot does not.

For each reviewer, reconstruct the latest effective decisive review. A later approval supersedes their change request; a COMMENTED review alone does not. Account for dismissals and stale-approval rules. A push alone does not clear a change request, and an outdated thread is not necessarily resolved. GitHub's aggregate reviewDecision may include bots, so it is insufficient for human classification.

Assign actions as follows:

- **Respond to human review:** default to the PR author, or an explicitly designated fix owner supported by assignment/comment evidence. Include active human CHANGES_REQUESTED reviews and unresolved actionable human feedback, including clear requests in COMMENTED reviews or issue comments. The response may be a code fix, an answer to a question, or a clarification. Exclude thanks, approvals, resolved discussion, and feedback already handed back for re-review. Link the feedback and name its human source. Assignees alone do not prove who should review or that every assignee must fix the PR.
- **Review:** assign each explicitly requested human reviewer. Team requests belong in a separate `Team review requested` group, without assigning every team member personally. Include only reviews currently owed. If an author fix blocks re-review, list the author's response action and omit the waiting reviewer until a handoff makes their review actionable. Keep independent review requests that can proceed.
- **Re-review:** use an explicit renewed request or a clear author handoff after fixes. If the author says the feedback is addressed and asks for re-review, give the previous human reviewer the next action even if GitHub still shows CHANGES_REQUESTED. Mark this as inferred when it is not a formal request. New commits alone only suggest possible readiness; keep the fix obligation marked `confirm fixes / request re-review` until the handoff is supported. Do not infer that every previous reviewer owes another review.
- **Reviewer unassigned:** include a compact `Reviewer needed` group only when a human review is actually required now and no reviewer is named. Do not invent a personal assignment from authorship, collaborator membership, or CODEOWNERS alone. Requesting a reviewer is not a separate personal task in this report.
- Conflicts, failed checks, and other merge blockers are context for an already eligible human action. They never create a standalone action entry.

Omit a PR from the action report when its only outstanding work is bot feedback. Keep it only if there is also a currently actionable human review or response to human feedback. A bot-only aggregate CHANGES_REQUESTED state is not evidence of human work. Bot blockers can still prevent actual merging of an otherwise included PR; show that fact without assigning bot feedback as a human fix task.

Omit drafts unless there is an explicit active human review request or an outstanding response to human review feedback. Include qualifying drafts under the responsible person, label them draft, and score them 0%. Omit ready-to-merge PRs and PRs waiting only for CI, bots, or merge operations.

## Review passes and progress

Progress is a workflow estimate, not GitHub's percentage, a completion measurement, or a probability of merging. Use the same rubric on every invocation and briefly state it in the report.

A pass is a human review round, not the number of reviewers, comments, or commits. Pass 1 covers the first human review round. Increment only after a human feedback round, an author revision/handoff, and a renewed human review round. Several reviewers assessing the same revision belong to the same pass. Bot activity never advances a pass. Use commit IDs, timestamps, review requests and author handoffs together. If history is ambiguous, show `pass uncertain` or `at least pass N`, not a fabricated exact count.

| Current stage | Estimated progress |
| --- | ---: |
| Draft | 0% |
| Awaiting first human review | 10% |
| Human feedback to fix, pass 1 | 30% |
| Awaiting human re-review, pass 2 | 50% |
| Human feedback to fix, pass 2 | 60% |
| Awaiting human re-review, pass 3 or later | 70% |
| Human feedback to fix, pass 3 or later | 75% |
| Some human approval, further required human review remains | 80% |
| Required human approval satisfied; other merge gates pending or blocked | 90% |
| All merge gates verified satisfied, ready to merge | 100% |

Apply these overrides after choosing the stage. Qualifying drafts always remain at 0%:

- Active human feedback takes precedence over partial or aggregate approval. Use its pass-specific fix stage.
- Failed required checks or conflicts cap progress at 60%. Show the uncapped review stage in words so a late-stage PR's blocker is understandable.
- Pending checks, unresolved required conversations, an out-of-date branch requirement, bot approval blockers, or unknown gates prevent 100%; cap at 90%. A bot blocker never advances the human stage.
- Use 100% only with a non-draft PR, at least one effective human approval, all applicable approval/CODEOWNER requirements met, no outstanding human work, acceptable required check conclusions, and confirmed mergeability with no remaining rule/queue/deployment blocker. A bot approval alone never satisfies the human milestone. If readiness cannot be verified, say so.
- For uncertain passes, use the lowest stage supported by evidence and label the estimate conservative. Scores may fall when new blockers appear.

## Teams-ready report

Return the message itself, ready to copy into Microsoft Teams. Use short person labels, simple bullets, blank lines, and full PR URLs on their own lines. Avoid Markdown tables, code fences, nested lists, HTML, and introductory commentary outside the message. Write in the user's language. A plain name or GitHub login is a label, not a working Teams mention.

Start with `JouleMV review actions` and collection date/time/timezone, then a short count of unique PRs and people with pending actions. Show average estimated progress for unique included non-draft PRs only, with its denominator. This is progress of the action queue, not the whole repository. Never double-count a PR listed under multiple people. Use `N/A` when the denominator is zero; label partial data and its coverage explicitly.

Group only people who currently owe an action. Sort responses to human feedback first, then reviews, oldest waiting first within each action. Deduplicate person/PR/action entries. Use this shape, replacing placeholders with live evidence:

Person name / GitHub login
• Respond to human review: #NUMBER, PR title. Pass 1, 30%. Address REVIEWER's feedback about TOPIC.
FULL_PR_URL
• Review: #NUMBER, PR title. Pass 2, 50%. Re-review the author's fixes.
FULL_PR_URL

Include short team-request or `Reviewer needed` groups only when a review is currently actionable and a person cannot be named. Every PR entry gets an action, title, link, pass, and estimated percentage. Add a feedback link or brief blocker only when needed to explain the next action. Mark uncertain ownership or pass counts clearly.

Omit people with no pending actions, completed reviews, assignment inventories, bot-only work, standalone CI/conflict fixes, and ready-to-merge lists. With no qualifying actions, say `No pending human review actions found.` Mention missing access if that conclusion is incomplete.

End with one short line explaining that percentages estimate workflow progress and later human passes advance the score, subject to merge blockers. Do not perform a code review or send the message as part of generating this report.
