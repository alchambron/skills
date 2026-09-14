---
name: github-inline-review
description: Publish the review already in the conversation as a GitHub Request changes review, with line-specific findings attached to exact diff lines. Invoke without arguments after a code review, or use when asked to publish existing review findings.
---

# GitHub inline review

Publish actionable findings as real GitHub review threads in Files changed. Mentioning `file:line` in a review body or PR conversation comment does not attach a comment to the code.

## Scope and authorization

Calling `$github-inline-review` in Codex or `/github-inline-review` in Claude Code with no arguments means: take the latest completed code review in this conversation and publish it on its PR as `REQUEST_CHANGES`. This invocation authorizes submission. Proceed without asking the user to repeat the review, mark findings, supply line numbers, choose a verdict, or confirm publication. Explicit draft, preview, local-only, or alternative-verdict instructions override this default. A request to create or edit this skill is configuration work, not an instruction to publish a review.

Use the latest completed review and any subsequent user corrections as the source. Extract findings and code locations from its prose and file links automatically. Preserve the review's substantive content, priorities, explanations, and validation caveats. Revalidate against the current PR, but do not start a new broad review or invent findings. If no completed review or custom comment exists, ask for the missing review unless the user also requested that you perform one.

Resolve the target from the PR associated with that conversation review first, then from the current repository and branch if they identify one unambiguously. No PR argument is required when context establishes the target. Ask only if the target remains missing or ambiguous after checking. Creating a PR, committing code, and merging are outside this skill.

Automatic selection during an ordinary code review does not itself authorize posting. When there is no explicit invocation or prior publishing authorization, prepare the exact review payload before asking whether to publish.

## Custom comment

Treat text supplied after the skill invocation as an additional user-authored comment to publish alongside the conversation review. The user does not need a label, flag, or special format. Preserve its wording and meaning; do not treat it as a replacement review or a request to investigate or implement a change. Clear instructions about the target PR, draft mode, or verdict still control execution rather than becoming comment text.

If the custom comment identifies specific code, resolve and verify its location using the same anchor rules as review findings, then publish it inline. Otherwise include it in the review body under "Additional comment". If its intended code location cannot be established, retain the comment in the body instead of guessing a line. Include custom text in duplicate detection and publication readback. New custom text counts as new content even when the earlier review has already been published. When no conversation review exists, publish the custom comment alone using the default verdict, unless the user overrides it; do not require a prior review or invent extra findings.

For example, `$github-inline-review Please add a regression test for the empty result case.` publishes the existing review plus that exact additional comment. Attach it inline only if the relevant location can be verified from context.

## Prepare anchors

1. Resolve the GitHub host, base repository, PR number, current head SHA, and base SHA. For forks, use the repository that owns the PR. Use authenticated `gh api` or an available GitHub tool that supports structured inline comments. Read [GitHub API examples](references/github-api.md) when using `gh`.
2. Fetch the PR's current files and diff, including every page, and existing review comments. Pin the review to the inspected head SHA. Local working-tree line numbers are evidence only after comparison with that exact revision. If the diff is incomplete, retrieve the missing hunks before mapping affected findings.
3. For each finding, identify the smallest relevant line or short range in a diff hunk. Use the exact repository-relative `filename` from GitHub's PR files response, including its current name for renamed files. Use old-file line numbers with `LEFT` for deletions, and new-file line numbers with `RIGHT` for additions or displayed context. File line numbers are not diff offsets.
4. Check each anchor against the hunk and code. Starting at `@@ -old,count +new,count @@`, context advances both counters, `-` advances only old, and `+` advances only new. Headers and `\ No newline at end of file` are not source lines. Set `line` to the final line; include `start_line` and `start_side` only for a multi-line range. Prefer a single side within one hunk.
5. Keep one actionable finding per thread. Explain the concrete trigger, consequence, and suggested correction concisely. Avoid reposting an existing equivalent finding at the same code location; account for outdated comments when comparing. Link existing threads in the result.

If the exact location is outside the diff, anchor to a directly relevant changed call site only when the comment accurately explains that relationship. Otherwise retain it as an explicitly labeled unanchored finding in the review summary. Binary files and unavailable hunks may also be unanchorable. Never invent an anchor or quietly replace all inline findings with a general comment.

Preparation is complete when every finding is mapped to a verified diff location, matched to an existing thread, or explicitly identified as unanchorable.

## Publish

Create a local JSON payload containing the reviewed `commit_id`, `event: REQUEST_CHANGES`, a brief overall summary in `body`, and structured `comments` with `path`, `body`, `line`, and `side`. Keep the main body to the overall assessment, finding count or broad themes, and relevant validation caveats. Put each line-specific finding's title, explanation, and suggested correction only in its own inline comment, with enough context to understand it independently; do not repeat or enumerate inline findings in the main body. General or explicitly unanchorable findings and non-inline custom comments belong only in the body under their own labels. Convert local file links to GitHub links for the reviewed revision. If revalidation shows a finding is resolved, exclude it from requested changes and report that adjustment to the user. Read [GitHub API examples](references/github-api.md) for the payload and submission commands. A tool must expose equivalent diff-location fields; a body-only comment tool cannot perform this task.

Default to `REQUEST_CHANGES`; use another verdict only when explicitly requested. If no actionable findings or user-supplied custom comment remain, explain that there are no changes to request and do not fabricate a blocking review. Reuse existing inline threads instead of duplicating them, but still submit the requested verdict and review body if that review has not already been published. If an equivalent Request changes review is already submitted for this content and revision, return its URL instead of duplicating it. If GitHub rejects the verdict, report the actual error and retain the prepared payload; do not silently downgrade to `COMMENT`.

Immediately before submission, re-fetch the PR head and base SHAs. If either changed, revalidate the findings and rebuild their anchors against the refreshed diff. Submit one review containing the inline comments where supported. A pending review is not published; submit it only if it belongs to this task. Leave unrelated pending reviews alone.

After a validation error, inspect the error, refresh the diff, and correct the actual mapping problem. Retry once after correction. After a timeout or ambiguous response, read existing reviews and comments first to determine whether the write succeeded. Retry only confirmed missing content; if success cannot be determined, stop and report uncertainty instead of risking duplicate comments.

## Verify publication

Read the submitted review and all of its comments back from GitHub. Verify the review is submitted with state `CHANGES_REQUESTED` for the default verdict, the body contains the intended summary without duplicating inline findings, the commit matches the reviewed SHA, and every intended new inline finding has a returned comment with the expected body, path, side, line or range, and `html_url`. Check that the body and inline threads together preserve all intended content, including any general findings or custom comments. Confirm a non-null current `line` for an active anchor. If the PR changed meanwhile, report comments that are already outdated rather than treating them as current.

Return the review URL, the number of verified inline comments, and any duplicate, unanchored, outdated, or failed findings. Include comment links when useful. An API success without readback is published but unverified, not verified completion.
