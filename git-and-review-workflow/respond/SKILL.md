---
name: respond
description: Analyze or address review feedback on a GitHub pull request. Use when asked to respond to PR comments, assess reviewer requests, or plan fixes for review feedback.
---

# Respond to PR feedback

Use the PR number supplied by the user, if any. Otherwise, find the PR for the current branch with `gh pr view`. If neither identifies a PR, ask for its number. Read the repository's agent instructions before examining code. In JouleMV, use root `AGENT.md` and, for feedback touching `frontend/`, `frontend/AGENT.md` and its skill index.

## Establish the current feedback

1. Fetch the PR title, head, base, reviews, general comments, and all pages of inline review comments. Use the current repository from `gh repo view`, not a hard-coded owner or repo. Include comment IDs or URLs, authors, paths, lines, timestamps, and reply relationships so each item remains traceable.
2. Check review-thread resolution and later replies. Separate open human requests from resolved threads, superseded requests, bot suggestions, and questions already answered. Do not count replies as new independent findings.
3. Read the PR's merge-base diff and the relevant current code. Verify whether each requested change is still needed; a comment may refer to an older commit. Preserve the reviewer's observed behavior even when a test or static reading does not reproduce it.

## Decide and report

Present every review comment in a compact table, including comments that are resolved, superseded, or not actionable. Keep one row per original comment or thread; fold replies into that row. Link each comment and briefly state its current status in the Comment cell. Use these columns:

| Comment | Important? | Valuable? | Solution for this comment |
| --- | --- | --- | --- |
| [Reviewer: concise summary](comment URL) (open/resolved/already addressed) | Important / Not important — brief reason | Valuable / Not valuable — brief reason | Specific code change, verification, or reviewer reply; say “no change” and why when appropriate. |

Judge **importance** by whether the issue needs action before merge, especially for correctness, security, data loss, or a meaningful user regression. Judge **value** by whether the feedback is valid and improves the PR, even when it is not merge-blocking. If evidence is insufficient, say “Needs verification” in the relevant cell and name the check in the solution. Do not equate a resolved thread with a verified fix.

After the table, give a short ordered implementation plan and verification steps for the comments that need work. When a solution needs more than a few sentences, keep its table cell concise and add a numbered explanation below keyed to that row. State which items are already addressed and which need a reviewer answer. Avoid claiming a comment is resolved or a fix is deployed solely from local code or tests.

If the user asked only to analyze or invoked the skill without authorizing changes, stop after the plan and ask which actions to take. If the user has asked to fix feedback or reply to reviewers, carry out that authorized work, verify it, and report the exact threads addressed. Follow repository instructions for any additional approval gate. Post GitHub replies, push, or change review state only when the user's request authorizes those actions.
