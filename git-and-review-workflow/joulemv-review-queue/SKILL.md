---
name: joulemv-review-queue
description: Produce a Teams-ready JouleMV action list grouped by person, covering pending human reviews and responses to human review feedback, with PR links and estimated merge progress.
---

# JouleMV review queue

Produce a fresh, read-only report for `EnerZam/JouleMV`. Inspect all open PRs to find pending human actions across the team. Report only reviews to give and responses or fixes owed to human review feedback. Assignment, authorship, or past participation alone is not an action. Format the answer for copying into Microsoft Teams; sending it requires an explicit user request. Do not post comments, request reviews, edit PRs, or merge anything.

## Collect evidence

Use authenticated GitHub CLI or an available GitHub connector. Read [references/github-data.md](references/github-data.md) for collection commands and pagination requirements.

1. Fetch every open PR, including drafts, to discover current obligations. Identify people from those obligations; a complete collaborator roster is unnecessary for this action-only report.
2. For each PR collect author, assignees, requested users/teams, full submitted review history, review threads and replies, issue comments, commit history, current head SHA, base branch, draft status, review decision, mergeability, and individual checks. Fetch applicable branch protection/rules when available.
3. Resolve current human obligations using the rules below. Inspect comment text when needed to distinguish actionable feedback from discussion. Keep evidence links for decisions about fixes or re-review.
4. Before reporting, ensure all pages were collected. Refresh any PR whose head changed during collection. Explicitly identify inaccessible data and unknown readiness; never silently treat missing data as a clean result.

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
