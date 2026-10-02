---
name: pr-classify
description: Classify pull requests or proposed changes and draft consistent PR titles and descriptions, with optional ticket references. Use before creating a PR, or when asked to categorize a PR, apply PR naming rules, or prepare PR metadata.
---

# PR Classify

Propose a primary type, meaningful scope, optional ticket references, risk, title, and description based on the actual changes. Repository conventions override the defaults below. Classification is metadata preparation, not a code review or evidence of merge readiness.

## Establish the evidence

Read applicable repository instructions and PR templates, naming rules, and label configuration. Recent PR titles can reveal a convention when documented rules are absent; distinguish observed practice from a mandatory rule.

Resolve the requested target before classifying:

- **Existing PR:** read its current title, body, base/head, changed files, and full diff. With GitHub CLI, use `gh pr view <target> --json number,url,title,body,baseRefName,headRefName,headRefOid,files` and `gh pr diff <target>`. Include every changed file; inspect relevant source when the diff alone is ambiguous.
- **Current branch:** identify the intended base from the user's context or repository default branch, then inspect the full merge-base diff and commits unique to the branch. `git diff <base>...HEAD` and `git log <base>..HEAD --format=%s` help establish that boundary. Ask for the base only if it cannot be resolved reliably.
- **Uncommitted work:** inspect staged and unstaged tracked changes with `git diff HEAD`, and read relevant untracked files identified by `git status --short`. State which changes the proposal covers. If both branch commits and working changes exist, clarify whether the target is working changes alone or the complete prospective PR when context does not resolve it.
- **Supplied artifacts:** use the provided diff and intent. When only a summary is available, mark the result provisional and name the missing evidence.

Use intent to interpret the code, and surface conflicts between the stated purpose and observed changes. An empty diff needs a target correction, not a fabricated classification.

## Choose type and scope

Choose one primary type according to the resulting behavior:

| Type | Rule |
|---|---|
| `feat` | Adds a capability or intentionally expands existing behavior. |
| `fix` | Corrects behavior that violates the intended behavior or contract. |
| `refactor` | Changes implementation while preserving observable behavior. |
| `perf` | Primarily improves performance while preserving functional behavior. |
| `docs` | Changes documentation only. |
| `test` | Changes tests or test tooling without changing production behavior. |
| `chore` | Maintenance, dependencies, build, CI, or configuration work without a more specific primary purpose. |

Supporting tests and documentation follow the primary implementation type. A dependency update that fixes a demonstrated bug may be `fix`; a database migration takes the type of the behavior it enables. Ticket prefixes and file extensions do not determine type. Claims of preserved behavior or performance improvement must match the evidence; explain uncertainty when they cannot be established.

Use the repository's scopes where available. Otherwise choose a short, meaningful domain or subsystem such as `permissions`, `charts`, `imports`, `database`, or `ci`. Omit scope when the change is truly cross-cutting and no single scope represents it honestly.

For mixed work, choose the main purpose and explain secondary changes. Flag unrelated purposes that would benefit from separate PRs; classify the current patch without splitting it automatically. Identify breaking compatibility changes separately, whatever the type.

## Handle optional tickets

Use exact identifiers or links explicitly supplied by the user or clearly associated with the target in its title, body, branch name, or unique commits. Record where each reference came from. Distinguish issue/ticket references from PR numbers, release numbers, and incidental mentions; omit uncertain references and flag the ambiguity.

- With no reference, report `Ticket: Not provided` and continue. Omit the ticket suffix from the title; no placeholder or invented identifier is needed.
- Preserve identifier spelling, prefix, and numbering. Do not infer the meaning of prefixes such as `JMV-B` or `JMV-F`.
- When references conflict, surface the conflict and use the user's explicit selection if given. Otherwise omit a primary ticket until resolved.
- With several associated tickets, list them in the description. Include one in the title only when it is clearly the primary ticket.
- Use a neutral `Related tickets` section. Automatic closing syntax such as `Closes #123` requires an explicit instruction to close that issue or confirmed repository policy for that relationship. An external ticket number is not automatically a GitHub issue.

If repository policy requires a ticket, report the metadata gap and still classify the changes. Do not claim the proposed PR satisfies that policy.

## Assess risk independently

Explain consequences using the actual change and affected callers/data:

- **Low:** limited consequences, such as documentation, tests, or local cosmetic changes.
- **Moderate:** bounded runtime behavior or operational changes with an understood recovery path.
- **High:** changes affecting authorization or tenant isolation, credible data loss, breaking compatibility, or broad deployment behavior with substantial recovery implications.

Consider migration reversibility, API consumers, data scope, and deployment requirements. A migration or small diff alone does not establish risk. Mark risk provisional when consequences cannot be determined. Risk is neither a defect finding nor approval to merge.

## Draft the result

Follow repository formatting first. Otherwise use:

```text
<type>(<optional scope>): <clear description> [<optional primary ticket>]
```

Omit unused scope parentheses and ticket brackets. Describe the resulting behavior with a concise action verb. When using the default format, mark established breaking changes with `!` before the colon and explain the impact in the description.

Examples:

```text
fix(permissions): Restore chart export access [JMV-B-199]
refactor(charts): Simplify export configuration
chore(ci): Update preview build workflow
feat(api)!: Require an explicit dataset identifier
```

Return type, scope (or omitted), ticket references and provenance (or not provided), risk with rationale, proposed title, and a short classification explanation. Add a proposed description using the repository template when present; otherwise include a summary, validation, related tickets when available, and material compatibility/deployment considerations when applicable.

Validation statements must describe checks actually run or supplied as evidence. Label unavailable checks `Not run` or `Reported by author`; keep local results distinct from CI. Keep proposed test steps separate from results. Propose labels only when requested or required by repository policy, using known existing labels and distinguishing type from risk.

## Use the title when creating a PR

When the task already authorizes creating a new PR, apply these rules as part of that creation workflow. Inspect the complete diff against the intended base, then use the resulting title and description directly with the PR creation tool. With GitHub CLI, pass the classified title through `gh pr create --title` and the description through `--body-file` using a temporary UTF-8 file; preserve literal text and newlines. Do not stop at a title suggestion or request additional naming approval when PR creation is already authorized.

Apply the same naming rules to draft PRs and to each layer of a stack, using that layer's actual changes. Follow the task's existing commit, push, and publication authorization. After creation, read back the saved title, body, and URL and verify they match the prepared metadata; report or correct any discrepancy within the authorized scope. Register the PR with the thread when the environment requires it.

For classification-only requests, return the proposal. For an explicitly requested update to an existing PR, update only the authorized metadata, refresh the target/diff before updating, and read back the saved fields. Reclassify if the changes moved. Skill invocation alone does not authorize commits, branch renames, PR creation, label creation, merge, or publication.
