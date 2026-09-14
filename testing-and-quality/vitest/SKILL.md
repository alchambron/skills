---
name: vitest
description: Foundational rules for ALL Vitest tests in this project. Use when creating, modifying, or reviewing any .test.ts or .test.tsx file. Covers project configuration, file placement, imports, test structure (describe/it), assertions, DOM queries, interaction patterns (fireEvent vs userEvent), CLI commands, and conventions, including the fixture-backed screen-test workflow. For component-specific mocking patterns (vi.mock, vi.hoisted, react-i18next, RTK Query, toastBridge), see the vitest-component skill. For Storybook *.stories.test.tsx files, see the vitest-stories skill.
---

# Vitest — Core Rules

> **Related skills**: `vitest-component` (mocking & component patterns) · `vitest-stories` (Storybook story tests) · `react-compiler` (compiler directives and memoization exceptions)

## TL;DR

| Topic                          | Rule                                                                                                                          |
| ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------- |
| Test runner                    | Vitest 4.x with jsdom environment                                                                                             |
| File naming                    | `*.test.ts` / `*.test.tsx` (unit) or `*.stories.test.tsx` (storybook)                                                         |
| File placement                 | Co-located next to source file **OR** in a `__tests__/` subfolder                                                             |
| Imports                        | Use `{ describe, it, expect, vi }` from `"vitest"`                                                                            |
| Rendering                      | `@testing-library/react` (`render`, `screen`, `fireEvent`, `waitFor`)                                                         |
| User events                    | `@testing-library/user-event` for realistic interactions                                                                      |
| Globals                        | `globals: true` is set — `describe`, `it`, `expect`, `vi` are auto-imported but **explicitly import them anyway** for clarity |
| Run unit tests (CI / headless) | `npx vitest run --project=default` — only the default project, no watch, exits after completion                               |
| Run default project (interactive) | `npm test` — starts the **default** project in watch mode (`vitest --project=default --maxWorkers=2`)                      |
| Run all projects (interactive) | `npm run test:all` — starts **all** Vitest projects (default + storybook); requires TTY and Playwright                        |
| Run one file                   | `npx vitest run src/path/to/File.test.tsx`                                                                                    |
| Watch mode                     | `npm run test:watch`                                                                                                          |
| Coverage                       | `npm run test:coverage`                                                                                                       |
| CI command                     | `npx vitest run --project=default` (produces JUnit XML)                                                                       |
| Screen tests                   | Allowed for `src/screens/**` when they use recorded fixtures + generated schemas + `screenTestUtils.tsx`                     |

---

## 1. Project Configuration

Vitest is configured in `vite.config.ts` with **two projects**:

| Project     | Environment                   | Includes                                           | Setup files                                              |
| ----------- | ----------------------------- | -------------------------------------------------- | -------------------------------------------------------- |
| `default`   | jsdom                         | `src/**/*.test.{ts,tsx}`, `src/**/*.spec.{ts,tsx}` | `src/test/vitest.globals.ts`, `src/test/vitest.setup.ts` |
| `storybook` | browser (Playwright/Chromium) | `src/**/*.stories.test.{ts,tsx}`                   | `.storybook/vitest.setup.ts`                             |

**CI runs only the `default` project** via `npx vitest run --project=default`.

### Setup files (already configured — do NOT duplicate their logic in tests)

- **`src/test/vitest.globals.ts`** — Mocks `localStorage` globally.
- **`src/test/vitest.setup.ts`** — Runs `cleanup()` after each test, initializes `i18n` with all 3 locales, mocks `window.matchMedia`, `IntersectionObserver`, `ResizeObserver`, and `window.scrollTo`.

---

## 2. Testing Scope

**Current scope**: Tests are written for **shared components** (`src/shared/components/`), **utility functions** (`src/util/`), and **fixture-backed screen tests** (`src/screens/`).

### Screen-level tests are in scope

Screen tests are allowed when they follow the shared contract pattern:

