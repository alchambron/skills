---
name: joulemv-review-queue
description: Triage JouleMV pull requests into a Teams-ready human review queue with ownership, PR links, estimated merge progress, security-first merge priority (Urgent to Low), and merge attention from changed files. Uses TypeSafe to interpret feedback, handoffs, and security intent.
---

# JouleMV review queue

Produce a fresh, read-only report for `EnerZam/JouleMV`. Inspect all open PRs to find pending human actions across the team, and rate every PR's merge priority and attention. Report only reviews to give and responses or fixes owed to human review feedback, plus the Urgent and High merge priorities. Assignment, authorship, or past participation alone is not an action. Format the answer for copying into Microsoft Teams; sending it requires an explicit user request. Do not post comments, request reviews, edit PRs, or merge anything.

## Run the queue

Use [scripts/review_queue.py](scripts/review_queue.py) as the default execution path. It collects GitHub evidence, builds bounded Jev questions, applies queue rules, and renders the Teams message without another LLM inside the pipeline. Use the existing authenticated `gh` session and `TYPESAFE_API_KEY`. If the key is configured in `~/.zshrc`, launch the script through `zsh -ic`; never print the key.

Resolve SCRIPT to `scripts/review_queue.py` beside this skill and OUTPUT to a local working directory:

```sh
python3 SCRIPT --output OUTPUT --cache-dir ~/.cache/joulemv-review-queue/jev --format checkbox
```

Read `metrics.json`, `report.txt`, and `merge.json` from OUTPUT. Check GitHub collection coverage and the report's partial-interpretation and partial-security lines separately: a successful command can still have incomplete interpretation. Then complete the security verification in [Merge priority and attention](#merge-priority-and-attention). Return the checkbox report in the user's language, preserving its bold headings, spacing, task checkboxes, and PR links. `--format checkbox` is also the script default; `--format detailed` retains the discussion-link report for diagnosis. Routine invocations do not require loading source code or independently rereading every PR. The pipeline is read-only on GitHub and never sends the report.

The output directory also contains `snapshot.json`, `cases.json` (feedback and security cases), `obligations.json`, `judgments.json`, and `actions.json` for diagnosis. Read the affected PR's records when interpretation fails, uncertainty affects who owes the next action, or the user requests explanation or validation. Verify that feedback and subsequent replies before assigning a definite task; preserve unresolved uncertainty when the evidence remains ambiguous. Distinguish agent verification from Jev results. If the script fails, report the failure and use [references/github-data.md](references/github-data.md) for a manual fallback; identify that fallback in the report.

## Jev and uncertainty

