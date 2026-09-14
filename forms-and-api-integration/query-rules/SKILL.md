---
name: query-rules
description: Rules about the implementation of an api call in the Frontend. Use when an API call needs to be created / added in the Frontend.
---

# Query Rules

## API Routes Management

CRITICAL: All API routes MUST be defined as const variables in `src/api/apiRoutes.ts` before use.

### Adding New Routes

1. Open `src/api/apiRoutes.ts`
2. Add your route to the `API_ROUTES` object:

```typescript
export const API_ROUTES = {
  // ... existing routes
  GET_ITEMS: "api/getItems", // ✅ Add new route
  SAVE_ITEM: "api/saveItem", // ✅ Add new route
  DELETE_ITEM: "api/deleteItem", // ✅ Add new route
} as const;
```

NEVER hardcode URLs in API files. Always reference `API_ROUTES.*`

## RTK Query API Pattern

### API Definition (`src/api/*Api.ts`)

```typescript
import { baseApi } from "./baseApi";
import { API_ROUTES } from "./apiRoutes";

export const featureApi = baseApi.injectEndpoints({
  endpoints: (builder) => ({
    // Query example
    getItems: builder.query<Item[], void>({
      query: () => ({
        url: API_ROUTES.GET_ITEMS, // ✅ Use API_ROUTES constant
        method: "get",
      }),
      providesTags: ["Items"],
    }),

    // Mutation example with toast
    saveItem: builder.mutation<unknown, ItemPayload>({
      query: (payload) => ({
        url: API_ROUTES.SAVE_ITEM, // ✅ Use API_ROUTES constant
        method: "post",
        body: payload,
        showSuccessToast: true, // Auto success toast
        successMessage: "items.success.save", // i18n key
      }),
      invalidatesTags: ["Items"],
    }),

    // Delete example
    deleteItem: builder.mutation<unknown, { id: number }>({
      query: (payload) => ({
        url: API_ROUTES.DELETE_ITEM, // ✅ Use API_ROUTES constant
        method: "post",
        body: payload,
        showSuccessToast: true,
        successMessage: "items.success.delete",
      }),
      invalidatesTags: ["Items"],
    }),
  }),
  overrideExisting: false,
});

export const { useGetItemsQuery, useSaveItemMutation, useDeleteItemMutation } =
  featureApi;
```

### Toast Configuration

| Field              | Type    | Default                 | Usage                               |
| ------------------ | ------- | ----------------------- | ----------------------------------- |
| `showSuccessToast` | boolean | false                   | Show success toast (mutations only) |
| `successMessage`   | string  | "toast.default.success" | i18n key for success                |
| `showErrorToast`   | boolean | false                   | Show error toast                    |
| `errorMessage`     | string  | "toast.default.error"   | i18n key for error                  |

**Note**: Success toasts only trigger for `post`, `patch`, `delete` methods (line 98-100 in axiosBaseQuery.ts).

⚠️ `showSuccessToast` has **no effect on queries** - only mutations.

## Screen Usage Pattern

> **Error handling, `isLoading`/`isError`, and `try/catch` rules** are defined in the **`error-handling-conventions`** skill. Refer to it for all error handling patterns.

### Query Hook (Read Operations)

```typescript
const Screen: React.FC = () => {
  const { data } = useGetItemsQuery();
  return <DataTable data={data} />;
};
```

### Mutation Hook (Write Operations)

```typescript
const Screen: React.FC = () => {
  const [saveItem] = useSaveItemMutation();
  const [deleteItem] = useDeleteItemMutation();

  const handleSave = (payload: ItemPayload) => {
    saveItem(payload);
  };

  const handleDelete = (id: number) => {
    deleteItem({ id });
  };

  return <Form onSubmit={handleSave} />;
};
```

### Custom Hook Pattern (Extract Logic)

```typescript
const useItemData = (itemId: number | undefined) => {
  const { data } = useGetItemDetailQuery(
    itemId ?? 0,
    {
      skip: !itemId,  // Don't fetch if no itemId
    }
  );

  return { data };
};

// Usage in screen
const Screen: React.FC = () => {
  const { data: item } = useItemData(itemId);

  if (!item) {
    return null;  // Or loading placeholder
  }

  return <ItemDetails item={item} />;
};
```

## Cache Invalidation

Always define the tags inside `src/api/tagTypes.ts`.

Use `providesTags` and `invalidatesTags` for automatic cache management:

```typescript
// Provide tags (queries) — only tag the entity type the query returns
providesTags: ["Items"];

// Invalidate tags (mutations) — only invalidate tags affected by the mutation
invalidatesTags: ["Items"]; // Refetches all queries with "Items" tag
```

> **Rule**: A query should only provide tags for its own entity type. Do not tag unrelated entities — this causes unnecessary cache invalidation.

**Available tags**: See `src/api/tagTypes.ts`

## Common Patterns

### Conditional Query (skip pattern)

```typescript
const { data } = useGetItemQuery(itemId ?? 0, {
  skip: !itemId, // Don't run query if no ID
});
```

### Polling

```typescript
const { data } = useGetItemsQuery(undefined, {
  pollingInterval: 5000, // Refetch every 5s
});
```

## Type Safety

### Response Types

```typescript
// Define in feature types.ts or API file
export interface Item {
  id: number;
  name: string;
  status: "active" | "inactive";
}

// Use in endpoint
builder.query<Item[], void>({
  // Item[] is response type
  // void is argument type
});

builder.mutation<unknown, ItemPayload>({
  // unknown is response type (often unused)
  // ItemPayload is argument type
});
```

## DO NOT

❌ **Never use `any` for response types**

```typescript
// WRONG
builder.query<any, void>({  // ❌ Define proper type
  ...
})

// CORRECT
builder.query<Item[], void>({
  ...
})
```

❌ **Never hardcode API URLs**

```typescript
// WRONG
query: () => ({
  url: "api/getItems", // ❌ Hardcoded URL
  method: "get",
});

// CORRECT
query: () => ({
  url: API_ROUTES.GET_ITEMS, // ✅ Use API_ROUTES constant
  method: "get",
});
```

## DO

✅ **Add routes to API_ROUTES first**

```typescript
// 1. Add to src/api/apiRoutes.ts
export const API_ROUTES = {
  GET_ITEMS: "api/getItems",
} as const;

// 2. Use in API definition
query: () => ({
  url: API_ROUTES.GET_ITEMS,
  method: "get",
});
```

## Summary

1. **All API routes MUST be in API_ROUTES** - never hardcode URLs in API files
2. **Type everything properly** - no `any` types allowed
3. **Use RTK Query's built-in features** - cache invalidation, polling, skip, etc.
4. **Error handling** - see the **`error-handling-conventions`** skill

---

**Related Files:**

- `src/api/apiRoutes.ts` - **API route constants (ADD ALL ROUTES HERE FIRST)**
- `src/helpers/Query/axiosBaseQuery.ts` - Error handling middleware
- `src/api/baseApi.ts` - Base API configuration
- `src/api/tagTypes.ts` - Available cache tags
