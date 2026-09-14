---
name: vitest-stories
description: Rules for creating *.stories.test.tsx files that validate Storybook stories using composeStories from @storybook/react-vite. These tests run in the storybook Vitest project (browser environment with Playwright/Chromium), not in the default jsdom project. Use alongside the vitest core skill.
---

# Vitest — Storybook Story Tests

> **Prerequisite**: Read the `vitest` core skill first for imports, test structure, and assertions.

---

## 1. Storybook Test Project

Story tests run in a **separate Vitest project** (`storybook`) with a different environment:

| Setting         | Value                                                |
| --------------- | ---------------------------------------------------- |
| Project name    | `storybook`                                          |
| Environment     | Browser (Playwright, headless Chromium)              |
| Include pattern | `src/**/*.stories.test.{ts,tsx}`                     |
| Setup file      | `.storybook/vitest.setup.ts`                         |
| CI              | **Not run in CI** — CI only runs `--project=default` |

The setup file calls `setProjectAnnotations` from `@storybook/react-vite` to apply Storybook decorators and parameters.

---

## 2. File Placement

Place `*.stories.test.tsx` next to the component's story file:

```
src/shared/components/DatePicker/
├── DatePicker.tsx
├── DatePicker.stories.tsx        ← Storybook stories
└── DatePicker.stories.test.tsx   ← Story tests (this file)
```

---

## 3. Template

```typescript
import { describe, it, expect } from "vitest";
import { composeStories } from "@storybook/react-vite";
import { render, screen } from "@testing-library/react";
import * as stories from "./Component.stories";

const { Default, WithData, Disabled } = composeStories(stories);

describe("Component Stories", () => {
  describe("Default Story", () => {
    it("renders successfully", () => {
      render(<Default />);
      expect(screen.getByRole("button")).toBeInTheDocument();
    });
  });

  describe("WithData Story", () => {
    it("displays data from story args", () => {
      render(<WithData />);
      expect(screen.getByText("expected text")).toBeInTheDocument();
    });
  });

  describe("Disabled Story", () => {
    it("renders in disabled state", () => {
      render(<Disabled />);
      expect(screen.getByRole("button")).toBeDisabled();
    });
  });
});
```

---

## 4. Rules

- **Always** import `composeStories` from `@storybook/react-vite` (not `@storybook/testing-react`).
- **Always** import the stories module as `import * as stories from "./Component.stories"`.
- **Always** destructure composed stories before the `describe` block.
- **Always** give each story its own `describe` block named `"{StoryName} Story"`.
- **Always** test, at minimum, that each story **renders without crashing**.
- **Never** use `vi.mock()` in story tests — stories should render with the decorators and args defined in the story file. If a story needs mocking, that should be done via Storybook decorators/parameters in the `*.stories.tsx` file.
- **Never** import `vi` unless you actually need it for assertions (e.g., `vi.fn()` for controlled story callbacks). Most story tests only need `describe`, `it`, `expect`.

---

## 5. What to Assert

Story tests validate that **Storybook stories render correctly** with their configured args and decorators. Keep assertions simple:

| What to check                  | Example                                                                 |
| ------------------------------ | ----------------------------------------------------------------------- |
| Renders without error          | `expect(screen.getByRole("button")).toBeInTheDocument()`                |
| Displays correct initial state | `expect(screen.getByText("2 selected")).toBeInTheDocument()`            |
| Applies story args             | `expect(screen.getByPlaceholderText("MM/DD/YYYY")).toBeInTheDocument()` |
| ARIA state from args           | `expect(mainButton).toHaveAttribute("aria-expanded", "true")`           |

**Do NOT** duplicate the full behavioral test suite from the `*.test.tsx` file. Story tests focus on **rendering correctness**, not user interaction flows.

---

## 6. Running Story Tests

```bash
# Run all story tests (requires Playwright browsers installed)
npx vitest run --project=storybook

# Install Playwright browsers if needed
npx playwright install --with-deps
```

Story tests are **not run in CI** — the GitHub Actions workflow only runs `--project=default`.
