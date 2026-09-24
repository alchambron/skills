# Skills

37 skills organized by purpose. Each skill folder includes its original instructions and supporting files. Some skills contain JouleMV conventions or depend on specific tools and services; see their `SKILL.md` for requirements.

## Categories

| Category | Purpose | Skills |
| --- | --- | --- |
| [Frontend conventions](frontend-conventions/) | Keep UI implementation consistent | [component-conventions](frontend-conventions/component-conventions/SKILL.md), [column-definition](frontend-conventions/column-definition/SKILL.md), [dropdown-utils](frontend-conventions/dropdown-utils/SKILL.md), [date-time-formatting](frontend-conventions/date-time-formatting/SKILL.md), [react-compiler](frontend-conventions/react-compiler/SKILL.md), [translation](frontend-conventions/translation/SKILL.md), [figma-import](frontend-conventions/figma-import/SKILL.md) |
| [Forms and API integration](forms-and-api-integration/) | Build modals, connect backend data, and handle errors consistently | [modal-config](forms-and-api-integration/modal-config/SKILL.md), [modal-forms](forms-and-api-integration/modal-forms/SKILL.md), [modal-patterns](forms-and-api-integration/modal-patterns/SKILL.md), [query-rules](forms-and-api-integration/query-rules/SKILL.md), [error-handling-conventions](forms-and-api-integration/error-handling-conventions/SKILL.md) |
| [Testing and quality](testing-and-quality/) | Verify behavior, enforce checks, and guide manual testing | [vitest](testing-and-quality/vitest/SKILL.md), [vitest-component](testing-and-quality/vitest-component/SKILL.md), [vitest-stories](testing-and-quality/vitest-stories/SKILL.md), [screen-test-fixtures](testing-and-quality/screen-test-fixtures/SKILL.md), [verification](testing-and-quality/verification/SKILL.md), [browser-testing](testing-and-quality/browser-testing/SKILL.md), [playwright](testing-and-quality/playwright/SKILL.md), [manual-test-guide](testing-and-quality/manual-test-guide/SKILL.md), [wcag-audit-patterns](testing-and-quality/wcag-audit-patterns/SKILL.md) |
| [Git and review workflow](git-and-review-workflow/) | Publish reviews, manage review queues, resolve conflicts, and ship changes | [github-inline-review](git-and-review-workflow/github-inline-review/SKILL.md), [joulemv-pr-sensitivity](git-and-review-workflow/joulemv-pr-sensitivity/SKILL.md), [joulemv-review-queue](git-and-review-workflow/joulemv-review-queue/SKILL.md), [respond](git-and-review-workflow/respond/SKILL.md), [resolving-merge-conflicts](git-and-review-workflow/resolving-merge-conflicts/SKILL.md), [yeet](git-and-review-workflow/yeet/SKILL.md) |
| [Architecture and planning](architecture-and-planning/) | Clarify domain concepts, improve module boundaries, and challenge designs | [codebase-design](architecture-and-planning/codebase-design/SKILL.md), [domain-modeling](architecture-and-planning/domain-modeling/SKILL.md), [improve-codebase-architecture](architecture-and-planning/improve-codebase-architecture/SKILL.md), [grilling](architecture-and-planning/grilling/SKILL.md), [grill-with-docs](architecture-and-planning/grill-with-docs/SKILL.md) |
| [Agent collaboration and writing](agent-collaboration-and-writing/) | Write agent instructions, transfer context, improve prose, and guide human setup | [writing-for-agents](agent-collaboration-and-writing/writing-for-agents/SKILL.md), [handoff](agent-collaboration-and-writing/handoff/SKILL.md), [unslop](agent-collaboration-and-writing/unslop/SKILL.md), [wizard](agent-collaboration-and-writing/wizard/SKILL.md) |
| [Personal workflows](personal-workflows/) | Create and troubleshoot running workouts | [coros-workouts](personal-workflows/coros-workouts/SKILL.md) |

## Layout

```text
category/
└── skill-name/
    ├── SKILL.md
    └── supporting files, when supplied
```

The category folders organize this repository. Each nested folder containing `SKILL.md` is an individual skill. Existing metadata, scripts, references, assets, and license files are preserved.

These are copies of the local skills. Editing this repository does not update the installed copies automatically.

## Import notes

The descriptions in `dropdown-utils` and `figma-import` were quoted to fix YAML parsing without changing their wording. All other skill files match their local sources.

The bundled skill validator rejects existing `disable-model-invocation` metadata in `handoff`, `grill-with-docs`, and `improve-codebase-architecture`, plus `argument-hint` in `handoff`. These fields are preserved from the source skills. All 37 frontmatter blocks parse as YAML; 34 skills pass the bundled validator.
