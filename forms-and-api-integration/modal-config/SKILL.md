---
name: modal-config
description: Core API reference for the MultiStepModal component. Covers component props, MultiStepModalConfig interface, step types (custom/table/input), finalActions vs onComplete, footer button customization (customCancelAction, customCompleteAction, visibility toggles), and DocumentUploadStep. Use when configuring a MultiStepModal or choosing between step types and action patterns. For file organization patterns see modal-patterns skill. For zod + react-hook-form see modal-forms skill.
---

# MultiStepModal — Configuration Reference

> **Related skills**: `modal-patterns` (file organization), `modal-forms` (zod + react-hook-form)

## 1. Component API

```typescript
import { MultiStepModal, type MultiStepModalConfig } from "@/shared/components/MultiStepModal";

<MultiStepModal
  config={config}       // MultiStepModalConfig (required)
  isOpen={isOpen}       // boolean (required)
  onClose={onClose}     // () => void (required)
  initialData={data}    // Record<string, unknown> (optional)
  onComplete={handler}  // (allStepData) => void | Promise<void> (optional)
/>
```

### MultiStepModalConfig

```typescript
interface MultiStepModalConfig {
  id: string;                          // Unique modal identifier (kebab-case)
  title: string;                       // Modal header title
  description?: string;                // Subtitle below title
  steps: ModalStep[];                  // One or more steps
  finalActions?: StepAction[];         // Custom action buttons (replaces default Complete)
  width?: "sm" | "md" | "lg" | "xl" | "2xl" | "full";   // default: "md"
  height?: "auto" | "sm" | "md" | "lg" | "xl" | "full";  // default: "md"
  allowClose?: boolean;
  customCancelAction?: StepAction;     // Override default Cancel button
  customCompleteAction?: StepAction;   // Override default Complete button
  isCancelButtonVisible?: boolean;     // default: true
  isCompleteButtonVisible?: boolean;   // default: true
}
```

### Key behavior

- **Single step**: StepIndicator is hidden; only step content + actions are shown.
- **Multi-step**: StepIndicator renders with progress, Previous/Next navigation appears.
- **finalActions**: When provided on the last step (or single step), these buttons replace the default Complete button. Each `StepAction.onClick` receives `(currentStepData, allStepData)`.
- **onComplete**: Called when the default Complete button is clicked (only if `finalActions` is not provided and `customCompleteAction` is not set).

---

## 2. Step Types

### 2a. Custom Step (most common in this codebase)

Use `type: "custom"` when you need full control over the step content. Pass any React component via the `component` property.

```typescript
{
  id: "my-step",
  label: t("my.step.label"),
  config: {
    type: "custom",
    component: <MyCustomComponent />,
  },
}
```

### 2b. Table Step

Use `type: "table"` for a built-in `DataTable` with optional selection.

```typescript
{
  id: "select-items",
  label: t("select.items.label"),
  config: {
    type: "table",
    columns: myColumns,
    data: myData,                       // static data
    // OR use rtqQuery for fetched data:
    // rtqQuery: { hook: useGetItemsQuery },
    selectable: true,
    multiSelect: true,
  },
}
```

### 2c. Input Step

Use `type: "input"` for auto-generated form fields.

```typescript
{
  id: "user-info",
  label: "User Info",
  config: {
    type: "input",
    fields: [
      { name: "email", label: "Email", type: "email", required: true },
      { name: "role", label: "Role", type: "select", options: [...] },
    ],
  },
}
```

### When to use which

| Use `custom` | Use `table` | Use `input` |
|---|---|---|
| Need `react-hook-form` + zod validation | Simple data table with row selection | Simple form without complex validation |
| Need shared components (`InputBox`, `DropdownRadix`, `DatePicker`) | Built-in RTK Query data fetching | Quick prototyping |
| Complex layouts combining form fields + tables | No custom rendering needed | No custom component needs |

> **In practice**, most modals in this codebase use `type: "custom"` because they require shared components and zod validation.

---

## 3. finalActions vs onComplete

| Feature | `finalActions` | `onComplete` |
|---------|---------------|-------------|
| Where defined | In `MultiStepModalConfig` | As a prop on `<MultiStepModal>` |
| Custom buttons | Yes (label, variant, disabled, onClick) | No (uses default Complete button) |
| Access to data | `onClick(currentStepData, allStepData)` | `onComplete(allStepData)` |
| When to use | When you need custom button labels, disable states, or submit logic | When using the built-in step data collection (input/table steps) |

> **In this codebase**, `finalActions` is the dominant pattern because most modals use custom step content with their own form state (zod + react-hook-form) rather than the built-in `stepData` collection.

### finalActions pattern

```typescript
finalActions: [
  {
    label: t("label.save"),
    variant: "save",
    disabled: isSubmitting || !isValid,
    onClick: onSubmit,   // your custom submit handler
  },
],
```

### onComplete pattern (for built-in step data)

