# GitHub data collection

Use `EnerZam/JouleMV` explicitly, even when invoked from another directory. These commands are read-only. Requires authenticated `gh` with repository access. A connector exposing equivalent data is an acceptable fallback.

## Inventory and full history

REST pagination avoids the default PR list limit:

```sh
gh api --method GET 'repos/EnerZam/JouleMV/pulls?state=open&per_page=100' --paginate --slurp
gh api --method GET 'repos/EnerZam/JouleMV/collaborators?per_page=100' --paginate --slurp
```

`--slurp` returns an array of pages. Flatten before processing. Pipe to a separate `jq` process if needed; `gh --slurp` cannot be combined with its `--jq` option. For each open PR, substitute its numeric ID for NUMBER:

```sh
gh pr view NUMBER --repo EnerZam/JouleMV --json number,title,url,author,assignees,reviewRequests,reviewDecision,isDraft,headRefOid,baseRefName,mergeable,mergeStateStatus,statusCheckRollup,updatedAt
gh api --method GET 'repos/EnerZam/JouleMV/pulls/NUMBER/reviews?per_page=100' --paginate --slurp
gh api --method GET 'repos/EnerZam/JouleMV/pulls/NUMBER/commits?per_page=100' --paginate --slurp
gh api --method GET 'repos/EnerZam/JouleMV/issues/NUMBER/comments?per_page=100' --paginate --slurp
gh api --method GET 'repos/EnerZam/JouleMV/issues/NUMBER/timeline?per_page=100' --paginate --slurp
gh pr checks NUMBER --repo EnerZam/JouleMV --required --json name,state,bucket,link,workflow
```

Check commands may exit nonzero when checks fail or are pending, or report that no required checks were found. Preserve and inspect their output; distinguish this from authentication/network errors. Confirm absent requirements against applicable rules before treating checks as satisfied. Fetch individual check runs/statuses with REST pagination if a rollup is incomplete. Evaluate required checks separately from optional checks, including accepted skipped/neutral conclusions where applicable.

## Review threads

Save this query to a temporary file and pass it through `gh api graphql -F query=@FILE -F number=NUMBER --paginate --slurp`. It paginates threads. If any thread's comments have another page, fetch the remaining comments separately by thread node ID before classifying that thread.

```graphql
query($number: Int!, $endCursor: String) {
  repository(owner: "EnerZam", name: "JouleMV") {
    pullRequest(number: $number) {
      headRefOid
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated
          comments(first: 100) {
            pageInfo { hasNextPage endCursor }
            nodes {
              author { __typename login }
              body createdAt url
              pullRequestReview { id state submittedAt commit { oid } }
            }
          }
        }
      }
    }
  }
}
```

Use the corresponding connection's cursor for nested comments; a top-level thread cursor does not paginate comments. Likewise inspect and paginate requested-reviewer connections if using GraphQL instead of REST. REST `pulls/NUMBER/requested_reviewers` supplies current user/team requests when needed.

## Merge rules

Inspect the PR's actual base branch. URL-encode its name when inserting it into an endpoint path:

```text
GET repos/EnerZam/JouleMV/branches/BASE/protection
GET repos/EnerZam/JouleMV/rules/branches/BASE
```

Read applicable approval counts, stale-review dismissal, latest-push approval, CODEOWNER, conversation resolution, required checks and other enforced gates. Use CODEOWNERS and changed paths when needed to establish required owners; ownership does not itself prove a personal review request. A denied or unavailable rule endpoint means unknown rules, not zero requirements. Distinguish visible satisfied requirements from an assertion that all merge gates are satisfied. Retry UNKNOWN mergeability once after collecting other data, then label it unknown if unresolved.

Source for CLI request and pagination behavior: [GitHub CLI API manual](https://cli.github.com/manual/gh_api).
