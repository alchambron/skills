---
name: verification
description: Use after making code changes to verify correctness. Runs type checking, linting, and detects common violations (any types, console statements, missing stories).
---

# Verification Skill

## Quick Checks

Run these after making code changes:

```bash
# Type check
npm run typecheck

# Lint
npm run lint
```

## Find Common Violations

```bash
# Find `any` types
rg -n '\bany\b' src/ --glob '*.{ts,tsx}'

# Find console.log statements
rg -n "console\." src/ --glob '*.{ts,tsx}'

# Find components missing stories
rg -l "export.*function" src/shared/components -g '*.tsx' | while read f; do [ ! -f "${f%.tsx}.stories.tsx" ] && echo "Missing story: $f"; done
```

## When to Run

- **After editing `.ts` / `.tsx` files** — run `npm run typecheck` + `npm run lint`
- **After modifying `shared/components/`** — check for missing stories
- **Before committing** — run all checks
