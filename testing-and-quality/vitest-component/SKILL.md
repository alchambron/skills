---
name: vitest-component
description: Mocking patterns and testing strategies for React UI components. Use when writing or reviewing tests for components in src/shared/components/ that need vi.mock() for react-i18next, RTK Query hooks, toastBridge, Tooltip, custom hooks, or third-party libraries (vi.hoisted). Also covers component rendering patterns, rerender for prop changes, the fixture-based screen-level testing pattern, and the preferred TDD workflow for screen tests. Use alongside the vitest core skill.
---

# Vitest — Component Testing

> **Prerequisite**: Read the `vitest` core skill first for imports, test structure, assertions, and commands.

---

## 1. When to Mock `react-i18next`

The global setup (`src/test/vitest.setup.ts`) already initializes `i18n` with all 3 locales. You do NOT always need to mock `react-i18next`.

| Scenario                                                                                               | Approach                                                             | Example                                                |
| ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- | ------------------------------------------------------ |
| Tests **assert on specific displayed text** and you want assertions decoupled from locale file changes | **Mock** with a hardcoded translations map                           | `Calculator.test.tsx`                                  |
| Tests focus on **UI behavior** (clicks, focus, state) and don't assert on exact text                   | **Don't mock** — let the global i18n setup provide real translations | `DatePicker.test.tsx`, `FloatingActionButton.test.tsx` |

**Rule of thumb**: If your `expect()` calls check for specific user-visible strings (e.g., `getByText("Generate")`), mock `react-i18next` so the test won't break when someone updates a translation. If you query by role, placeholder, or test behavior without checking exact text, skip the mock.

### react-i18next mock template

```typescript
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => {
      const translations: Record<string, string> = {
        "feature.label.name": "Name",
        "feature.button.save": "Save",
      };
      return translations[key] || key;
    },
    i18n: { language: "en" },
  }),
}));
```

---

## 2. Mocking RTK Query Hooks

Use this pattern for isolated shared-component tests only. For screen-level tests, skip direct hook mocks and use the fixture-backed request-boundary pattern in Section 10.

```typescript
vi.mock("../../../api/featureApi", () => ({
  useGetItemsQuery: () => ({ data: mockData }),
  useLazyCheckQuery: () => [vi.fn(), { isLoading: false }],
  useSaveMutation: () => [vi.fn()],
}));
```

---

## 3. Mocking Shared Components (isolation)

When a component depends on complex shared components (e.g., Tooltip), mock them to simplify rendering:

```typescript
vi.mock("../Tooltip/Tooltip", () => ({
  Tooltip: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  TooltipContent: ({ children }: { children: React.ReactNode }) => (
    <div>{children}</div>
  ),
  TooltipProvider: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
  TooltipTrigger: ({ children }: { children: React.ReactNode }) => (
    <>{children}</>
  ),
}));
```

---

## 4. Mocking Utility Modules

```typescript
vi.mock("../../../util/toastBridge", () => ({
  toast: {
    show: vi.fn(),
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
    destructive: vi.fn(),
    default: vi.fn(),
  },
}));
```

---

## 5. Mocking Third-Party Libraries with `vi.hoisted()`

Use `vi.hoisted()` when mocks need **shared mutable state** between `vi.mock()` factory functions and test assertions:

```typescript
const h = vi.hoisted(() => {
  const saveAsMock = vi.fn();
  const writeMock = vi.fn(() => new Uint8Array([1, 2, 3]));
  return { saveAsMock, writeMock };
});

vi.mock("file-saver", () => ({
  saveAs: (blob: Blob, filename: string) => h.saveAsMock(blob, filename),
}));

vi.mock("xlsx", () => ({
  utils: {
    aoa_to_sheet: vi.fn(),
    book_new: vi.fn(),
    book_append_sheet: vi.fn(),
  },
  write: h.writeMock,
}));

// In tests:
expect(h.saveAsMock).toHaveBeenCalledWith(expect.any(Object), "export.csv");
```

---

## 6. Mocking Custom Hooks

When a component delegates logic to a custom hook, mock the hook to control its return value:

```typescript
vi.mock("./useMyHook", () => {
  // Use require for React hooks inside mock factories
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { useState: useStateMock } = require("react");
  return {
    useMyHook: (initialValue: string) => {
      const [value, setValue] = useStateMock(initialValue);
      return { value, setValue, reset: vi.fn() };
    },
  };
});
```

---

## 7. Mock Cleanup

Always clear mocks in `beforeEach` when tests share mock functions:

```typescript
beforeEach(() => {
  vi.clearAllMocks();
});
```

For tests using `vi.stubGlobal()`, also unstub:

```typescript
beforeEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal("Blob", CustomBlobMock);
});
```

---

## 8. Component Testing Pattern

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

// Mocks before component import
vi.mock("../../../api/featureApi", () => ({ ... }));

import { MyComponent } from "./MyComponent";

