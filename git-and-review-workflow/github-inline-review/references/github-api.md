# GitHub API examples

Use GitHub CLI authentication already configured for the target host. These examples use `github.com`; substitute the resolved host and repository. Set `repo` to the base `OWNER/REPO` and `pr` to the PR number. Keep review text in JSON files written with a JSON serializer or a quoted heredoc so Markdown backticks and dollar signs cannot execute as shell code.

## Read the target

```bash
gh auth status --hostname "$host"
gh api --hostname "$host" "repos/$repo/pulls/$pr" > pr.json
gh api --hostname "$host" --paginate --slurp "repos/$repo/pulls/$pr/files?per_page=100" > files-pages.json
gh api --hostname "$host" -H 'Accept: application/vnd.github.v3.diff' "repos/$repo/pulls/$pr" > pr.diff
gh api --hostname "$host" --paginate --slurp "repos/$repo/pulls/$pr/comments?per_page=100" > comments-pages.json
gh api --hostname "$host" --paginate --slurp "repos/$repo/pulls/$pr/reviews?per_page=100" > reviews-pages.json
```

Use an isolated temporary directory for these artifacts. `--slurp` wraps paginated arrays in an outer array; flatten one level when reading them. Extract `head.sha` and `base.sha` from `pr.json`. Verify they remain stable after fetching the diff. A missing `patch` is not evidence that a file has no changes. If using a local fallback, fetch the exact commits and compare their merge base to the head, matching the PR's three-dot comparison. Confirm the resulting hunk against GitHub before posting.

## Submit one review

Illustrative `review.json`, with a single-line finding and a separate multi-line finding. Replace all example values with validated findings and the inspected SHA.

```json
{
  "commit_id": "REVIEWED_HEAD_SHA",
  "event": "REQUEST_CHANGES",
  "body": "Requesting changes for two correctness issues. Details and suggested corrections are attached to the relevant code lines.",
  "comments": [
    {
      "path": "src/example.ts",
      "line": 42,
      "side": "RIGHT",
      "body": "[P1] Handle an empty result before dereferencing it. When the lookup returns no rows, this access throws and the request fails. Return the existing not-found response first."
    },
    {
      "path": "src/other.ts",
      "start_line": 18,
      "start_side": "RIGHT",
      "line": 21,
      "side": "RIGHT",
      "body": "[P2] Preserve the caller's filter when constructing this query. Otherwise filtered exports include rows outside the requested selection."
    }
  ]
}
```

```bash
gh api --hostname "$host" --method POST "repos/$repo/pulls/$pr/reviews" --input review.json > published-review.json
```

Use `line` and `side`, not the legacy `position` offset. `gh pr comment` and a body-only `gh pr review` do not create inline threads. Omitting `event` creates a pending review. If a connector requires a pending-review workflow, create it, add structured inline comments, and submit that specific review with the intended event.

## Read back

Set `review_id` from the create response, never from a guessed latest review.

```bash
gh api --hostname "$host" "repos/$repo/pulls/$pr/reviews/$review_id" > verified-review.json
gh api --hostname "$host" --paginate --slurp "repos/$repo/pulls/$pr/reviews/$review_id/comments?per_page=100" > verified-comments-pages.json
```

Check `submitted_at`, review state `CHANGES_REQUESTED` for the default verdict, `commit_id`, the complete review body, and each comment's location and body. If the review-comments listing omits modern location fields, fetch the individual comment through `GET /repos/{owner}/{repo}/pulls/comments/{comment_id}`. Preserve the returned `html_url` values for the user.

Official references: [create and inspect reviews](https://docs.github.com/en/rest/pulls/reviews#create-a-review-for-a-pull-request), [inline comment fields](https://docs.github.com/en/rest/pulls/comments#create-a-review-comment-for-a-pull-request).
