---
name: joulemv-review-launcher
description: Show my actionable JouleMV PR reviews, let me choose one, then launch a T3 Code review that publishes verified Request changes findings only. Use when I ask to launch or pick my next PR review in T3 Code.
---

# JouleMV review launcher

Use this skill from a T3 Code thread to launch one review at a time. This thread is the picker; the selected PR gets its own T3 Code thread and T3-managed worktree. Do not register an unselected PR with the picker thread.

## Show the user's review choices

Use the existing [JouleMV review queue](../joulemv-review-queue/SKILL.md) as the source of human actions. Run its collector freshly; do not turn authorship, past participation, or a new commit alone into a review task. The queue may contain uncertain actions: show only confirmed personal `Review` and `Re-review` actions in the numbered picker, and mention any incomplete coverage.

```sh
mkdir -p ~/.cache/joulemv-review-launcher
OUTPUT=$(mktemp -d ~/.cache/joulemv-review-launcher/queue.XXXXXXXX)
zsh -ic 'python3 /Users/alchambron/.codex/skills/joulemv-review-queue/scripts/review_queue.py --output '"$OUTPUT"' --cache-dir /Users/alchambron/.cache/joulemv-review-queue/jev --format checkbox'
LOGIN=$(gh api user --jq .login)
python3 /Users/alchambron/.codex/skills/joulemv-review-launcher/scripts/pending_reviews.py --queue-output "$OUTPUT" --login "$LOGIN"
```

Keep the generated `OUTPUT/pending-reviews.json` path for the launch step. Show the numbered list and ask which **one** PR the user wants to review. If a number or PR URL was supplied with the invocation, use that selection only if it appears in the fresh picker. If the queue is incomplete, say so; do not claim the picker covers every PR.

## Launch the selected review

After selection, call the bundled launcher with the fresh picker JSON and selected PR number:

```sh
node /Users/alchambron/.codex/skills/joulemv-review-launcher/scripts/launch_review.mjs \
  --pending-json "$OUTPUT/pending-reviews.json" --pr NUMBER
```

The launcher must recheck that the PR is open and still at the picked head before creating anything. On drift, refresh the queue and ask the user to select from the updated list. It creates the T3 worktree from the main JouleMV checkout, starts a new T3 thread with `$joulemv-code-review` on the selected PR, links that PR to the new thread, and verifies the link. The review first reports its verified findings in that thread. If and only if at least one finding warrants Request changes, the launched thread then invokes `$github-inline-review` with no arguments to publish those findings as `REQUEST_CHANGES` and verifies the publication. With no verified change request, or only uncertain observations, it publishes nothing. The launcher never approves the PR or submits a GitHub `APPROVE` review.

Report the new thread link, worktree path, selected PR, and whether the review was started. If creation or linking partially fails, report the exact confirmed state and resume safely without creating a duplicate thread. Keep T3 auth tokens private and short-lived. Do not imply that a review has completed merely because its thread was created.