describe("MyComponent", () => {
  const mockOnChange = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("Rendering", () => {
    it("renders with required props", () => {
      render(<MyComponent value="test" onChange={mockOnChange} />);
      expect(screen.getByRole("textbox")).toBeInTheDocument();
    });
  });

  describe("User Interactions", () => {
    it("calls onChange on user input", async () => {
      render(<MyComponent onChange={mockOnChange} />);

      const input = screen.getByRole("textbox");
      await userEvent.type(input, "hello");

      expect(mockOnChange).toHaveBeenCalled();
    });
  });
});
```

---

## 9. Testing Prop Changes with `rerender`

```typescript
it("resets value when dependsOn changes", () => {
  const handleChange = vi.fn();
  const { rerender } = render(
    <Component dependsOn={1} value={2} onChange={handleChange} />,
  );

  handleChange.mockClear();

  rerender(
    <Component dependsOn={2} value={2} onChange={handleChange} />,
  );

  expect(handleChange).toHaveBeenCalledWith(0);
});
```

---

## 10. Screen-Level Testing (Fixture-Based)

Screen tests use **recorded JSON fixtures** as mock data and **auto-generated Zod schemas** for contract validation.

### Architecture

```text
src/api/__fixtures__/           ← JSON fixtures recorded from real backend
src/api/__generated__/          ← Zod schemas auto-generated from fixtures
src/test/contract/              ← inferSchema.ts, recordFixtures.ts, createTestStore.ts, screenTestUtils.tsx
src/screens/<Screen>/__tests__/ ← Screen test files
```

### Recording fixtures

```bash
RECORD_USERNAME=admin RECORD_PASSWORD=secret npm run record-fixtures
```

This hits real backend endpoints, saves JSON fixtures, and auto-generates strict Zod schemas. Add or update recordable endpoints in `src/test/contract/recordFixtures.ts` via the frontend-owned `ENDPOINT_DEFINITIONS` allowlist. Backend OpenAPI `x-fixture` metadata validates and enriches those entries when available.

### Screen test structure

Every screen test file has **two** describe blocks:

1. **API Contract** — validates the fixture still matches the generated Zod schema (catches backend drift)
2. **Screen Rendering** — installs fixture-backed API intercepts, renders the screen through the shared test store, asserts data is displayed

### Template

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { screen } from "@testing-library/react";

import { API_ROUTES } from "@/api/apiRoutes";
import { ItemSchema } from "@/api/__generated__/<endpoint>.schema";
import fixtureData from "@/api/__fixtures__/<endpoint>.json";
import {
  installFixtureApiMock,
  renderScreen as renderScreenWithProviders,
} from "@/test/contract/screenTestUtils";

// --- Contract tests ---
describe("<Screen> API Contract", () => {
  it("fixture matches the expected schema (strict)", () => {
    for (const item of fixtureData) {
      const result = ItemSchema.safeParse(item);
      if (!result.success) {
        throw new Error(
          `Fixture item id=${(item as Record<string, unknown>).id} does not match schema:\n` +
            JSON.stringify(result.error.format(), null, 2),
        );
      }
      expect(result.success).toBe(true);
    }
  });

  it("fixture has at least one record", () => {
    expect(fixtureData.length).toBeGreaterThan(0);
  });
});

// --- Shared mocks/helpers (before component import) ---
const mockNavigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => mockNavigate };
});

let restoreApiMock = () => {};

import ScreenComponent from "../ScreenComponent";

function renderScreen() {
  return renderScreenWithProviders(<ScreenComponent />);
}

// --- Screen tests ---
describe("<Screen> Screen", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    restoreApiMock = installFixtureApiMock({
      routes: [
        {
          method: "get",
          url: API_ROUTES.<ENDPOINT>,
          response: fixtureData,
        },
      ],
    });
  });

  afterEach(() => {
    restoreApiMock();
  });

  it("renders the data table", async () => {
    renderScreen();
    expect(await screen.findByRole("table")).toBeInTheDocument();
  });

  it("displays data from fixture", async () => {
    renderScreen();
    await screen.findByRole("table");
    for (const item of fixtureData) {
      expect(screen.getByText(item.uniqueField)).toBeInTheDocument();
    }
  });
});
```

### TDD workflow

For screen work, TDD means **Test-Driven Development**, not just “run tests while coding.” Prefer this red-green-refactor loop:

1. **Red**: add or tighten a failing contract test and one visible rendering assertion before changing the screen.
2. Install fixture-backed API intercepts with `installFixtureApiMock`.
3. Run the single screen file headlessly and confirm the failure is the one you intended.
4. **Green**: implement the smallest UI change needed to satisfy the test.
5. **Refactor**: simplify the screen code and test without changing behavior.
6. Re-record fixtures only when backend data or schema changed, not for ordinary UI-only refactors.

### Key rules

- **Mock the request boundary, not the RTK Query hook**: Keep the real endpoint definition, base query, auth header injection, and response unwrapping in play.
- **Use `installFixtureApiMock`**: It auto-wraps fixture responses in the backend `{ data: ... }` envelope and validates the auth header by default.
- **Treat fixtures as committed test input**: Local `pre-push` refreshes them; CI executes the headless Vitest suite on pull request updates and pushes to `work`.
- **Short values**: Use `getAllByText(...).length > 0` instead of `getByText(...)` for values like numbers that may appear in multiple DOM elements (e.g., pagination).
- **Unique values**: Use `getByText(...)` for values guaranteed to be unique (e.g., codes, names).
- **Re-record**: When backend changes, re-run `npm run record-fixtures` to update both fixtures and schemas. Tests will fail if schema drifts.

### Reference implementation

See `src/screens/Building/__tests__/BuildingList.test.tsx` as the canonical example.

---

## 11. Component-Specific Rules

### DO NOT

- **Never** mock `localStorage`, `matchMedia`, `IntersectionObserver`, `ResizeObserver`, or `window.scrollTo` — already in global setup.
- **Never** call `cleanup()` manually — already in global setup.
- **Never** initialize `i18n` in a test file — already in global setup. Only mock `react-i18next` per Section 1 above.

### DO

- **Always** place `vi.mock()` calls before the component import.
- **Always** call `vi.clearAllMocks()` in `beforeEach` when tests share mock functions.
- **Always** use `vi.hoisted()` when mock state is shared between `vi.mock()` factories and test assertions.
- **Always** mock only what's necessary — if a dependency works fine without mocking, don't mock it.