- Validate committed JSON fixtures against generated Zod schemas.
- Render screens through `src/test/contract/screenTestUtils.tsx`.
- Mock the HTTP/request boundary instead of mocking RTK Query hooks directly.
- Reuse the real endpoint definition, `baseApi`, auth token wiring, and response unwrapping path.

**Do not** `vi.mock()` feature API hooks in screen test files. That bypasses the RTK Query path we want to verify.

### TDD loop for screens

TDD here means **Test-Driven Development**: follow a red-green-refactor loop.

1. **Red**: write a failing contract or rendering assertion in the screen test before changing production code.
2. Reuse an existing recorded fixture, or add/update a recordable endpoint in `src/test/contract/recordFixtures.ts`.
3. Run the single file headlessly to confirm the test fails for the expected reason:

```bash
npx vitest run --project=default src/screens/<Screen>/__tests__/<Screen>.test.tsx
```

4. **Green**: implement the smallest screen change needed to make the test pass.
5. **Refactor**: clean up the code and test while keeping the test green.
6. Re-record fixtures only when the backend payload/schema changed:

```bash
npm run record-fixtures
```

Local `pre-push` refreshes fixtures and schemas only. CI runs the headless Vitest suite on pull request updates and pushes to `work`.

### Existing compiler safety test

This repo already contains a guard test for `react-hook-form` compatibility with the React Compiler:

- [src/test/useNoMemoDirective.test.ts](../../../src/test/useNoMemoDirective.test.ts)

If a source file starts calling a React Hook Form hook, update the source to include `"use no memo";` instead of weakening or bypassing the test.

---

## 3. File Placement & Naming

### Co-located tests (default)

```
src/shared/components/DatePicker/
├── DatePicker.tsx
├── DatePicker.test.tsx          ← unit test
├── DatePicker.stories.tsx
└── DatePicker.stories.test.tsx  ← storybook stories test (see vitest-stories skill)
```

### `__tests__/` subfolder (for component sub-modules)

```
src/shared/components/Datatable/
├── filters/
│   └── __tests__/
│       ├── FilterUtils.test.tsx
│       └── DatatableFilter.integration.test.tsx
└── __tests__/
    └── DataTableExportButtons.test.tsx
```

### Screen tests

```text
src/screens/Building/
├── BuildingList.tsx
└── __tests__/
    └── BuildingList.test.tsx
```

### Naming conventions

| File type        | Pattern                            | Example                                |
| ---------------- | ---------------------------------- | -------------------------------------- |
| Unit test        | `{ComponentName}.test.tsx`         | `DatePicker.test.tsx`                  |
| Utility test     | `{utilName}.test.ts`               | `utils.test.ts`                        |
| Integration test | `{Feature}.integration.test.tsx`   | `DatatableFilter.integration.test.tsx` |
| Storybook test   | `{ComponentName}.stories.test.tsx` | `DatePicker.stories.test.tsx`          |

---

## 4. Imports

