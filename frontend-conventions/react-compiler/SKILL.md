---
name: react-compiler
description: React Compiler conventions for this codebase. Use when creating or modifying React components, hooks, modal configs, charts, tables, or forms. Explains when to avoid useMemo/useCallback/memo, when to add the "use no memo" directive for react-hook-form, and the limited exceptions for TanStack Table and Recharts.
---

# React Compiler Conventions

## Core Rule

This project uses the React Compiler.

Default to plain React code:

- Do **not** add `useMemo`
- Do **not** add `useCallback`
- Do **not** wrap components in `memo`

Assume the compiler handles normal memoization for standard React code.

## Allowed Exceptions

Manual memoization is only allowed when a library is known to conflict with compiler assumptions or when the codebase already documents the reason.

### 1. `react-hook-form`

`react-hook-form` uses interior mutability and is **not** compiler-safe by default in this repo.

If a function component or hook calls any React Hook Form hook, add:

```tsx
"use no memo";
```

Place it as the **first statement inside the function body**.

Applies to calls such as:

- `useForm`
- `useFormContext`
- `useWatch`
- `useController`
- `useFieldArray`
- `useFormState`

This rule is enforced by:

- [src/test/useNoMemoDirective.test.ts](../../../src/test/useNoMemoDirective.test.ts)

Do not “fix” React Hook Form code by adding `useMemo` or `useCallback`. Use the directive instead.

### 2. TanStack Table

TanStack Table is the main exception where manual memoization may still be needed.

Use `useMemo` or `memo` only when:

- the table integration already depends on stable references, or
- the file already documents that the compiler does not optimize that path

When you keep or add manual memoization for TanStack Table, leave a short code comment explaining why.

### 3. Recharts

Recharts is another allowed exception when chart rendering or derived chart config/data needs stable references for correctness or performance.

Do not add memoization automatically. Only use it when:

- the chart code is already structured around stable references, or
- you verified that removing memoization would break behavior or cause meaningful regressions

Add a short comment if you keep or introduce manual memoization for a Recharts-specific reason.

## Patterns

### Standard React code

```tsx
export function Example({ value }: ExampleProps) {
  const doubled = value * 2;

  const handleClick = () => {
    console.log(doubled);
  };

  return <button onClick={handleClick}>{doubled}</button>;
}
```

### React Hook Form component or hook

```tsx
export function UserModal() {
  "use no memo";

  const methods = useForm<FormValues>();

  return <FormProvider {...methods}>{/* ... */}</FormProvider>;
}
```

### TanStack/Recharts exception

```tsx
const columns = useMemo(() => buildColumns(t), [t]);
```

Only do this when the table/chart integration needs it. Add a short comment nearby.

## Do Not

- Do not add `useMemo`, `useCallback`, or `memo` as a default optimization
- Do not copy older patterns that predate the React Compiler
- Do not use `useMemo` as a workaround for React Hook Form
- Do not omit `"use no memo"` in files calling React Hook Form hooks

## Checklist

- [ ] No new `useMemo`, `useCallback`, or `memo` without a library-specific reason
- [ ] Every file calling React Hook Form hooks includes `"use no memo";`
- [ ] TanStack Table memoization has a short reason comment
- [ ] Recharts memoization has a short reason comment
