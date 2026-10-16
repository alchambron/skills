---
name: joulemv-pr-triage
description: Order JouleMV pull requests needing human review, with verified security work and small quick reviews first, dependencies respected, and complex low-urgency changes last. Use for a ranked review list or deciding what to review next; use joulemv-review-queue for an owner-grouped Teams action report.
---

# JouleMV PR triage

Produce one fresh, numbered review order for `EnerZam/JouleMV`, followed by a separate waiting list. Keep GitHub read-only: this skill does not authorize reviews, comments, reviewer requests, branch changes, approvals or merges. Respect an explicitly requested repository, reviewer filter or narrower scope.

## Collect current evidence

Reuse the installed sibling [joulemv-review-queue](../joulemv-review-queue/SKILL.md) for collection, human-action interpretation and security verification. Read its instructions, resolve its script relative to that skill, and run it with a new output directory:

```sh
python3 SCRIPT --output OUTPUT --cache-dir ~/.cache/joulemv-review-queue/jev --format checkbox
```

Use the existing authenticated `gh` and TypeSafe setup; if the key comes from `.zshrc`, launch through `zsh -ic` without printing credentials. If collection or interpretation fails, use the sibling's documented fallback and label missing coverage. This skill replaces its owner-grouped report format with the ranking below; keep its ownership and evidence rules.

Read `metrics.json`, `snapshot.json`, `actions.json` and `merge.json`. Check collection coverage, unavailable judgments and ownership uncertainties separately. Obtain missing additions, deletions, changed-file counts, base/head branches and head SHAs from GitHub. Paginate beyond CLI defaults. Retain collection time, repository, coverage and head SHAs in local audit data.

Refresh GitHub inventory, review requests, conversations, mergeability and material check results for each invocation. Historical artifacts are examples or explicitly labeled replays, never a current queue. Before delivery, recheck inventory and heads; update affected classifications when they change. An unchanged head does not establish unchanged discussion or review state.

## Decide who can review now

- Main list: pending first reviews or re-reviews supported by a current request or clear author handoff. Deduplicate PRs and name every currently actionable reviewer on one entry.
- Separate waiting list: author fixes/responses, merge conflicts, unresolved ownership or handoff ambiguity, and dependencies that prevent a useful review. For this triage, conflicts move a PR to waiting even when code inspection remains possible; this differs from the sibling's owner-grouped action report. A blocked security PR stays visibly high priority in this list.
- An independent reviewer may proceed while another waits for fixes; name the actionable person and show the remaining blocker. A stack child can be reviewed before its parent merges when the parent is available and the diff is stable; order its parent first and disclose the dependency.
- Assignment, authorship, old participation and bot reviews do not establish a pending human action. Apply the sibling's effective-review and handoff rules, including actionable feedback in COMMENTED reviews.
- Use `Reviewer needed` only when a human review is actually needed and ownership is otherwise clear. Keep ambiguous obligations in the waiting list rather than guessing a reviewer.
- Exclude PRs whose human review is complete and whose only remaining work is CI, bots or merge operations; briefly account for exclusions. Bot approval alone never establishes human completion: retain a still-needed human review, using `Reviewer needed` when appropriate. Include drafts only with an explicit active human review request, label them, and identify their merge restriction. Flag test/disposable PRs for disposition instead of promoting them as quick wins.

## Rank the actionable PRs

Use these bands, in order, unless the user supplies a different urgency policy:

1. **Security:** verified fixes, mitigations or preventive hardening of authentication, authorization, tenant isolation, secrets or another real security weakness. Stronger evidence of serious active exposure outranks effort; when urgency is comparable, prefer smaller, more focused reviews. Keep preventive hardening and limited security corrections identifiable within this band.
2. **Other urgent work:** explicit urgent tickets, production incidents and concrete release blockers. Explain any reason to elevate an active incident above the default security band.
3. **Quick reviews:** small, bounded changes with limited behavioral impact and no unresolved blocker. Examples include copy, cursor feedback, sorting, display precision and isolated layout fixes.
4. **Ordinary fixes and features:** order by concrete urgency, estimated review effort and scope. An actionable re-review with fixes handed back can be an efficient next choice.
5. **Large, less urgent changes:** broad features, large migrations or refactors, dependency batches and large documentation or tooling stacks. Classify a focused migration by its actual scope and urgency rather than placing it here solely because it changes SQL. Preserve parent-before-child order within a stack.