### Standard test imports

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
```

### Import rules

- **Always** import `describe`, `it`, `expect`, `vi` from `"vitest"` explicitly, even though globals are enabled.
- **Always** import `render`, `screen` from `@testing-library/react`.
- Use `fireEvent` for simple synthetic events (change, click).
- Use `userEvent` from `@testing-library/user-event` for realistic user interactions (typing, tabbing, sequential clicks).
- Place `vi.mock()` calls **before** the component import for readability. Vitest hoists them automatically at compile time, but keeping this order makes it visually clear which modules are mocked. If no mocks are needed, import the component directly.
- Import **types** with the `type` keyword: `import type { DateRange } from "react-day-picker"`.

### Import order in test files

```typescript
// 1. vitest imports
import { describe, it, expect, vi, beforeEach } from "vitest";
// 2. testing-library imports
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
// 3. vi.mock() calls (place before component import — Vitest hoists automatically)
vi.mock("react-i18next", () => ({ ... }));
// 4. Component under test
import { MyComponent } from "./MyComponent";
// 5. Type imports
import type { SomeType } from "./types";
```

---

## 5. Test Structure

### Describe / it nesting

Use nested `describe` blocks to group tests by **category**:

```typescript
describe("ComponentName", () => {
  const mockOnChange = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Rendering", () => {
    it("renders with default props", () => { ... });
  });

  describe("User Interactions", () => {
    it("calls onChange when value changes", () => { ... });
  });

  describe("Edge Cases", () => {
    it("handles empty data gracefully", () => { ... });
  });
});
```

### Common describe group names

- `Rendering` — Initial render, conditional rendering, props affecting UI
- `User Interactions` — Clicks, typing, navigation
- `Controlled Mode` / `Uncontrolled Mode` — Controlled vs uncontrolled behavior
- `Accessibility` — ARIA attributes, tab indices, roles
- `Custom Styling` — className, style props
- `Edge Cases` — Empty data, rapid clicks, missing handlers
- `Positioning` — Layout-specific props
- `Animation and Transitions` — Visual state changes

### it() descriptions

- Start with a **verb**: "renders", "calls", "displays", "updates", "clears", "opens", "closes", "disables"
- Be specific about what is tested and expected

```typescript
// GOOD
it("renders with placeholder when no value selected", () => { ... });
it("calls onChange when an option is selected", async () => { ... });

// BAD
it("works correctly", () => { ... });
it("test 1", () => { ... });
```

### Unfinished or future tests

```typescript
it.todo("should render combined formulas when provided");
describe.todo("Formula Validation");
```

---

## 6. Assertions & Queries

### DOM queries (prefer in this order)

1. `screen.getByRole("button", { name: "Save" })` — Accessible role queries (best)
2. `screen.getByLabelText("Name")` — Label association
3. `screen.getByPlaceholderText("Enter name...")` — Placeholder text
4. `screen.getByText("Submit")` — Visible text
5. `screen.getByDisplayValue("current value")` — Input current value
6. `screen.getByTestId("custom-id")` — data-testid (last resort)

### Negative queries

```typescript
expect(screen.queryByRole("button", { name: "1" })).not.toBeInTheDocument();
expect(screen.queryByText("error")).not.toBeInTheDocument();
```

### Async queries

```typescript
const option = await screen.findByText("Option 1");

await waitFor(() => {
  expect(screen.getByPlaceholderText("Search...")).toBeInTheDocument();
});
```

### Common matchers

```typescript
// Presence
expect(element).toBeInTheDocument();
expect(element).not.toBeInTheDocument();

// Attributes
expect(element).toHaveAttribute("aria-expanded", "true");
expect(element).toBeDisabled();

// Values
expect(input).toHaveValue("hello");
expect(input).toHaveDisplayValue("01/15/2024");

// CSS
expect(element).toHaveClass("custom-class");
expect(element).toHaveStyle({ width: "300px" });

// Mock calls
expect(mockFn).toHaveBeenCalled();
expect(mockFn).toHaveBeenCalledTimes(1);
expect(mockFn).toHaveBeenCalledWith(expectedArg);
expect(mockFn).toHaveBeenCalledWith(expect.objectContaining({ key: "value" }));

// Collections
expect(screen.getAllByTestId("TextFilter")).toHaveLength(1);

// Equality
expect(result).toBe("expected");
expect(result).toStrictEqual(["Col A", "Col C"]);
expect(result).toBeTruthy();
expect(result).toBeNull();
```

---

## 7. Interaction Patterns

### fireEvent (simple synthetic events)

```typescript
fireEvent.click(button);
fireEvent.change(input, { target: { value: "new value" } });
```

### userEvent (realistic user behavior — preferred for complex interactions)

```typescript
const user = userEvent.setup();
await user.click(button);
await user.type(input, "hello");
await user.clear(input);
await user.tab();
await user.keyboard("{Escape}");
```

| Use `fireEvent`                     | Use `userEvent`                            |
| ----------------------------------- | ------------------------------------------ |
| Simple click/change in sync tests   | Sequential interactions (type, tab, click) |
| When `await` is not needed          | When testing focus/blur behavior           |
| Quick assertions on simple handlers | When testing realistic user flows          |

---

## 8. Utility Function Testing Pattern

No mocking needed — pure input → output:

```typescript
import { describe, it, expect } from "vitest";
import { myUtil } from "../myUtil";

