---
name: column-definition
description: Rules for defining DataTable columns in this project. Use when creating or modifying table columns in .tsx files. Covers when to inline translation labels, when renderElement is necessary, date formatting with preferences, and avoiding unnecessary column helper abstractions.
---

# Column Definition Conventions

## TL;DR

| Pattern | Rule | Why |
|---------|------|-----|
| `columns` array | Define explicit `TableColumn<RowType>[]` inline in the screen/component | Clear and local behavior |
| Translation labels | Call `t("...")` directly in `label` unless reused multiple times | Less boilerplate |
| `renderElement` | Use only when needed for custom UI, formatting, or interactions | Avoid unnecessary complexity |
| Date columns | Always format with `formatDateByPreferences(..., { fallback: "-" })` | Consistent with user preferences |
| Column helpers (`createColumn`) | Avoid by default; only use when there is real repeated behavior | Prevent abstraction for no benefit |

## 1. Default Column Pattern

Use explicit column objects:

```tsx
const columns: TableColumn<MyRow>[] = [
  {
    dataKey: "code",
    label: t("my.table.column.code", { defaultValue: "Code" }),
  },
  {
    dataKey: "startDate",
    label: t("my.table.column.start.date", { defaultValue: "Start Date" }),
    renderElement: (_row, value) => (
      <span>{formatDateByPreferences(value, { fallback: "-" })}</span>
    ),
  },
];
```

## 2. When `renderElement` Is Required

Use `renderElement` only for:

1. Custom component rendering (buttons, links, icons, badges, etc.)
2. Value formatting that is not default-safe (date/time/currency)
3. Interactive behavior (open modal, trigger actions, show tooltip, etc.)

Do not add `renderElement` if a plain value display is enough.

## 3. Labels and i18n

Prefer inline labels:

```tsx
label: t("meter.dashboard.reference.table.column.targets", {
  defaultValue: "Targets",
})
```

Create a separate label constant only when the same translated label is reused in multiple places.

## 4. Date Rendering Rule

Any date shown to users must use preference-aware formatting:

```tsx
renderElement: (_row, value) => (
  <span>{formatDateByPreferences(value, { fallback: "-" })}</span>
)
```

Avoid raw ISO dates and avoid `toLocaleDateString` directly in table columns.

## 5. Action Columns

Use `isAction: true` for row actions when no custom dataKey cell is needed:

```tsx
{
  isAction: true,
  buttonConfigs: (row) => [
    { action: "delete", mutationTrigger: () => onDelete(row.id) },
  ],
}
```

Use a data column with `renderElement` only if the action must be embedded with value content in the same cell.

## 6. Practical Heuristic

Before adding helpers like `createColumn`, check:

1. Is there real duplicated logic (not just repeated `dataKey + label`)?
2. Will this helper make the file easier to read today?
3. Will the helper reduce future bug risk?

If the answer is mostly no, keep explicit column objects.
