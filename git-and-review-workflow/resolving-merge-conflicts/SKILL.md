---
name: resolving-merge-conflicts
description: "Use when you need to resolve an in-progress git merge/rebase conflict."
---

1. **See the current state** of the merge/rebase. Check git history, conflicting files, and existing staged and unstaged work. Read the applicable `AGENT.md` rules, including approval for changes affecting three or more files. Honor authorization already given for this task.

2. **Find the primary sources** for each conflict. Understand deeply why each change was made, and what the original intent was. Read the commit messages, check the PRs, check original issues/tickets.

3. **Resolve each hunk.** Preserve both intents where possible. Where incompatible, pick the one matching the merge's stated goal and note the trade-off. Do **not** invent new behaviour. If intent remains ambiguous after investigation, stop and request user direction. When no safe resolution exists, aborting the merge or rebase is an option; preserve pre-existing work and obtain direction before an abort that could discard it.

4. Discover the project's **automated checks** and run them, typically typecheck, then tests, then format. Fix failures caused by the merge within the authorized scope. If checks remain failing or cannot run, stop before completing the merge and report the evidence for user direction.

5. **Finish the merge/rebase.** After checks pass, review the full staged diff and stage only files related to this merge or rebase. Preserve unrelated work; if unrelated changes are already staged, resolve that boundary with the user before committing. Follow applicable approval rules before completion. If rebasing, continue through the remaining commits with the same intent and validation checks.
