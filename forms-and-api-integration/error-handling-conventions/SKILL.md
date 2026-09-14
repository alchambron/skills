---
name: error-handling-conventions
description: Defines when and why NOT to use isLoading, isError, and try/catch with RTK Query hooks in this project. Use when creating new screens, hooks, or components that call RTK Query hooks. Also use when reviewing code that adds try/catch around mutations, destructures isLoading or isError, or uses .unwrap(). Covers the centralized error handling middleware in axiosBaseQuery.ts and the handleMutationResult helper. Relevant for .ts and .tsx files in src/screens/ and src/hooks/.
---

# Error Handling & Loading State Conventions

## TL;DR

| Pattern | Rule | Why |
|---------|------|-----|
| `isLoading` | **Do NOT use** | Deferred decision - consistency across all screens |
| `isError` | **Do NOT use** | Errors handled globally by middleware |
| `try / catch` | **Do NOT use** (with exceptions) | `axiosBaseQuery` catches everything |
| `handleMutationResult` | Use when you need a **custom success toast** | Checks `"data" in result` without try/catch |
| `.unwrap()` + `try/catch` | Use **only** when you need side-effects on error | e.g., file uploads, multi-step flows |

> See [ADR-001: Centralized Error Handling](../../../docs/adr/001-centralized-error-handling.md) for the full rationale.

---

## 1. Why No `isLoading`?

RTK Query provides `isLoading` and `isFetching` flags on every hook. We intentionally **do not destructure or use them**.

**Reason**: The team decided to defer loading state implementation to maintain consistency. Some screens would have spinners, others wouldn't, and the UX would be inconsistent. When we implement loading states, we'll do it app-wide with a shared pattern.

```typescript
// WRONG - do not destructure isLoading
const { data, isLoading } = useGetItemsQuery();
const [saveItem, { isLoading: isSaving }] = useSaveItemMutation();

// CORRECT - only destructure data
const { data } = useGetItemsQuery();
const [saveItem] = useSaveItemMutation();
```

---

## 2. Why No `isError`?

All API errors are caught and handled at the middleware level by `axiosBaseQuery` (`src/helpers/Query/axiosBaseQuery.ts`):

```
Screen calls hook  -->  axiosBaseQuery wraps call in try/catch
                        |
                        +--> Success: return { data }  +  optional success toast
                        |
                        +--> Error: RequestErrorHandler.handle()
                                    |
                                    +--> 401: re-auth modal
                                    +--> 402: /forbidden redirect
                                    +--> 403: /not-found redirect
                                    +--> Backend error ID: mapped toast message
                                    +--> Network error: general error toast
```

Because errors are **already surfaced to the user** via toasts and redirects, individual screens never need to check `isError`.

```typescript
// WRONG - error is already handled by middleware
const { data, isError, error } = useGetItemsQuery();
if (isError) {
  return <ErrorMessage error={error} />;
}

// CORRECT - just use the data, errors show as toasts automatically
const { data } = useGetItemsQuery();
```

---

## 3. Why No `try / catch`?

The `axiosBaseQuery` in `src/helpers/Query/axiosBaseQuery.ts` already wraps every API call:

```typescript
// axiosBaseQuery.ts (simplified)
async function executeBaseQuery(request) {
  try {
    const data = await executeRequest(...);
    handleSuccessToast(...);          // show success toast if configured
    return { data };
  } catch (raw) {
    runErrorHandlerSafe(raw);         // RequestErrorHandler + backend error toast
    handleErrorToast(...);            // show error toast if configured
    return { error: normalizeError(raw) };
  }
}
```

So in screens, this is redundant:

```typescript
// WRONG - the try/catch does nothing useful, axiosBaseQuery already caught it
const handleSave = async (payload: ItemPayload) => {
  try {
    await saveItem(payload);
  } catch (error) {
    // This will NEVER execute - axiosBaseQuery caught it already
    // RTK Query returns { error } instead of throwing
  }
};

// CORRECT - just call the mutation
const handleSave = (payload: ItemPayload) => {
  saveItem(payload);
};
```

---

## 4. When `handleMutationResult` IS Used

