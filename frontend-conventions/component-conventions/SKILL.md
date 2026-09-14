---
name: component-conventions
description: Rules for defining React component functions. Use when creating or modifying any .tsx component file. Covers the correct function signature to avoid react/prop-types eslint errors, prop typing patterns, and export conventions.
---

# React Component Conventions

> **Related skills**: `react-compiler` (memoization and `"use no memo"` rules)

## TL;DR

| Pattern | Rule | Why |
|---------|------|-----|
| `React.FC<Props>` | **Do NOT use** | Triggers `react/prop-types` eslint errors |
| `({ ...}: Props) => {}` | **Use this** | Props typed on the parameter, no eslint issue |
| `/* eslint-disable react/prop-types */` | **Do NOT use** | Not a real fix, hides the problem |

---

## 1. Component Function Signature

**Never** use `React.FC` or `React.FunctionComponent`. Type props directly on the destructured parameter.

```tsx
// WRONG - causes "X is missing in props validation" eslint errors
export const MyComponent: React.FC<MyComponentProps> = ({ title, count }) => {
  return <div>{title}: {count}</div>;
};

// WRONG - suppressing the error instead of fixing it
/* eslint-disable react/prop-types */
export const MyComponent: React.FC<MyComponentProps> = ({ title, count }) => {
  return <div>{title}: {count}</div>;
};

// CORRECT - type on the destructured parameter
export const MyComponent = ({ title, count }: MyComponentProps) => {
  return <div>{title}: {count}</div>;
};
```

---

## 2. Props with Default Values

When props have defaults, use the same pattern with default values in destructuring:

```tsx
// CORRECT
export const MyComponent = ({
  title,
  count,
  variant = "primary",
  isVisible = true,
}: MyComponentProps) => {
  return <div>{title}</div>;
};
```

---

## 3. Props Interface Location

Props interfaces must be defined in the feature's `types.ts` file or in a co-located `types.ts` for shared components:

```
src/screens/Feature/types.ts          # screen component props
src/shared/components/Widget/types.ts # shared component props
```

---

## 4. Component Export

Use named exports, not default exports, for components:

```tsx
// CORRECT
export const MyComponent = ({ title }: MyComponentProps) => {
  // ...
};
```
