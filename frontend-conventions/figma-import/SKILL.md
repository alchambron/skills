---
name: figma-import
description: "Use when importing or syncing a Figma design into this repo — extracting screens, components, or styles from a figma.com URL. Enforces design-token usage: every color, spacing, radius, and font value referenced from Figma must resolve to a named CSS variable in `src/index.css` (`@theme` block) and be consumed via Tailwind utility classes or `var(--color-…)`. Blocks raw hex/px/rem leaking into JSX. Triggers on phrases like \"import from Figma\", \"use this Figma link\", \"match the Figma design\", or any figma.com URL in the request."
---

# Figma → Code Import (Design-Token-Enforced)

This skill governs every Figma import in `frontend/`. The Figma file is the **source of truth** for tokens; code mirrors token names exactly (`Cyan/100` → `--color-cyan-100`).

## Pre-flight — always run, in this order

1. **Resolve URL**. From `figma.com/design/:fileKey/:fileName?node-id=A-B`:
   - `fileKey` = `:fileKey`
   - `nodeId` = `A:B` (replace `-` with `:`)
   - Branch URL → use `branchKey` as `fileKey`.

2. **Pull variables before pulling code.** Always call:
   - `mcp__plugin_figma_figma__get_variable_defs(nodeId, fileKey)` — returns the named palette (`{"Cyan/100":"#d3f3ff", …}`).
   - `mcp__plugin_figma_figma__get_design_context(nodeId, fileKey, clientFrameworks="react", clientLanguages="typescript,css")` — returns reference JSX + asset URLs.
   - Optional: `get_metadata` first if the node ID has children worth picking; `get_screenshot` if a visual check is needed.

3. **If both calls fail with "nothing selected"**, ask the user to select the layer in Figma desktop. The MCP server reads the desktop app's current selection for write-touching calls — file-key alone is not enough for some nodes. Screenshot calls work without selection.

## Token mapping rule (non-negotiable)

| Figma name | CSS var | Tailwind utility |
|---|---|---|
| `Cyan/100` | `--color-cyan-100` | `bg-cyan-100`, `text-cyan-100`, … |
| `Primary/500` | `--color-primary-500` | `bg-primary-500`, … |
| `Grayscale/900` | `--color-grayscale-900` | `text-grayscale-900`, … |
| `WebSynco/500` | `--color-websynco-500` | `bg-websynco-500`, … |

- Lowercase the family; replace `/` with `-`. Keep numeric/`regular` suffix verbatim.
- Tokens live in `src/index.css` under the `@theme { … }` block. Tailwind v4 auto-generates utilities for any `--color-*` token declared there.
- **If a Figma name maps to a hex that already exists under a different name in code, rename the existing token to the Figma name.** Single source of truth is the designer's vocabulary.

## Workflow

For every imported node:

1. **Diff palette.** Compare `get_variable_defs` output against `src/index.css` `@theme` block.
   - For each Figma name not yet in `@theme`: add it.
   - For each hex already used under a Tailwind/arbitrary value but not yet named: rename it to the Figma token.

2. **Adapt the generated JSX.** Figma MCP returns Tailwind v4 JSX with arbitrary values like `bg-[var(--cyan\/100,#d3f3ff)]`. Convert these:
   - `bg-[var(--cyan\/100,#d3f3ff)]` → `bg-cyan-100`
   - `text-[color:var(--cyan\/500,#04b6d4)]` → `text-cyan-500`
   - `border-[var(--primary\/500,#2050a0)]` → `border-primary-500`
   - Gradients keep `var(...)` form: `bg-[linear-gradient(...,var(--color-grayscale-900)_22%,...)]`
   - Reject: `bg-[#d3f3ff]`, `text-[#04b6d4]`, raw hex inline styles. Always use the utility.

3. **Strip Figma-only noise.** The MCP output contains:
   - `data-node-id="…"` — drop.
   - `data-name="…"` — drop.
   - `font-['Nimbus_Sans:Bold',sans-serif]` — replace with project font stack (`Open Sans`).
   - Hard-coded widths/heights from the design canvas (`w-[1219px]`) — drop unless the layout truly is fixed-width.
   - Absolute-positioned `Overlay+Shadow` divs that duplicate `box-shadow` — collapse to a `shadow-*` utility.

4. **Map to existing primitives.** Before writing new JSX, search `src/shared/components/` for an existing component (Button, DropdownRadix, InputBox, DataTable, etc.). Wire the Figma layout to those — do not re-implement.

5. **Verify zero leakage.** After edits, run:
   ```bash
   rg -niI --no-heading -g 'src/**' -g '!**/*.test.*' -g '!**/*.stories.*' -g '!src/index.css' \
     -e '\[#(eaf9ff|d3f3ff|92e6ff|04b6d4|06b6d4|002830|e9edfd|2050a0|eaeaf3|abaed0|e8e8e8|3b3b3b|262626|111111|fadca4|79ec34|50a020|f9c43b)\]'
   ```
   Empty output = pass. Extend the regex when new palette entries are added.

6. **Type-check.** `npx tsc --noEmit` from `frontend/`. Compare against `git stash` baseline if errors appear unrelated.

7. **Storybook.** For any change to `src/shared/components/`, update the `.stories.tsx` (project rule from `AGENT.md`).

## Asset handling

`get_design_context` returns short-lived asset URLs (`https://www.figma.com/api/mcp/asset/...`) that expire in 7 days. Download with `curl` and commit to `frontend/public/` (or the asset dir the component expects). Never inline-embed an MCP URL — it will 404.

## Known palette (as of last sync)

Source of truth: `src/index.css` `@theme` block. Current tokens:

```
--color-cyan-50  / --color-cyan-100 / --color-cyan-200 / --color-cyan-500 / --color-cyan-regular / --color-cyan-900
--color-primary-50 / --color-primary-500
--color-neutral-50 / --color-neutral-200
--color-grayscale-50 / --color-grayscale-500 / --color-grayscale-700 / --color-grayscale-900
--color-warning-200
--color-success-200 / --color-success-500
--color-websynco-500
```

If a Figma call returns a name not in this list, add it to `@theme` in the same commit as the JSX that uses it. Never use the hex inline as a "stopgap."

## When the user pastes a Figma URL with no instruction

Default behaviour:
1. Run `get_variable_defs` + `get_design_context`.
2. Tell the user what node you found (name + type).
3. Ask whether to (a) generate a new component, (b) restyle an existing one, or (c) only sync tokens.

## Anti-patterns (reject and fix)

- `className="bg-[#d3f3ff]"` — must be `bg-cyan-100`.
- `style={{ color: "#04b6d4" }}` — must be `className="text-cyan-500"` or `style={{ color: "var(--color-cyan-500)" }}` if a utility is impossible.
- New token declared at the call-site (`bg-[color:var(--my-blue,#abc)]`) — declare in `@theme`, use the utility.
- Importing a Figma node without first reading its variables — every Figma value must trace back to a named token.
- Inlining MCP asset URLs — they expire.

## Pointer

When the user says "import this from Figma," this skill's checklist applies before any code is written. The `figma-use` / `figma-generate-design` MCP skills cover the *plugin API* side (write back to Figma); this skill covers the *read-into-this-repo* side.