When you need to show a **custom success message** (not configured in the API endpoint), use the `handleMutationResult` helper from `src/util/mutationHelper.ts`.

This does NOT use try/catch. It checks `"data" in result` to determine success:

```typescript
import { handleMutationResult } from "@/util/mutationHelper";
import { useToastHelpers } from "@/shared/components/Toast/hooks";

function useItemHandlers() {
  const { showSuccess } = useToastHelpers();
  const [saveItem] = useSaveItemMutation();
  const { t } = useTranslation();

  const handleSave = async (row: RowType) => {
    const payload = mapRowToPayload(row);
    const result = await saveItem(payload);
    // Shows success toast only when result has data (no error)
    // Error toast is still handled by middleware automatically
    return handleMutationResult(result, showSuccess, t("items.success.save"));
  };

  return { handleSave };
}
```

**Real example** - `src/screens/Configuration/Classes.tsx`:

```typescript
const handleSave = async (currentRowData: RowType) => {
  const result = await saveClass(savePayload);
  return handleMutationResult(result, showSuccess, t("classes.success.save"));
};
```

---

## 5. The Exceptions: When `try / catch` IS Allowed

There are **rare** cases where `try / catch` with `.unwrap()` is necessary. These are when you need to:

1. **Perform a side-effect only on error** (e.g., reset form, show a specific error UI)
2. **Show a custom error message** different from the generic middleware toast
3. **Handle multi-step flows** where failure of one step affects subsequent steps

### Example: File Upload (side-effect on error)

From `src/screens/Building/Tab/BuildingDetailDocumentsTab.tsx`:

```typescript
const handleUploadDocument = async (stepData: Record<string, unknown>) => {
  try {
    const file = validateUploadData(stepData);
    if (!file) {
      showError(
        t("building.documents.upload.error.title"),
        t("building.documents.upload.error.no.file"),
      );
      return;
    }

    // .unwrap() re-throws the error so catch block can handle it
    const result = await uploadBuildingDocument({ buildingId, file }).unwrap();
    handleUploadSuccess(result);  // custom success side-effect (close modal, refresh)
  } catch {
    handleUploadError();  // custom error side-effect (show specific upload error UI)
  }
};
```

**Why this is an exception**: The file upload needs to close a modal on success and show upload-specific error messaging. The generic middleware toast isn't sufficient.

### Key rule for exceptions

If you use `try / catch`, you **must** use `.unwrap()` on the mutation call. Without `.unwrap()`, RTK Query returns `{ error }` instead of throwing, so `catch` will never execute:

```typescript
// WRONG - catch never fires because RTK Query doesn't throw
try {
  await saveItem(payload);  // returns { error: ... }, does NOT throw
} catch {
  // This never executes
}

// CORRECT - .unwrap() makes it throw on error
try {
  await saveItem(payload).unwrap();  // throws if error
} catch {
  // This executes on error
}
```

---

## Decision Tree

```
Need to call an RTK Query mutation?
|
+--> Just fire and forget? (toast handles feedback)
|    --> saveItem(payload);  // no try/catch, no await
|
+--> Need custom success toast?
|    --> const result = await saveItem(payload);
|        handleMutationResult(result, showSuccess, "custom message");
|
+--> Need to do something AFTER success? (navigate, close modal)
|    --> const result = await saveItem(payload);
|        if ("data" in result) { navigate("/somewhere"); }
|
+--> Need custom error handling? (specific error UI, form reset)
     --> try {
           await saveItem(payload).unwrap();
           handleSuccess();
         } catch {
           handleSpecificError();
         }
```

---

## Reference Files

| File | Purpose |
|------|---------|
| `src/helpers/Query/axiosBaseQuery.ts` | Global try/catch wrapper for all API calls |
| `src/helpers/Request/RequestErrorHandler.ts` | HTTP status code routing (401, 402, 403, etc.) |
| `src/util/mutationHelper.ts` | `handleMutationResult` helper |
| `src/api/baseApi.ts` | RTK Query base configuration |
| `docs/adr/001-centralized-error-handling.md` | Architecture Decision Record |
