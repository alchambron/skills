---
name: joulemv-code-review
description: Review a JouleMV pull request or current branch read-only using Matt Pocock's Standards and Spec axes plus a verified regression check. Use when asked to review JouleMV code or invoked as $joulemv-code-review.
---

# JouleMV code review

This is a JouleMV adaptation of Matt Pocock's `code-review` skill. Read the installed [`code-review` skill](../code-review/SKILL.md) before each review and apply its fixed-point, Standards, and Spec method. This wrapper supplies the GitHub issue-tracker context and adds a defect check and PR-head validation without altering the repository setup.

## Target and evidence

1. Read the repository `AGENT.md`. For frontend changes, read `frontend/AGENT.md` and `frontend/.claude/skills/INDEX.md`; load only relevant review conventions. Keep the review read-only.
2. Resolve an explicitly supplied PR or ref first. Otherwise resolve the PR attached to the current branch with `gh pr view`. If no unique PR exists, review the user-specified fixed point; ask for one only if the target remains ambiguous.
3. For a PR, obtain its number, URL, title, body, base branch, current head SHA, commits, and changed files from GitHub. Fetch its base and head without checking out or changing the user's branch. Verify the inspected head SHA matches GitHub. Use the merge base for `git diff <base>...<head>` and record the commit list. If the user explicitly chose another fixed point, honor it and identify that scope in the result.
4. GitHub is the issue tracker for `EnerZam/JouleMV`. Read linked issues with `gh issue view` and the PR description/comments when they define requirements. If a repository `docs/agents/issue-tracker.md` exists, follow it for issue retrieval. This GitHub configuration supplies the tracker context when that file is absent. If no usable spec exists, say so and skip the Spec axis.

## Review

5. Run Matt's Standards and Spec axes in independent parallel subagents, with the exact diff and commit list. The Standards reviewer receives applicable `AGENT.md` files and relevant coding conventions plus Matt's smell baseline. The Spec reviewer receives the PR/issue requirements. Preserve the source and evidence for each candidate finding.
6. Independently inspect changed behavior and its callers for correctness, regressions, security, data handling, and missing edge cases. Follow relevant code paths beyond the diff. Reproduce or trace candidate defects where practical. Use targeted checks only when they resolve a concrete uncertainty; label static, local-test, browser, staging, and production evidence accurately.
7. Validate every proposed defect against the current PR diff and surrounding code. Include the concrete failure condition, impact, and precise changed line. Drop speculative or already-addressed findings. Recheck GitHub head before finishing; if it moved, refresh the review against the new head.

## Result

Lead with verified, actionable defects ordered by severity, with file and line links. State "no actionable findings" when none were found; that is not an approval. Follow with separate `Standards` and `Spec` sections for remaining observations, retaining their distinct sources and distinguishing documented-rule violations from judgment-call smells. State missing-spec and verification limits. Keep this command read-only: do not edit code, submit a GitHub review, post comments, or approve the PR. Never submit `APPROVE` or declare the user's approval through this skill. An explicit later `$github-inline-review` invocation can publish verified findings separately.
