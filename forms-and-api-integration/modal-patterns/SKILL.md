---
name: modal-patterns
description: File organization patterns for screens using MultiStepModal. Covers inline config for single-modal screens, the ModalConfiguration router pattern for multi-modal screens (Modal/ directory, config hooks, provider components, MODAL_PROVIDERS map, discriminator types), and cross-step data sharing via Redux datatableSlice. Use when deciding how to organize modal code in a screen or when a screen has multiple modals. For MultiStepModal API reference see modal-config skill. For form validation see modal-forms skill.
---

# MultiStepModal — File Organization Patterns

> **Related skills**: `modal-config` (API reference), `modal-forms` (zod + react-hook-form), `react-compiler` (memoization and `"use no memo"` rules)

## Decision Flow

When adding a modal to a screen, check the current state:

```
Adding a modal to a screen?
│
├─ Screen has NO modals yet
│  └─ Will this screen need more modals in the future?
│     ├─ No  → Use Pattern A (inline config)
│     └─ Yes → Use Pattern B (ModalConfiguration router) from the start
│
├─ Screen has already 1 modal inside (inline config)
│  └─ Refactor to Pattern B:
│     1. Create Modal/ directory
│     2. Move existing modal config into a useXModalConfig hook
│     3. Create [Feature]ModalConfiguration.tsx router
│     4. Add the new modal as a second config hook + provider
│     5. Update the screen to import the router instead of inline config
│
└─ Screen already has a ModalConfiguration router
   └─ Add to existing:
      1. Create a new [ModalName]Modal.tsx with a useXModalConfig hook
      2. Add a new provider component in the ModalConfiguration file
      3. Add the new entry to the MODAL_PROVIDERS map
      4. Add the new discriminator value to the SubjectId/ModalType union
```

### Quick Reference

| Scenario                 | Pattern                                           | Example                                 |
| ------------------------ | ------------------------------------------------- | --------------------------------------- |
| Screen has **1 modal**   | Inline config in the component                    | `BuildingDetailClassesTab.tsx`          |
| Screen has **2+ modals** | `Modal/` directory with ModalConfiguration router | `BuildingDetailsModalConfiguration.tsx` |

---

## 1. Pattern A: Inline Config (single modal)

When a screen has only one modal, define the config directly in the component.

**File**: `src/screens/[Feature]/[Tab].tsx` or `src/screens/[Feature]/[Modal].tsx`

```typescript
import { MultiStepModal, type MultiStepModalConfig } from "@/shared/components/MultiStepModal";

function MyComponent() {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const { t } = useTranslation();

  const modalConfig: MultiStepModalConfig = {
    id: "my-modal",
    title: t("my.modal.title"),
    width: "2xl",
    height: "auto",
    steps: [
      {
        id: "step-1",
        label: t("my.modal.step.label"),
        config: {
          type: "custom",
          component: <MyStepContent />,
        },
      },
    ],
    finalActions: [
      {
        label: t("label.save"),
        variant: "save",
        disabled: !isValid,
        onClick: handleSubmit,
      },
    ],
  };

  return (
    <div>
      <Button onClick={() => setIsModalOpen(true)}>{t("button.open")}</Button>
      <MultiStepModal
        config={modalConfig}
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
      />
    </div>
  );
}
```

> Inline configs do not require `useMemo`. Pattern B config hooks also default to plain objects unless a library-specific exception from `react-compiler` applies.

---

## 2. Pattern B: ModalConfiguration Router (2+ modals)

When a screen has multiple modals, use a `Modal/` directory with a router component that switches between provider components based on a discriminator.

### File structure

```
src/screens/[Feature]/
  ├── [Screen].tsx                       # Screen component (passes subjectId/modalType)
  └── Modal/
      ├── [Feature]ModalConfiguration.tsx  # Router (entry point)
      ├── [Modal1Name]Modal.tsx            # Config hook for modal 1
      └── [Modal2Name]Modal.tsx            # Config hook for modal 2
```

### Step 1: Create config hooks (one per modal)

Each modal gets its own file exporting a `useXModalConfig` hook that returns `MultiStepModalConfig`. Return the config directly by default. Only add manual memoization for an allowed `react-compiler` exception such as TanStack Table or Recharts.

```typescript
// Modal/MyFeatureCreateModal.tsx
import type { MultiStepModalConfig } from "@/shared/components/MultiStepModal";

export function useCreateModalConfig(
  entityId: number,
  onClose: () => void,
): MultiStepModalConfig {
  const { t } = useTranslation();
  const [createEntity] = useCreateEntityMutation();

  // ... form setup, columns, handlers ...

  return {
    id: "my-feature-create-modal",
    title: t("my.feature.create.modal.title"),
    width: "2xl",
    height: "auto",
    steps: [
      {
        /* ... */
      },
    ],
    finalActions: [
      {
        /* ... */
      },
    ],
  };
}
```

### Step 2: Create provider components in the ModalConfiguration file

