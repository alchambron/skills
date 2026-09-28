---
name: joulemv-review-launcher
description: Show my actionable JouleMV PR reviews, accept one, several, or all, and launch separate T3 Code reviews that publish verified Request changes findings or run pictured browser scenarios. Use when I ask to launch or pick my next PR review in T3 Code.
---

# JouleMV review launcher

Use this skill from a T3 Code thread to launch selected reviews one at a time. This thread is the picker; each selected PR gets its own T3 Code thread and T3-managed worktree. Do not register an unselected PR with the picker thread.

## Show the user's review choices

Use the existing [JouleMV review queue](../joulemv-review-queue/SKILL.md) as the source of human actions. Run its collector freshly; do not turn authorship, past participation, or a new commit alone into a review task. The queue may contain uncertain actions: show only confirmed personal `Review` and `Re-review` actions in the numbered picker, and mention any incomplete coverage.

```sh
mkdir -p ~/.cache/joulemv-review-launcher
OUTPUT=$(mktemp -d ~/.cache/joulemv-review-launcher/queue.XXXXXXXX)
zsh -ic 'python3 /Users/alchambron/.codex/skills/joulemv-review-queue/scripts/review_queue.py --output '"$OUTPUT"' --cache-dir /Users/alchambron/.cache/joulemv-review-queue/jev --format checkbox'
LOGIN=$(gh api user --jq .login)
python3 /Users/alchambron/.codex/skills/joulemv-review-launcher/scripts/pending_reviews.py --queue-output "$OUTPUT" --login "$LOGIN"
```

Keep the generated `OUTPUT/pending-reviews.json` path for the launch step. Show the numbered list and ask which PR numbers or URLs the user wants, accepting **all** for every confirmed entry. If selections were supplied with the invocation, use only entries in the fresh picker. Deduplicate selections. If the queue is incomplete, say so; do not claim the picker covers every PR.

## Launch the selected reviews

For each selected PR, call the bundled launcher separately and sequentially with the fresh picker JSON and its number:

```sh
node /Users/alchambron/.codex/skills/joulemv-review-launcher/scripts/launch_review.mjs \
  --pending-json "$OUTPUT/pending-reviews.json" --pr NUMBER
```

The launcher rechecks that each PR is open, targets `work`, and remains at the picked head. It fetches and pins the current `origin/work` commit, then creates a named T3 branch and worktree at that exact commit. It verifies the T3 setup record, branch, and worktree revision before reporting success. On head drift, refresh the queue and retry that PR only if it remains a confirmed selection; otherwise report it as skipped. It starts a new T3 thread pinned to Codex `gpt-6-sol` and links the PR to that thread. Continue with other selected PRs after an individual failure, but do not blindly retry an uncertain thread creation. A reused thread keeps its original prompt, model, and worktree base; do not claim the new workflow ran there.

In each new review thread, choose one branch after the code review:

1. Verify the initial worktree commit equals the pinned `origin/work` commit. Complete `$joulemv-code-review` against the pinned PR head and report its verified findings. The review fetches the PR head without checking it out.
2. **Finding branch:** If the review reports any verified actionable defect, invoke `$github-inline-review` with no arguments, publish only `REQUEST_CHANGES`, and verify the GitHub review and inline comments. Then end the thread. If publication fails, report the failure and still end the thread. Do not invoke `$manual-test-guide`, start the app, or open the browser in this branch. Never approve a PR on GitHub.
3. **No-findings branch only:** Invoke `$manual-test-guide` for the completed review. For live testing, merge the pinned PR head into the clean isolated branch based on the pinned `origin/work` commit. If the merge succeeds, require a merge commit whose first parent is that base and whose second parent is the reviewed PR head. If the merge conflicts, abort it and leave the T3 worktree at `origin/work`. Use `git worktree add --detach` at a unique path outside the T3 worktree for the pinned PR head. Test from that second worktree and label the evidence **PR-head-only**; integration with current `work` remains unverified. Do not resolve semantic conflicts automatically. From the verified test worktree, run `python3 /Users/alchambron/.codex/skills/joulemv-review-launcher/scripts/agent_run.py doctor`, then `start`. Check that `start` returns the selected integration or PR-head commit and has `ok: true` with `status: ready` or `already_ready`. Pass its `frontendUrl` to T3 Code `preview_open` after `preview_status`, and execute every runnable guide scenario with the `preview_*` tools. Save a `preview_snapshot({save:true})` for each scenario's visible observed outcome, inspect the saved image, and embed it in the thread's report. If the image is blank, blurred, or clips the result while page text exists, wait for rendering or resize the preview and capture it again until the result is legible. Mark scenarios Pass, Fail, or Blocked with expected and actual results. If startup, a prerequisite, or target revision cannot be verified, report the gap and do not embed an empty browser frame. The CLI replays the branch's versioned tenant migrations only in the isolated clone; it does not recreate root seed data. Treat missing root fixtures as a test gap. Retain the logs path for failures.
4. If browser testing exposes a reproducible defect in the reviewed change, stop further scenarios, validate it against the current PR head, add it to the review, and invoke `$github-inline-review` for `REQUEST_CHANGES`. Environment failures alone do not become GitHub findings. A clean review and passing browser scenarios still never approve the PR.

After any `start` attempt, always run `python3 /Users/alchambron/.codex/skills/joulemv-review-launcher/scripts/agent_run.py stop` from the test worktree before ending the thread, including after failures or an early exit. Verify its JSON says `databaseDropped: true` or `already_stopped`; retry or report a cleanup error if it does not. Remove a disposable PR-head worktree only after cleanup succeeds. The CLI also expires abandoned runs after two hours.

Report each selected PR, thread link, worktree path, and whether its review was started or an existing thread was reused. If creation or linking partially fails, report the exact confirmed state and resume safely without creating a duplicate thread. Keep T3 auth tokens private and short-lived. Do not imply that a review or browser test has completed merely because its thread was created.
