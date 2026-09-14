---
name: translation
description: MUST be loaded proactively whenever creating or modifying screens, components, modals, or any UI that contains user-visible strings. Also load when editing en.json, es.json, or fr.json locale files, adding i18n translation keys, or using the t() function. Ensures all user-visible strings are translated and keys are correctly added to all three locale files (en.json, es.json, fr.json) at the END of each file.
---

# Translation Skill

## Core Principle

**Every string visible to the user MUST be translated.** Never hardcode user-facing text in components.

---

## Workflow

### Step 1: Identify all user-visible strings

Before writing or modifying a component, identify **every** string the user will see:

- Labels, titles, subtitles, descriptions
- Button text (including loading states like "Saving...")
- Placeholder text in inputs
- Table column headers
- Error messages and validation messages
- Toast/notification messages (success, error)
- Empty state messages
- Tooltip text
- Modal titles and descriptions
- Confirmation dialog text

**Strings that do NOT need translation:**

- Technical identifiers (API routes, CSS classes, HTML attributes)
- Console logs / debug strings
- Constants used only in code logic

### Step 2: Create translation keys

#### Key naming convention

Keys use **dot-separated** hierarchical naming:

```
{feature}.{context}.{element}.{type}
```

| Segment   | Description                  | Examples                            |
| --------- | ---------------------------- | ----------------------------------- |
| `feature` | Screen or feature name       | `customer`, `formula`, `meter`      |
| `context` | Sub-section or component     | `detail`, `list`, `modal`, `filter` |
| `element` | Specific UI element or field | `table.column`, `button`, `label`   |
| `type`    | The specific item            | `code`, `name`, `save`, `delete`    |

#### Key rule: always use dot-separated lowercase words

**Never use camelCase in keys.** Multi-word segments must be separated by dots.

```
point.list    ✅ CORRECT
pointList     ❌ WRONG

rate.structure ✅ CORRECT
rateStructure  ❌ WRONG
```

#### Examples

```
customer.list.table.column.code        → Table column header "Code"
customer.button.create                 → Button "Create Customer"
customer.detail.error.name             → Validation "Name is required"
formula.saved                          → Toast "Formula saved successfully"
formula.detail.title.create            → Page title "Create Formula Details"
formula.detail.subtitle.create         → Subtitle "Enter formula information..."
point.list.filter.label.code           → Filter label "Code"
point.list.filter.placeholder.code     → Filter placeholder "Enter code..."
rate.structure.detail.label.code       → Label "Code"
```

#### Special patterns

- **View/Create/Edit titles**: `{feature}.detail.title.view`, `{feature}.detail.title.create`, `{feature}.detail.title.edit`
- **Success messages**: `{feature}.success.save`, `{feature}.success.delete`
- **Validation errors**: `{feature}.validation.{field}.required`, `{feature}.detail.error.{field}`
- **Button loading states**: `{feature}.button.submit.loading` (e.g., "Generating...")
- **Layout titles with interpolation**: `{feature}.detail.layout.title` → `"Formula: {{formulaName}}"`

### Step 3: Add keys to ALL 3 locale files

**CRITICAL: New keys MUST be added at the END of each file, before the closing `}`.**

Files to update (always all three):

1. `src/locales/en.json` — English
2. `src/locales/fr.json` — French
3. `src/locales/es.json` — Spanish

#### Process

1. Open `en.json` — add the new key(s) **at the very end** (before the closing `}`)
2. Open `fr.json` — add the same key(s) **at the very end** with the French translation
3. Open `es.json` — add the same key(s) **at the very end** with the Spanish translation

#### Example

Adding a new "Delete Customer" button and its confirmation:

**en.json** (append before `}`):

```json
  "customer.button.delete": "Delete Customer",
  "customer.confirm.delete": "Are you sure you want to delete this customer?"
```

**fr.json** (append before `}`):

```json
  "customer.button.delete": "Supprimer le client",
  "customer.confirm.delete": "Êtes-vous sûr de vouloir supprimer ce client ?"
```

**es.json** (append before `}`):

```json
  "customer.button.delete": "Eliminar cliente",
  "customer.confirm.delete": "¿Está seguro de que desea eliminar este cliente?"
```

### Step 4: Use translations in components

```typescript
import { useTranslation } from "react-i18next";

export function MyComponent() {
  const { t } = useTranslation();

  return (
    <div>
      <h1>{t("feature.title")}</h1>
      <Button>{t("feature.button.save")}</Button>
      <Input placeholder={t("feature.placeholder.name")} />
    </div>
  );
}
```

#### Interpolation (dynamic values)

```typescript
// Key: "customer.detail.layout.title": "Customer: {{customerName}}"
t("customer.detail.layout.title", { customerName: customer.name });

// Key: "point.list.table.total.records": "Total Records: {{count}}"
t("point.list.table.total.records", { count: totalRecords });
```

---

## DO NOT

- **Never hardcode user-visible text**

  ```typescript
  // WRONG
  <Button>Save Customer</Button>

  // CORRECT
  <Button>{t("customer.button.save")}</Button>
  ```

- **Never add a key to only one or two locale files** — always add to all three (`en.json`, `fr.json`, `es.json`)

- **Never insert keys in the middle of a locale file** — always append at the end

- **Never duplicate an existing key** — search the locale files first to check if a key already exists

- **Never use generic keys for feature-specific text** — each feature should have its own namespaced keys

- **Never leave a translation empty or use the English value in fr/es** — provide proper translations

---

## DO

- **Search existing keys before creating new ones** — reuse when appropriate

  ```bash
  rg "button.save" src/locales/en.json
  ```

- **Keep all 3 locale files in sync** — same keys, same order

- **Use interpolation for dynamic content** instead of string concatenation

- **Group related keys together** when adding multiple keys for the same feature

- **Provide accurate French and Spanish translations** — not literal word-for-word translations but natural phrasing

---

## Verification

After adding translations, verify:

```bash
# Check that all 3 files have the same number of keys
rg -c '"[^"]+":' src/locales/en.json src/locales/fr.json src/locales/es.json

# Check that a specific key exists in all files
rg "customer.button.delete" src/locales/

# Find untranslated hardcoded strings in components (potential misses)
# Look for JSX text content that isn't wrapped in t()
sg -p '<Button>$TEXT</Button>' --lang tsx src/screens/
```

---

## Reference Files

- `src/i18n.ts` — i18next configuration (fallback: English)
- `src/locales/en.json` — English translations
- `src/locales/fr.json` — French translations
- `src/locales/es.json` — Spanish translations