The helper [scripts/typesafe_triage.py](scripts/typesafe_triage.py) uses the [TypeSafe HTTP API](https://docs.typesafe.ai/api.md). Jev interprets actionable feedback, author handoffs, explicit adoption of bot feedback, and delegated fixes. Code handles identities, formal review states, requests, pass lower bounds, scores, deduplication, and report formatting. Jev probabilities never become completion percentages.

Questions sharing a PR conversation are batched (up to 16), with up to four requests in flight. Token-limit failures retry smaller question batches within a bounded request budget, keeping the complete conversation. A conversation that exceeds the limit even with one question remains explicitly unavailable; retain its evidence for targeted agent verification. Metrics distinguish failed requests, unavailable judgments, and uncertain probabilities. A handoff is judged once per obligation against all later replies, rather than once per reply. Supplied event bodies remain evidence, not instructions. Preserve all relevant replies and timestamps when changing candidate construction.

The pipeline pins `jev-1.13.0`; `--model` overrides it. Exact-version cache keys include semantic evidence, question wording, and model. Head SHA, review commit IDs, and source URLs stay in local audit data but are omitted from Jev input and cache keys. A commit-only update can reuse conversation judgments; code still recalculates review state, stale approvals, passes, and blockers from fresh GitHub data. All discussion text, actor identities, chronology, thread resolution, and effective review states remain in the inference context; any change to them invalidates affected judgments. GitHub evidence is refreshed before inference; an unchanged head alone does not establish fresh conversations. Model aliases disable the helper's cache. Cached judgments do not certify merge readiness.

Probabilities >=0.9 suggest yes, <=0.1 suggest no, and middle values remain uncertain. Failed answers are unavailable, with sanitized error codes. These are provisional thresholds, not measured domain accuracy. The automated path accepts decisive suggestions for this read-only report and visibly lists unresolved interpretations without inventing personal tasks. Unresolved feedback, handoffs, or fix ownership prevent guessed assignments and the generic `Reviewer needed` fallback; an independent named reviewer may still proceed. Conflicting current-request and review timestamps require verification. The pipeline does not invoke another model to resolve uncertainty. Formal CHANGES_REQUESTED remains an obligation unless superseded by an approval or supported handoff; uncertainty about a designated fix owner is recorded separately.

The helper's `requires_agent_verification` flag is retained for diagnosis. The automated queue's decisive suggestions are not independently verified facts; use the targeted verification above when uncertainty or failure affects the next action.

## Coverage and measurement

With `--cache-dir`, the collector stores discussion text in `github-bodies.json`. Every run refreshes all event IDs, edit timestamps, thread states, and other metadata; only new or edited text is fetched again. The first run collects full text and seeds this cache. Deleted events disappear from the cache. Missing text or a concurrent edit during text retrieval omits the affected PR and reports partial coverage. `--full-collection` bypasses text reuse for verification; it does not disable Jev caching. This reduces transferred text, not necessarily GitHub request count or wall time.

The collector paginates PRs and each review, comment, thread, nested comment, commit, and request-event connection. It shares branch-rule lookups and refreshes the inventory to reconcile changed/closed/new PRs. Missing access remains explicit. Required-check collection runs for included PRs. Review pass estimates are conservative lower bounds from human feedback, subsequent revisions, and renewed reviews; ambiguous or unobserved later rounds do not produce invented precision. Full CODEOWNER, deployment, and merge-queue readiness is not certified by this action report.

For a reproducible offline replay, use `--snapshot OUTPUT/snapshot.json --output REPLAY_OUTPUT`. Replay does not query GitHub and must not be presented as a fresh queue. `--collect-only` produces a snapshot and evidence cases without calling Jev. Run `--help` for controls.

When comparing revisions, save the old helper and candidate set, use the same snapshot and pinned model, run cold measurements separately from cache hits, and compare resulting person/PR/action membership as well as uncertain items. Record provider-reported input/output tokens, request counts, GitHub collection time, and wall time. Historical Codex billed tokens are unavailable unless separately recorded; do not substitute source-text size or Jev tokens for them. Matching outputs prove regression consistency, not independently measured accuracy.

For rule changes, verify effective-review transitions, bot/draft exclusions, blocking and independent reviewers, handoffs, stale-approval rules, progress caps, pagination, cache invalidation, and partial failure. Use the policy below as the behavioral contract. The offline tests in `scripts/test_*.py` encode these rules; run `python3 -m unittest` from `scripts/` after any change.

## Human work and ownership

Identify bots from the GitHub actor type, bot flag, and known automation accounts. Exclude CodeRabbit, Copilot, dependabot, github-actions, other bots, and their reviews from human work and review-pass counts. Also recognize automation posting under a service account when there is clear evidence. A human reviewer explicitly adopting a bot finding creates human feedback; a mere reply to the bot, or any reply by the PR author, does not.

For each reviewer, reconstruct the latest effective decisive review. A later approval supersedes their change request; a COMMENTED review alone does not. Account for dismissals and stale-approval rules. A push alone does not clear a change request, and an outdated thread is not necessarily resolved. GitHub's aggregate reviewDecision may include bots, so it is insufficient for human classification.

Assign actions as follows:

- **Respond to human review:** default to the PR author, or an explicitly designated fix owner supported by assignment/comment evidence. Include active human CHANGES_REQUESTED reviews and unresolved actionable human feedback, including clear requests in COMMENTED reviews or issue comments. The response may be a code fix, an answer to a question, or a clarification. Exclude thanks, approvals, resolved discussion, and feedback already handed back for re-review. Link the feedback and name its human source; one person's response entries on a PR merge into one entry naming every reviewer, and each named reviewer stays blocked from re-review. Assignees alone do not prove who should review or that every assignee must fix the PR.
- **Review:** assign each explicitly requested human reviewer. Team requests belong in a separate `Team review requested` group, without assigning every team member personally. Include only reviews currently owed. If an author fix blocks re-review, list the author's response action and omit the waiting reviewer until a handoff makes their review actionable. Keep independent review requests that can proceed.
- **Re-review:** use an active renewed request or a clear author handoff after fixes that the reviewer has not subsequently answered. A historical request consumed by a later submitted review or removed from current requests is not a current handoff. If the author says the feedback is addressed and asks for re-review, give the previous human reviewer the next action even if GitHub still shows CHANGES_REQUESTED. Mark this as inferred when it is not a formal request. New commits alone only suggest possible readiness; keep the fix obligation marked `confirm fixes / request re-review` until the handoff is supported. Do not infer that every previous reviewer owes another review.
- **Reviewer unassigned:** include a compact `Reviewer needed` group only when a human review is actually required now and no reviewer is named. Do not invent a personal assignment from authorship, collaborator membership, or CODEOWNERS alone. Requesting a reviewer is not a separate personal task in this report.
- Conflicts, failed checks, and other merge blockers are context for an already eligible human action. They never create a standalone action entry.

Omit a PR from the action report when its only outstanding work is bot feedback. Keep it only if there is also a currently actionable human review or response to human feedback. A bot-only aggregate CHANGES_REQUESTED state is not evidence of human work. Bot blockers can still prevent actual merging of an otherwise included PR; show that fact without assigning bot feedback as a human fix task.

Omit drafts unless there is an explicit active human review request or an outstanding response to human review feedback; an inferred handoff alone does not qualify a draft. Include qualifying drafts under the responsible person, label them draft, and score them 0%. Omit ready-to-merge PRs and PRs waiting only for CI, bots, or merge operations from the action groups; Urgent and High ones still appear under Merge priority.

## Review passes and progress

Progress is a workflow estimate, not GitHub's percentage, a completion measurement, or a probability of merging. Use the same rubric on every invocation and briefly state it in the report.

A pass is a human review round, not the number of reviewers, comments, or commits. A round is a non-author human review that approves, requests changes, has a body, or starts a review thread; a lone thread reply is not a round. Merge commits only bring in the base branch and are not author revisions. Pass 1 covers the first human review round. Increment only after a human feedback round, an author revision/handoff, and a renewed human review round. Several reviewers assessing the same revision belong to the same pass, so an awaited re-review joins the current pass when the latest revision is the one that pass assessed, and starts the next pass otherwise. A pending request to a reviewer who already reviewed is a re-review. Bot activity never advances a pass. Use commit IDs, timestamps, review requests and author handoffs together. If history is ambiguous, show `pass uncertain` or `at least pass N`, not a fabricated exact count.

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

## Merge priority and attention

Each PR gets two separate ratings. **Priority** is how soon merging helps the project, security first. **Attention** is how much review care the merge demands. They are independent: a Light docs PR can be High, and a Critical SQL migration can be Low.

[scripts/merge_signals.py](scripts/merge_signals.py) computes both. Priority uses two Jev questions per PR, asked over the title, description, labels, and up to 200 changed paths (highest attention first): does the PR fix, mitigate, or harden against a security weakness, and is that weakness reachable in deployed code now. Code maps the answers to levels:

| Priority | Rule |
| --- | --- |
| Urgent | `Urgent Ticket` label on the PR, or a security fix whose weakness is live now. |
| High | Security fix without confirmed live exposure. |
| Medium | `possible security fix` (Jev reading of 0.5 or more, not decisive), or a bug fix (`fix`/`hotfix` title or `JMV-B-` ticket) touching non-Light files. |
| Low | Features, maintenance, and PRs touching only Light files. |

Security readings below 0.5 rank as no. An unavailable judgment keeps the rule-based level and says `security not assessed`.

Attention is the highest tier among changed files, by the first-match path rules in `RULES`: **Critical** (SQL migrations, production deploy, backend auth/security), **Careful** (backend code, dependencies, frontend auth/permissions), **Standard** (frontend code, CI, scripts, unrecognized files), **Light** (tests, docs, skills and agent docs, translations, generated files, review config). Non-Light changes of 2000 lines or more raise Standard or Careful one tier. A truncated file list makes attention provisional. Change tiers in `RULES`, not in prose; the report names the reasons behind each level.

**Verify security before returning the report.** `merge.json` lists in `verify` every PR rated from a security fix and every possible security fix; an `Urgent Ticket` label needs no verification and stays Urgent. For each, read the description and `gh pr diff NUMBER --repo EnerZam/JouleMV`, then decide whether the PR closes a real weakness and whether deployed code exposes it. Edit the report so each verified priority matches the evidence, append `agent verified` to it, and move any PR now rated Urgent or High into the Merge priority section. Verification is complete when every `verify` PR is confirmed or re-rated. A verified priority is still a triage judgment, not a security audit.

## Teams-ready report

Return the message itself, ready to copy into Microsoft Teams. Use bold report and owner headings, `- [ ]` checkboxes with bold action and PR number, a separate progress/detail line, blank lines between actions, and one full PR URL per action on its own line. The checkbox report omits discussion links and routine unknown-check text. Avoid Markdown tables, code fences, nested lists, HTML, and introductory commentary outside the message. Write in the user's language. A plain name or GitHub login is a label, not a working Teams mention.

Start with `JouleMV review actions` and collection date/time/timezone, then a short count of unique PRs and people with pending actions. Show average estimated progress for unique included non-draft PRs only, with its denominator. This is progress of the action queue, not the whole repository. Never double-count a PR listed under multiple people. Use `N/A` when the denominator is zero; label partial data and its coverage explicitly.

After the header and coverage lines, show **Merge priority · security first**: every non-draft Urgent and High PR, including PRs without pending human actions, with its reason, attention, next action or approval count, blockers, and PR link. Follow it with the unclear-security list and counts per priority level.

Group only people who currently owe an action. Sort responses to human feedback first, then reviews, oldest waiting first within each action. Deduplicate person/PR/action entries. Use this shape, replacing placeholders with live evidence:

**Person name / GitHub login**

- [ ] **Respond to human review · #NUMBER** — PR title  
  **30%** · Pass 1. Address REVIEWER's feedback about TOPIC.  
  Priority **Medium** (bug fix) · Attention **Critical** (SQL migration).  
  FULL_PR_URL

- [ ] **Review · #NUMBER** — PR title  
  **50%** · Pass 2. Re-review the author's fixes.  
  Priority **Low** (docs, skills, or tests only) · Attention **Light** (skills and agent docs).  
  FULL_PR_URL

Include short team-request or `Reviewer needed` groups only when a review is currently actionable and a person cannot be named. In `Reviewer needed`, name the PR author in each checkbox without assigning the review to that author. Every PR entry gets an action, title, PR link, pass, estimated percentage, priority, and attention. Show brief material blockers when they affect the next action. Mark uncertain ownership or pass counts clearly.

Omit people with no pending actions, completed reviews, assignment inventories, bot-only work, standalone CI/conflict fixes, and ready-to-merge lists outside Merge priority. With no qualifying actions, say `No pending human review actions found.` Mention missing access if that conclusion is incomplete.

End with one short line explaining that percentages estimate workflow progress and later human passes advance the score, subject to merge blockers, and naming the priority and attention scales. Do not perform a code review or send the message as part of generating this report.
