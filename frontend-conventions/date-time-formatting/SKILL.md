---
name: date-time-formatting
description: Enforce preference-aware date/time handling in the frontend. Use when creating or modifying UI that displays or edits dates/times so user Preferences (dateFormat/timeFormat) are respected.
---

# Date/Time Formatting Rules

## Canonical Utilities

Always use these utilities:

- `src/util/dateTime/dateTimePreferences.ts`
  - `formatDateByPreferences(value, options?)`
  - `formatDateTimeByPreferences(value, options?)`
  - `formatTimeByPreferences(value, options?)`
  - `syncDateTimePreferencesFromSettings(settings)`
- `src/hooks/useDateTimePreferences.ts`

## Required Rules

1. Do not use `toLocaleDateString()` for user-facing dates.
2. Do not use `toLocaleString()` for user-facing date+time values.
3. Do not hardcode DatePicker formats like `"MM/DD/YYYY"` in app code.
4. Let `DatePicker` use preference defaults unless a strict external contract requires a specific format.
5. Keep backend payload serialization formats (for example `"yyyy-MM-dd"`) only when the API contract requires it.

## Usage Patterns

- Date only:
  - `formatDateByPreferences(value, { fallback: "-" })`
- Date + time:
  - `formatDateTimeByPreferences(value, { fallback: "-" })`
- Time only:
  - `formatTimeByPreferences(value, { fallback: "-" })`

## Sync Requirement

When settings are loaded/refreshed from backend parameters, call:

- `syncDateTimePreferencesFromSettings(parametersData?.settings)`

This keeps in-memory and persisted preferences aligned.

## Review Checklist

Run these checks after edits:

```bash
rg -n "toLocaleDateString\\(|toLocaleString\\(" src -g '!**/*.test.*' -g '!**/*.stories.*'
rg -n "format=\\\"(MM/DD/YYYY|YYYY/MM/DD|YYYY-MM-DD)\\\"" src -g '!**/*.test.*' -g '!**/*.stories.*'
```