Each provider component wraps the config hook and renders `<MultiStepModal>`.

```typescript
// Modal/MyFeatureModalConfiguration.tsx
import { MultiStepModal } from "@/shared/components/MultiStepModal";
import { useCreateModalConfig } from "./MyFeatureCreateModal";
import { useEditModalConfig } from "./MyFeatureEditModal";

type ModalConfigProviderProps = {
  isOpen: boolean;
  onClose: () => void;
  entityId: number;
};

function CreateProvider({
  isOpen,
  onClose,
  entityId,
}: ModalConfigProviderProps) {
  const config = useCreateModalConfig(entityId, onClose);
  return <MultiStepModal config={config} isOpen={isOpen} onClose={onClose} />;
}

function EditProvider({
  isOpen,
  onClose,
  entityId,
}: ModalConfigProviderProps) {
  const config = useEditModalConfig(entityId, onClose);
  return <MultiStepModal config={config} isOpen={isOpen} onClose={onClose} />;
}
```

### Step 3: Create the router with a MODAL_PROVIDERS map

Use a discriminator type (numeric `SubjectId` or string union `ModalType`) to select the correct provider.

**Numeric discriminator** (when modals are triggered by action buttons with IDs):

```typescript
type SubjectId = 1 | 2;

type ModalProvider = (props: ModalConfigProviderProps) => React.ReactElement;

const MODAL_PROVIDERS: Record<SubjectId, ModalProvider> = {
  1: CreateProvider,
  2: EditProvider,
};

function MyFeatureModals({
  isOpen,
  onClose,
  entityId,
  subjectId,
}: MyFeatureModalProps) {
  const Provider = MODAL_PROVIDERS[subjectId as SubjectId];
  if (!Provider) return null;

  return (
    <Suspense fallback={null}>
      <Provider entityId={entityId} isOpen={isOpen} onClose={onClose} />
    </Suspense>
  );
}

export default MyFeatureModals;
```

**String discriminator** (when modal type is a readable string):

```typescript
type ModalType = "classes" | "parent-relation" | "child-relation";

type ModalProvider = (props: ModalConfigProviderProps) => React.ReactElement;

const MODAL_PROVIDERS: Record<ModalType, ModalProvider> = {
  classes: ClassesProvider,
  "parent-relation": ParentRelationProvider,
  "child-relation": ChildRelationProvider,
};

function EntitiesModal({
  isOpen,
  onClose,
  entityId,
  modalType,
}: EntitiesModalProps) {
  const Provider = MODAL_PROVIDERS[modalType];
  if (!Provider) return null;

  return (
    <Suspense fallback={null}>
      <Provider entityId={entityId} isOpen={isOpen} onClose={onClose} />
    </Suspense>
  );
}
```

**When to use named SUBJECT_IDS constants**:

If the discriminator is numeric and its meaning is not self-evident, export a `SUBJECT_IDS` constant object:

```typescript
export const SUBJECT_IDS = {
  GROSS_FLOOR_AREA: 1,
  EXTERNAL_TEMPERATURE: 2,
  WEATHER_STATION: 3,
} as const;

export type SubjectId = (typeof SUBJECT_IDS)[keyof typeof SUBJECT_IDS];
```

---

## 3. Multi-step Selection Across Steps

For modals where step 1 selects data and step 2 acts on it, use Redux `datatableSlice` to share selection state:

```typescript
// Step 1: Selection table
{
  id: "select-entities",
  label: t("step.1.label"),
  config: {
    type: "custom",
    component: (
      <DataTable
        columns={columnsEntities}
        data={allEntities}
        isSelectable
        tableId="my-modal-entities-table"
      />
    ),
  },
}

// Step 2: Uses selected rows from step 1
// Read selected rows via useSelector:
const selectedEntities = useSelector((state: RootState) =>
  selectSelectedRows<Entity>(state, "my-modal-entities-table"),
);
```

For removing selected rows from step 2:

```typescript
import { removeSelectedRow } from "@/shared/components/Datatable/Main/datatableSlice";

// In column buttonConfigs:
{
  action: "delete",
  mutationTrigger: async () => {
    dispatch(removeSelectedRow({ tableId: "my-modal-entities-table", rowId: row.rowId }));
    return { data: undefined };
  },
}
```

---

## 4. Patterns Checklist

- [ ] Screen with 2+ modals uses the ModalConfiguration router pattern with `Modal/` directory
- [ ] Config hooks (`useXModalConfig`) return plain config objects by default
- [ ] If a config hook uses `react-hook-form`, it follows the `react-compiler` rule and includes `"use no memo";`
- [ ] If a config hook uses manual memoization for TanStack Table or Recharts, it includes a short reason comment
- [ ] Each provider component only calls one config hook and renders one `<MultiStepModal>`
- [ ] Router uses `<Suspense fallback={null}>` around providers
- [ ] `MODAL_PROVIDERS` map covers all discriminator values with a `!Provider` null guard
