---
name: dropdown-utils
description: "Use when creating or modifying dropdown option mappings. Enforces shared helpers for id/code option conversion and lookup: mapToDropdownOptions, getCodeById, and getIdByCode."
---

# Dropdown Utils Skill

## Core Rule

Use shared utilities for dropdown mapping and lookup instead of rewriting local helper functions.

## Use These Utilities

1. `mapToDropdownOptions` from `src/util/IdCodeNameHelper.ts`
2. `getCodeById` from `src/util/dropdownOptionUtil.ts`
3. `getIdByCode` from `src/util/dropdownOptionUtil.ts`

## Workflow

1. Search for manual dropdown mappings first:

```bash
rg "\.map\(\(option\) => \(\{" src/
rg "getCodeById|getIdByCode" src/
```

2. Replace direct id/code option mapping with `mapToDropdownOptions` when data is a plain array with `id` and `code`.

3. Replace local lookup helpers with `getCodeById` and `getIdByCode`.

4. Keep custom mapping only when needed:
- translated labels (`name: t("...")`)
- extra fields like `value`, `parentId`, or feature-specific transformations

5. Run verification:

```bash
npx tsc --noEmit
npx eslint <touched-files>
```

## Examples

### Plain id/code mapping

```ts
const options = mapToDropdownOptions(data, {
  id: "id",
  code: "code",
});
```

### Code lookup from id

```ts
const code = getCodeById(options, selectedId, fallbackCode);
```

### Id lookup from code

```ts
const id = getIdByCode(options, selectedCode, fallbackId);
```