Within comparable work, use smaller effective scope, fewer sensitive areas, clear handoff and older waiting time as tie-breakers. Explain material judgment calls; do not present a speculative numeric score or time estimate as measured.

**Dependencies constrain every band.** Build the stack from actual base/head branches and explicit PR/package references. A required parent precedes its child even when the child is smaller or appears more urgent. Promote a prerequisite needed by high-priority work and say why. Separate hard dependencies from overlapping files or suggested merge coordination. External package publication, version re-pinning and coordinated frontend/backend deployment are material dependencies, not implicit completion.

## Verify security and review effort

Read the description and actual diff for every security candidate in `merge.json.verify`, plus candidates uncovered during triage. Record whether each closes an existing weakness, adds preventive hardening, corrects an authentication response, or merely preserves permissions in a new feature/migration. A feature may include a real security fix; classify the specific change. SQL acknowledgment rules and approval-policy edits require care but are not automatically runtime security fixes.

Confirm deployed exposure only with evidence about the running version or reachable deployed path. Otherwise write `production exposure unverified`; code in a branch does not prove it is deployed. Downgrade false positives, retain genuine security work, and record an explicit result for every candidate. If a diff cannot be obtained, mark verification incomplete instead of claiming a confirmed security ranking.

Use the sibling's attention/path signals as a starting point, then inspect enough of likely quick wins to justify their placement. Review effort depends on changed behavior and blast radius, alongside file count and additions/deletions:

- Count tests/generated files separately from production behavior when they inflate a focused change.
- A one-file tenant SQL migration can need more care than a multi-file display change.
- Tiny changes to automatic approval eligibility, permissions or shared authentication routes deserve a policy/security review.
- Shared dropdowns, database writes, financial/energy calculations, transaction ordering and API contracts widen review scope even with small diffs.
- Large documentation stacks can be low runtime risk while still requiring substantial review time.

When GitHub rejects a large diff, use paginated file APIs or a local immutable base/head comparison if available. Preserve the limitation when only focused paths were checked. Keep triage distinct from a full correctness or security review, browser testing, and merge approval.

## Deliver the ordered list

Write in the user's language. Lead with collection time/timezone, coverage and the number of unique PRs in the review and waiting lists. Briefly state the ranking policy and any material uncertainty.

Use one global numbering sequence, with checkboxes. Each main entry contains:

- Linked PR number and descriptive title.
- Security/urgency or effort category, file count and additions/deletions.
- Actionable reviewer names; distinguish first review and re-review when known.
- One concrete reason for its position and any dependency or material blocker.

Example shape, with fresh values:

```text
1. [ ] #NUMBER — Title (full PR link)
   Security · 5 files · +120/−20 · Review: reviewer
   Require modify permissions for writes; compact scope.
```

Keep waiting items outside this numbered sequence, name the next responsible person when supported, and state what must happen before review. Keep security blockers first there. Optional workflow percentages use the sibling's conservative rubric and remain separate from priority or probability.

In T3 Code, when an inline list/table helps, use `html_preview` then `html_render`, with working PR links, readable narrow layouts and local checkboxes. Explain that checkboxes do not submit GitHub reviews. A numbered Markdown list remains the fallback; honor requests for plain text or Teams formatting. Save a copyable Markdown list and compact structured audit data when producing local artifacts. Follow the current environment's PR-linking requirements.

Completion means every collected PR has a review, waiting or exclusion disposition; each flagged security candidate has a verification result or visible limitation; dependencies are respected; and coverage/uncertainties are explicit. Do not reuse particular PR numbers, counts or positions from an earlier run.
