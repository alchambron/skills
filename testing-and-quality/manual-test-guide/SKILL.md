---
name: manual-test-guide
description: After completing a code review, or when given a review by Codex or Claude, produce detailed instructions for the user to test the changes themselves in the app. Also use when asked how to manually test a reviewed PR or change.
---

# Manual test guide

Turn the completed review and its code changes into an executable manual test guide for the user. Keep the review findings intact and deliver the guide after them. This skill prepares instructions; it does not execute the tests, fix code, or publish a review.

## Establish the target

1. Reuse the review in the conversation, whether written by Codex or Claude. If it comes from another session, use the supplied review text, file, or accessible link; do not imply access to the other agent's conversation. If no review is available, request it or the PR reference, and identify any provisional guide based only on code.
2. Resolve the reviewed PR, branch, base, and commit when available. Inspect the actual change set, including the merge-base diff for a PR even when the worktree is clean. If the current head differs from the reviewed revision, identify the mismatch and ground the guide in the stated target revision.
3. Read repository instructions before inspecting implementation. Trace changed behavior to app routes, menu entries, translated labels, forms, permissions, and data requirements. Use existing tests as clues to scenarios, not evidence that manual testing passed.

Finish this step when every distinct changed user flow and actionable review finding has either a scenario or an explicit reason it cannot be checked through the app. Group implementation-only changes by their affected behavior; avoid a checklist per file. A review with no findings still needs tests of the changed behavior.

## Build runnable scenarios

Start with the main changed flow, then review-finding reproductions and directly affected regressions. Include validation, empty data, permissions, persistence, navigation/backtracking, dates/locales, or exports only when the diff or review makes them relevant.

For each scenario provide:

- An unchecked box, stable ID such as `MT-01`, and a short behavior-focused title.
- The purpose and related review finding, if any.
- Preconditions: account role, tenant/project, necessary records and their state, and relevant feature settings. Share common setup once.
- The exact navigation path and starting state. Use verified UI labels; label code-inferred navigation as unverified and avoid inventing controls or URLs.
- Numbered actions with concrete input values and an observable expected result at each meaningful checkpoint. Replace “verify it works” with the value, message, selected state, record count, downloaded content, or other visible outcome to check. Derive calculated expectations from a stated input example.
- What indicates failure. For an unresolved finding, distinguish correct behavior from the defect the reviewed revision may currently exhibit; reproducing a known defect is not a successful acceptance test.
- Cleanup or restoration when the scenario changes data. Prefer disposable test records in a development or staging environment; call out irreversible effects where relevant to the actual scenario.

Choose sample data the user can create through the UI when possible. If a scenario depends on an existing record, give selection criteria rather than an invented record ID. Mark unavailable prerequisites as blocked and still provide independent scenarios.

For backend-only or infrastructure changes, identify an observable app consequence where one exists. If the app cannot validate the change, state that limitation and give a separate technical check only when it is concrete and supported by the repository. Keep developer commands and internal implementation details out of the click-through steps.

## Deliver the guide

Use the user's language. Begin with the target revision/environment, a short explanation of what changed, and shared setup. Confirm that the app must be running the target revision; an unknown deployment is an explicit prerequisite, not assumed coverage. Include build/start instructions only when needed and verified from repository configuration.

Present scenarios as a prioritized checklist, with core checks first and additional relevant regressions afterward. Scale detail to the change while keeping every scenario independently followable. Include references to the review or source where they clarify an uncertainty, outside the user actions.

End with:

- Coverage gaps: unavailable data, unverified navigation, and checks that require API, database, or live-system evidence. Keep static review, automated tests, and observed browser results distinct.
- A compact result format the user can paste back: `Scenario ID | Pass / Fail / Blocked | Actual result | Screenshot or error (if useful)`, plus the tested environment and build/commit if known. Leave all results untested until execution is reported or observed.

Before delivering, check that every scenario has a reachable starting point, usable input data, and an observable pass/fail condition. If a missing detail prevents those, name the missing detail rather than filling it with a guess.