```typescript
<MultiStepModal
  config={config}
  isOpen={isOpen}
  onClose={onClose}
  onComplete={async (allStepData) => {
    const uploadData = allStepData.upload as { file: File };
    await uploadFile(uploadData.file);
  }}
/>
```

---

## 4. Footer Button Customization

The modal footer has two button zones:

```
┌────────────────────────────────────────────────────────┐
│ [Per-step actions]          [Previous] [Action] [Cancel] │
│ (ModalStep.actions)                                      │
└────────────────────────────────────────────────────────┘
```

### Button rendering priority (right side)

The **Action** slot renders based on this priority (first match wins):

| Priority | Condition | Renders |
|----------|-----------|---------|
| 1 | `isCompleteButtonVisible: false` | Nothing |
| 2 | Not last step (multi-step) | **Next** button |
| 3 | `customCompleteAction` is set | Custom complete button |
| 4 | `finalActions` is set | Final action button(s) |
| 5 | None of the above | Default **Complete** button |

The **Cancel** slot:

| Priority | Condition | Renders |
|----------|-----------|---------|
| 1 | `isCancelButtonVisible: false` | Nothing |
| 2 | `customCancelAction` is set | Custom cancel button |
| 3 | None of the above | Default **Cancel** button |

### 4a. `customCancelAction` — Replace Cancel button

Use when you want a different label or behavior for the dismiss button. The real codebase usage is `BuildingDetailAddUserModal.tsx`, where the modal has no form submission — actions happen inline per-row, so the only footer button is a "Close" button:

```typescript
const modalConfig: MultiStepModalConfig = {
  id: "building-add-user-modal",
  title: t("building.detail.users.modal.label"),
  width: "2xl",
  height: "auto",
  customCancelAction: {
    label: t("label.close"),
    onClick: () => {
      onClose();
    },
  },
  isCompleteButtonVisible: false,
  steps: [
    {
      id: "add-user",
      label: t("building.detail.users.modal.label"),
      config: {
        type: "custom",
        component: (
          <DataTable
            columns={columns}  // columns include per-row action buttons
            data={data}
            tableId="building-user-add-user-table"
          />
        ),
      },
    },
  ],
};
```

### 4b. `customCompleteAction` — Replace Complete button

Use when you want a custom label or behavior for the primary action button (overrides both `finalActions` and the default Complete button):

```typescript
const modalConfig: MultiStepModalConfig = {
  id: "my-modal",
  title: t("modal.title"),
  customCompleteAction: {
    label: t("button.submit.and.notify"),
    variant: "default",
    onClick: async (_stepData, allStepData) => {
      await submitAndNotify(allStepData);
    },
  },
  steps: [{ /* ... */ }],
};
```

> **Note**: `customCompleteAction` has higher priority than `finalActions`. Do not set both — `finalActions` will be ignored.

### 4c. Hiding all default buttons

When a custom step manages its own actions internally:

```typescript
const modalConfig: MultiStepModalConfig = {
  id: "self-managed-modal",
  title: t("modal.title"),
  isCancelButtonVisible: false,
  isCompleteButtonVisible: false,
  steps: [
    {
      id: "content",
      label: t("step.label"),
      config: {
        type: "custom",
        component: <SelfManagedContent />,  // has its own save/cancel buttons
      },
    },
  ],
};
```

---

## 5. DocumentUploadStep

For file upload modals, use the built-in `DocumentUploadStep`:

```typescript
import { DocumentUploadStep } from "@/shared/components/MultiStepModal";

// In step config:
{
  id: "upload",
  label: t("upload.label"),
  config: {
    type: "custom",
    component: <DocumentUploadStep accept="image/*" onDataChange={() => {}} />,
    validate: (stepData: unknown) => {
      const data = stepData as { file: File | null };
      return {
        isValid: Boolean(data?.file),
        errors: data?.file ? [] : [t("validation.file.required")],
      };
    },
  },
}
```

`DocumentUploadStep` communicates file selection through the built-in `onDataChange` callback (injected via `cloneElement`). The selected file is stored in the modal's internal `stepData` under the step's `id` key. You can access this data in two ways:

- **`finalActions.onClick`**: The `allStepData` parameter includes `{ [stepId]: { file: File } }`. This is the pattern used in the codebase (see `BuildingDetailImagesTab.tsx`).
- **`onComplete` prop**: Also receives `allStepData` with the file data, but only fires from the default Complete button (which is hidden when `finalActions` is set).

---

## 6. Config Checklist

- [ ] All user-visible strings use `t()` translations
- [ ] Modal `id` is unique and descriptive (kebab-case)
- [ ] `tableId` props on `DataTable` are unique per modal instance
- [ ] `width` and `height` are set appropriately (`"2xl"` + `"auto"` is the most common)
- [ ] `finalActions` `onClick` handles submit logic (not relying on `onComplete` when using custom forms)
