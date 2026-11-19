---
name: gh-event-wait
description: Wait for CI after a push or for a submitted review on a github.com pull request using GitHub CLI Event Streams. Use when the workflow allows a CLI wait.
---

# Wait for GitHub pull request events

Use the host's PR watcher when the workflow requires it. For a CLI wait, use the full PR URL and check the installed syntax with `gh event listen --help`. Run `gh event subscription list` with the same active account to check preview access. Listing is read-only. Listening can create or reuse a subscription, and subscriptions expire after 24 hours.

## Attach before reading state

1. Start the listener in a running command session. Keep stderr visible and wait for its attachment status before reading the PR in a separate command. Earlier events are not replayed.
2. Read current state immediately after attachment, even if you already read it before starting. If the requested condition is already met, stop the listener.
3. Use each event to refresh state. The full event's `data.api_url` identifies the object that changed. Re-read after reconnecting because events during a disconnect are missed. On timeout, do a final state read and report any outstanding condition. `--timeout` exits with status 8 if the deadline is reached first.

## CI after a push

Set `pr_url` to the PR URL and retain the pushed commit SHA. Start with the next check-suite completion:

```bash
gh event listen --resource "$pr_url" \
  --event com.github.pull_request.check_suite.status.changed.v1 \
  --jq .data.conclusion
```

For the full CI wait, add `--follow` before starting and set `--timeout` to the wait budget when needed. After attachment and each event, read `gh pr view "$pr_url" --json headRefOid,statusCheckRollup` to compare the rollup with the pushed SHA. Use `gh pr checks "$pr_url"` for check details. If the head changes, report that the target was superseded and verify that SHA separately if needed. Each suite emits its own conclusion. Stop when the expected checks for the target head settle or the deadline is reached. Report missing, pending, skipped, cancelled, failed, and passed checks according to the evidence.

## Submitted review

```bash
gh event listen --resource "$pr_url" \
  --event com.github.pull_request.review.submitted.v1 \
  --timeout 30m
```

After attachment and on each event, read `gh pr view "$pr_url" --json headRefOid,reviewDecision,reviews`. Verify the reviewer, verdict, and submission time against the request. For the next review, use the start of the wait as the cutoff for the initial read. Add `--follow` from the start if unrelated submissions could arrive first.

The default exits after one event. Use `--count N` for a fixed number of deliveries or `--follow` to stay attached until stopped. The count measures deliveries, including any irrelevant to the requested condition. To listen for several event types, repeat `--event` or pass comma-separated types.

## Fallback

On `gh event subscription list`, exit 1 with this stderr confirms the preview is disabled for the active account:

```text
failed to list subscriptions: event subscriptions are in preview and not enabled for this account: HTTP 403: Event subscriptions are not enabled for this account (https://api.github.com/event-subscriptions?per_page=30)
```

Match the explicit `Event subscriptions are not enabled for this account` message. A bare 403 leaves enrollment unknown. The exact error from `gh event listen` and live delivery remain unverified. If the CLI lacks the command or the PR is on GitHub Enterprise Server or GHE.com, use the applicable fallback as well.

- For PR CI, use `gh pr checks "$pr_url" --watch`. For a known Actions run, use `gh run watch <run-id> --repo OWNER/REPO --exit-status`. Verify the target head and expected checks in either case.
- For a review, use the host's review watcher if available. If polling is allowed, refresh the review snapshot at a bounded interval until the requested review appears or the wait budget expires.
