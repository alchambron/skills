---
name: screen-test-fixtures
description: Rules for fixture-backed screen tests in src/screens/**. Use when creating, modifying, or reviewing any screen-level Vitest file that renders a real screen through screenTestUtils. Enforces explicit recorded fixtures, generated schemas, request-boundary mocking, and meaningful assertions that prove the screen consumes backend data correctly.
---

# Screen Test Fixtures

> **Prerequisite**: Read `vitest` first for shared test conventions. Use this skill for `src/screens/**` tests that should exercise the real RTK Query/request path.

## Core Rule

A real screen test **must** be fixture-backed at the HTTP boundary.

That means the test:

- renders the screen through `src/test/contract/screenTestUtils.tsx`
- provides explicit mock routes for every expected request
- uses committed fixtures from `src/api/__fixtures__/`
- validates fixtures with generated schemas from `src/api/__generated__/` when the schema matches the backend contract
- asserts on rendered data that proves the screen mapped the payload correctly

If a test bypasses that path, it is not a screen test. It is a component test and should be treated as such.

## Required Rules

### 1. Mock at the request boundary

Use `installFixtureApiMock()` and `renderScreen()`.

- Do not mock RTK Query hooks directly in screen test files.
- Do not mock the feature API module to inject fake `data`.
- Do not replace the screen's data path with hand-built props or hook return values.

## 2. Fixtures are mandatory

Every real screen test must use recorded fixtures.

- Prefer existing JSON fixtures in `src/api/__fixtures__/`.
- If the required endpoint is missing, add or update the recorder entry in `src/test/contract/recordFixtures.ts`.
- Re-record fixtures only when the backend payload truly changed.

## 3. Every expected endpoint must be explicit

Declare every request the screen is expected to make.

- Add a `routes` entry for each query/mutation used during the test.
- It is acceptable to return `[]`, `{}`, or `null` for endpoints that are intentionally irrelevant to the assertion, but they must still be declared explicitly.
- If a response varies by params, use a route handler function and branch on the params deliberately.

Do not rely on unhandled-request fallbacks.

- Do not use `allowUnhandledRequests` in real screen tests.
- Do not let unmatched requests silently resolve to placeholder payloads.
- If a screen starts calling a new endpoint, the test should fail until that endpoint is declared.

## 4. Validate the contract first

Before rendering assertions, validate the fixture shape.

- Use the generated schema when available and correct.
- Use strict fixture/schema checks for the endpoint under test, not loose `objectContaining` checks by default.
- If the generated schema and recorded fixture disagree, treat that as real contract drift.

When schema drift exists:

- do not hide it with permissive matchers
- either fix the fixture/schema generation path, or document the drift and use explicit shape assertions temporarily

## 5. Assertions must prove data wiring

A screen test should prove that backend data reached the UI correctly.

Good assertions:

- a recorded row value appears in the expected table or dialog
- a chart control or label reflects data derived from the fixture
- a search result from the fixture appears after the user action that triggers loading

Weak assertions to avoid as the primary proof:

- `container.querySelector("div")`
- `findByRole("table")` with no data assertion
- `svg` existence only
- generic shell render checks with no link to the fixture payload

Shell assertions are acceptable only as secondary checks.

## 6. Do not fake screen context

Avoid replacing real screen wiring with synthetic context when the goal is a screen test.

- Do not mock `useOutletContext` with empty arrays or hand-built screen state.
- Do not turn a screen test into an empty-state smoke test by bypassing its loaders.

If the intent is to test a pure presentational empty state, move that coverage to a lower-level component test.

## 7. Prefer realistic user flow

If the screen requires interaction before data appears:

- render the screen
- perform the real user action
- assert on the resulting fixture-backed UI

Use `userEvent` for realistic interactions when the UI depends on typing, clicking, or search triggers.

## 8. Keep the test intention clear

A strong screen test usually has three phases:

1. fixture contract checks
2. render + interaction through the real screen path
3. assertions tied to recorded data

Keep the test narrow. Cover one meaningful flow per test instead of asserting every visible element.

## Review Checklist

Use this checklist when reviewing a screen test:

- Does it render through `renderScreen()`?
- Does it install explicit routes for every expected endpoint?
- Does it avoid `allowUnhandledRequests`?
- Does it reuse committed fixtures instead of inline payloads?
- Does it validate the fixture against the generated schema, or clearly document real schema drift?
- Does it avoid mocking RTK Query hooks, `useOutletContext`, or other screen data wiring?
- Do the assertions prove the screen consumed fixture data correctly rather than only rendering a shell?

If any answer is no, the test is probably not providing real screen coverage.

## Minimal Pattern

```ts
const apiMock = installFixtureApiMock({
  routes: [
    {
      method: "get",
      path: API_ROUTES.GET_SOMETHING,
      response: someFixture,
    },
    {
      method: "post",
      path: API_ROUTES.SYNC_SOMETHING,
      response: {},
    },
  ],
});

expectFixtureMatchesSchema("someFixture", someFixture, SomeSchema);

renderScreen(<MyScreen />, { route: "/screen/123" });

expect(await screen.findByText(someFixture[0].name)).toBeInTheDocument();

apiMock.cleanup();
```

## When Not To Use This Skill

Do not use this skill for:

- isolated shared-component tests
- tests that intentionally mock hooks at component level
- Storybook `*.stories.test.tsx`

Those cases should use the existing `vitest`, `vitest-component`, or `vitest-stories` skills instead.