describe("myUtil", () => {
  it("returns expected output for valid input", () => {
    expect(myUtil("input")).toBe("expected");
  });

  it("handles edge case", () => {
    expect(myUtil("")).toBe("");
    expect(myUtil(null)).toBeNull();
  });
});
```

---

## 9. Commands Reference

| Command                                               | What it does                                                           |
| ----------------------------------------------------- | ---------------------------------------------------------------------- |
| `npm test`                                            | Starts the default project in interactive watch mode                   |
| `npm run test:all`                                    | Starts all Vitest projects (default + storybook); needs TTY + Playwright |
| `npm run test:watch`                                  | Watch mode for the default project                                     |
| `npm run test:coverage`                               | V8 coverage report for the default project                             |
| `npm run test:ui`                                     | Vitest UI for the default project                                      |
| `npx vitest run --project=default`                    | Default project only, CI mode, no watch                                |
| `npx vitest run src/path/File.test.tsx`               | Single test file                                                       |
| `npx vitest run --project=default --reporter=verbose` | Verbose output for the default project                                 |

**CI** (`.github/workflows/vitest.yml`) runs `npx vitest run --project=default` → JUnit XML at `cicd/reports/junit.xml`.

---

## 10. DO NOT

- **Never** mock `localStorage`, `matchMedia`, `IntersectionObserver`, `ResizeObserver`, or `window.scrollTo` — already mocked in global setup.
- **Never** call `cleanup()` manually — already done in global setup.
- **Never** initialize `i18n` in a test file — already initialized in global setup.
- **Never** use `any` types — use proper types or `unknown`.
- **Never** use `React.FC` — use named function declarations with typed props inline (e.g. `function Foo({ bar }: { bar: string }) {`).
- **Never** create test files outside of `src/`.
- **Never** mock RTK Query feature hooks directly in screen test files — use the request-boundary fixture pattern instead.
- **Prefer** `.test.ts` / `.test.tsx` over `.spec.ts` / `.spec.tsx` — all existing tests use `.test`.
- **Never** mix `fireEvent` and `userEvent` in the same test case without good reason.

## 11. DO

- **Always** import `describe`, `it`, `expect`, `vi` explicitly from `"vitest"`.
- **Always** place `vi.mock()` calls before the component import.
- **Always** call `vi.clearAllMocks()` in `beforeEach` when tests share mock functions.
- **Always** use `async/await` with `userEvent` and `waitFor`.
- **Always** use `screen` queries instead of destructuring from `render()` (except `container`).
- **Always** test from the user's perspective — query by role, label, or text.
- **Always** provide a descriptive `it()` string starting with a verb.
- **Always** group tests in nested `describe` blocks by category.
- **Always** co-locate test files with the source they test.

---

## Reference Files

| File                           | Purpose                                                       |
| ------------------------------ | ------------------------------------------------------------- |
| `vite.config.ts`               | Vitest project configuration (default + storybook projects)   |
| `src/test/vitest.globals.ts`   | Global mocks (localStorage)                                   |
| `src/test/vitest.setup.ts`     | Test environment setup (i18n, matchMedia, cleanup, observers) |
| `src/test/contract/screenTestUtils.tsx` | Shared store/router + fixture API helpers for screen tests    |
| `.github/workflows/vitest.yml` | CI workflow — `npx vitest run --project=default`              |
| `cicd/reports/junit.xml`       | JUnit XML output for CI reporting                             |
| `package.json`                 | Scripts: `test`, `test:watch`, `test:coverage`, `test:ui`     |
