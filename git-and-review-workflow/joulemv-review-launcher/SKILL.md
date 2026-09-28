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

The launcher rechecks that each PR is open and still at the picked head before creating anything. On drift, refresh the queue and retry that PR only if it remains a confirmed selection; otherwise report it as skipped. It creates a T3 worktree from the main JouleMV checkout, starts a new T3 thread, links the PR to that thread, and verifies the link. Continue with other selected PRs after an individual failure, but do not blindly retry an uncertain thread creation. A reused thread keeps its original prompt; do not claim the new workflow ran there.

In each new review thread, follow this order:

1. Complete `$joulemv-code-review` and report its verified findings.
2. If a verified actionable finding warrants Request changes, invoke `$github-inline-review` with no arguments in that thread, publish only `REQUEST_CHANGES`, and verify the GitHub review and inline comments. Never approve a PR on GitHub.
3. If there is no verified change request, invoke `$manual-test-guide` for the completed review. Then execute every runnable guide scenario in the T3 Code collaborative browser against the reviewed revision. Use `preview_status`, `preview_open` when needed, and the `preview_*` tools. Save a `preview_snapshot({save:true})` for each scenario's observed outcome and embed each image in the thread's report. Mark scenarios Pass, Fail, or Blocked with expected and actual results. If a prerequisite or target revision cannot be verified, report the gap instead of claiming a pass; never invent screenshots.
4. If browser testing exposes a reproducible defect in the reviewed change, validate it against the current PR head, add it to the review, and invoke `$github-inline-review` for `REQUEST_CHANGES`. Environment failures alone do not become GitHub findings. A clean review and passing browser scenarios still never approve the PR.

Report each selected PR, thread link, worktree path, and whether its review was started or an existing thread was reused. If creation or linking partially fails, report the exact confirmed state and resume safely without creating a duplicate thread. Keep T3 auth tokens private and short-lived. Do not imply that a review or browser test has completed merely because its thread was created.
