# Skills

56 skills organized by purpose. Each skill folder includes its original instructions and supporting files. Some skills contain JouleMV conventions or depend on specific tools and services; see their `SKILL.md` for requirements.

## Categories

| Category | Purpose | Skills |
| --- | --- | --- |
| [Frontend conventions](frontend-conventions/) | Keep UI implementation consistent | [component-conventions](frontend-conventions/component-conventions/SKILL.md), [column-definition](frontend-conventions/column-definition/SKILL.md), [dropdown-utils](frontend-conventions/dropdown-utils/SKILL.md), [date-time-formatting](frontend-conventions/date-time-formatting/SKILL.md), [react-compiler](frontend-conventions/react-compiler/SKILL.md), [translation](frontend-conventions/translation/SKILL.md), [figma-import](frontend-conventions/figma-import/SKILL.md) |
| [Forms and API integration](forms-and-api-integration/) | Build modals, connect backend data, and handle errors consistently | [modal-config](forms-and-api-integration/modal-config/SKILL.md), [modal-forms](forms-and-api-integration/modal-forms/SKILL.md), [modal-patterns](forms-and-api-integration/modal-patterns/SKILL.md), [query-rules](forms-and-api-integration/query-rules/SKILL.md), [error-handling-conventions](forms-and-api-integration/error-handling-conventions/SKILL.md), [typesafe-ai](forms-and-api-integration/typesafe-ai/SKILL.md) |
| [Testing and quality](testing-and-quality/) | Verify behavior, enforce checks, and guide manual testing | [vitest](testing-and-quality/vitest/SKILL.md), [vitest-component](testing-and-quality/vitest-component/SKILL.md), [vitest-stories](testing-and-quality/vitest-stories/SKILL.md), [screen-test-fixtures](testing-and-quality/screen-test-fixtures/SKILL.md), [verification](testing-and-quality/verification/SKILL.md), [browser-testing](testing-and-quality/browser-testing/SKILL.md), [playwright](testing-and-quality/playwright/SKILL.md), [manual-test-guide](testing-and-quality/manual-test-guide/SKILL.md), [wcag-audit-patterns](testing-and-quality/wcag-audit-patterns/SKILL.md), [create-verification-skill](testing-and-quality/create-verification-skill/SKILL.md) |
| [Git and review workflow](git-and-review-workflow/) | Monitor pull requests, publish reviews, manage review queues, resolve conflicts, and ship changes | [babysit-pr](git-and-review-workflow/babysit-pr/SKILL.md), [github-inline-review](git-and-review-workflow/github-inline-review/SKILL.md), [joulemv-pr-sensitivity](git-and-review-workflow/joulemv-pr-sensitivity/SKILL.md), [joulemv-review-queue](git-and-review-workflow/joulemv-review-queue/SKILL.md), [joulemv-pr-triage](git-and-review-workflow/joulemv-pr-triage/SKILL.md), [respond](git-and-review-workflow/respond/SKILL.md), [respond-all](git-and-review-workflow/respond-all/SKILL.md), [resolving-merge-conflicts](git-and-review-workflow/resolving-merge-conflicts/SKILL.md), [yeet](git-and-review-workflow/yeet/SKILL.md), [pr-classify](git-and-review-workflow/pr-classify/SKILL.md), [code-review](git-and-review-workflow/code-review/SKILL.md), [joulemv-code-review](git-and-review-workflow/joulemv-code-review/SKILL.md), [joulemv-review-launcher](git-and-review-workflow/joulemv-review-launcher/SKILL.md), [review-all](git-and-review-workflow/review-all/SKILL.md), [joulemv-pr-risk](git-and-review-workflow/joulemv-pr-risk/SKILL.md), [update-my-prs](git-and-review-workflow/update-my-prs/SKILL.md) |
| [Architecture and planning](architecture-and-planning/) | Clarify domain concepts, improve module boundaries, and challenge designs | [codebase-design](architecture-and-planning/codebase-design/SKILL.md), [domain-modeling](architecture-and-planning/domain-modeling/SKILL.md), [setup-matt-pocock-skills](architecture-and-planning/setup-matt-pocock-skills/SKILL.md) |
| [Agent collaboration and writing](agent-collaboration-and-writing/) | Coordinate agents, write instructions, transfer context, improve prose, and guide human setup | [parallel-investigation](agent-collaboration-and-writing/parallel-investigation/SKILL.md), [delegated-implementation](agent-collaboration-and-writing/delegated-implementation/SKILL.md), [writing-for-agents](agent-collaboration-and-writing/writing-for-agents/SKILL.md), [unslop](agent-collaboration-and-writing/unslop/SKILL.md), [wizard](agent-collaboration-and-writing/wizard/SKILL.md), [caveman](agent-collaboration-and-writing/caveman/SKILL.md), [configure-context-awareness](agent-collaboration-and-writing/configure-context-awareness/SKILL.md), [find-skills](agent-collaboration-and-writing/find-skills/SKILL.md), [no-comments](agent-collaboration-and-writing/no-comments/SKILL.md) |
| [Security and operations](security-and-operations/) | Audit security, track vulnerabilities, and inspect production errors | [joulemv-sentry-prod-events](security-and-operations/joulemv-sentry-prod-events/SKILL.md), [security-audit](security-and-operations/security-audit/SKILL.md), [security-watch](security-and-operations/security-watch/SKILL.md) |
| [Personal workflows](personal-workflows/) | Manage running workouts and remote development previews | [coros-workouts](personal-workflows/coros-workouts/SKILL.md), [remote-preview](personal-workflows/remote-preview/SKILL.md) |

## Layout

```text
category/
└── skill-name/
    ├── SKILL.md
    └── supporting files, when supplied
```

The category folders organize this repository. Each nested folder containing `SKILL.md` is an individual skill. Existing metadata, scripts, references, assets, and license files are preserved.

These are copies of the local skills. Editing this repository does not update the installed copies automatically. The collection covers personal skills installed directly under `~/.codex/skills`, `~/.agents/skills`, and `~/.claude/skills`; built-in system skills and plugin-managed bundles are excluded.

## Install or update review-all

Install the complete folders below into your agent's personal skills directory (`~/.codex/skills` for Codex or `~/.claude/skills` for Claude Code). Refresh the whole installed folder when updating, including scripts, references and assets; copying only `SKILL.md` leaves the runner and report generator behind. Keep a backup of any local customizations. Start a new agent session after updating and confirm it loads the intended `review-all/SKILL.md`, rather than an older project or plugin copy.

| Repository folder | Installed folder |
| --- | --- |
| `git-and-review-workflow/review-all` | `review-all` |
| `git-and-review-workflow/code-review` | `code-review` |
| `git-and-review-workflow/joulemv-code-review` | `joulemv-code-review` |
| `git-and-review-workflow/github-inline-review` | `github-inline-review` |
| `testing-and-quality/manual-test-guide` | `manual-test-guide` |
| `testing-and-quality/browser-testing` | `joulemv-browser-testing` |

The repository uses category-relative dependency links; flattened installations resolve those dependencies through the installed skill catalog as described in `review-all`. The browser skill's declared name is `browser-testing`; its installed folder above matches the sibling links used by existing personal skills. Remove or disable an obsolete `joulemv-review-all` entry only after confirming the new installation, so invocations cannot select the old workflow accidentally.

The full workflow requires all five companion skills above. Its dependency checker runs before batch work; missing or invalid skills stop the batch and produce installation links to this repository. Copy the updated `review-all` folder as a unit so its checker and command guards stay together.

Invoke `/review-all` in Claude Code or `$review-all` in Codex. Clean reviews must proceed through `manual-test-guide`, execution of the planned live scenarios, and an illustrated HTML report. T3 Code displays that report inline; other hosts receive the generated `scenario-guide.html` file. No separate request to run tests is needed. Missing runtime prerequisites remain explicit blockers, and token telemetry is optional outside Codex. Test databases, credentials and saved login sessions are machine-local prerequisites; they are not distributed with the skills.

## Multi-agent setup for Codex

From this repository, install the two skills in your personal Codex skills directory:

```bash
mkdir -p ~/.codex/skills
cp -R agent-collaboration-and-writing/parallel-investigation ~/.codex/skills/
cp -R agent-collaboration-and-writing/delegated-implementation ~/.codex/skills/
```

Then add this section to `~/.codex/AGENTS.md` so it applies across projects:

```markdown
## Multi-agent work

For substantial tasks with independent workstreams, use subagents when parallel work or separate context will materially improve speed or coverage. Use the `parallel-investigation` skill for independent, read-only lines of inquiry and the `delegated-implementation` skill for multi-part code changes. Keep short or tightly dependent work in the main agent. Give each subagent a bounded task and expected result; the main agent owns coordination, integration, and final verification. Assign separate files or worktrees for concurrent edits. Follow the user's scope and project instructions; delegation grants no additional permission for commits, publication, deployment, or external changes.
```

The global instruction is loaded when a new Codex session starts. The skills do not replace more specific project workflows, such as PR review.

## Pull request naming setup

Install the skill in your personal Codex directory:

```bash
mkdir -p ~/.codex/skills ~/.agents/skills
cp -R git-and-review-workflow/pr-classify ~/.codex/skills/
ln -s ~/.codex/skills/pr-classify ~/.agents/skills/pr-classify
```

Add this section to `~/.codex/AGENTS.md` so PR creation follows the rules across repositories:

```markdown
## Pull request naming

Before creating any pull request, read and apply `~/.codex/skills/pr-classify/SKILL.md`. Classify the actual diff and use its title and description directly in the authorized creation workflow, including drafts and each stack layer. Follow explicit repository naming policies; otherwise use the skill's default format with an optional verified primary ticket. A missing ticket must not block classification. Read back the saved metadata after creation.
```

The default title is `type(scope): Description [optional primary ticket]`. Omit unused scope parentheses and ticket brackets. New Codex sessions load the global instruction; no manual skill invocation is required for PR creation.

For Claude Code, link the same skill into `~/.claude/skills/pr-classify` and add the instruction above to `~/.claude/CLAUDE.md`.

## Import notes

The descriptions in `dropdown-utils` and `figma-import` were quoted to fix YAML parsing without changing their wording. The `review-all` repository copy uses category-relative links for its testing dependencies; its installed copy uses sibling skill links. The `respond` fixer reference uses a category-relative link to browser-testing; the installed copy uses a sibling skill link. `respond-all` calls `respond`'s scripts through their installed `~/.codex/skills` paths. `review-all` bundles its runtime lock helper and shares the same machine-wide lock directory with `respond`. `review-all`'s scenario runner installs pinned Playwright 1.60.0 and its headless Chromium shell into `~/.codex/review-state/review-all/tools/playwright` (about 210 MB) on first use; per-repository test helpers live outside the repository in `~/.codex/review-state/review-all/helpers`. The repository's `browser-testing` includes the same Navigator and scripted-runner contract as the installed `joulemv-browser-testing`. Other imported files preserve their source content at the time of import. Installed copies may have changed since. The newly imported skills retain their installed sibling links and external prerequisites: the shared Sentry wrapper, the separate `cc-context-awareness` installation, and existing security-watch templates, inventory, and browser installation. These external resources are not bundled.

All 56 skill frontmatter blocks parse as YAML. Imported source metadata is retained.
