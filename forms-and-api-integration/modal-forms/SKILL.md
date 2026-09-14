---
name: modal-forms
description: Zod + react-hook-form integration for validated forms inside MultiStepModal custom steps. Covers makeSchema factory pattern with translated validation, useForm + zodResolver setup, the required "use no memo" directive, FormProvider wrapping, error rendering, and submit approaches (trigger/getValues vs handleSubmit). Use when adding form validation to a modal step. For MultiStepModal API reference see modal-config skill. For file organization patterns see modal-patterns skill.
---

# MultiStepModal — Form Validation

> **Related skills**: `modal-config` (API reference), `modal-patterns` (file organization), `react-compiler` (memoization and `"use no memo"` rules)

## 1. Schema Factory

Always create the schema in a `makeSchema` factory that receives the `t` function so validation messages are translated:

```typescript
function makeSchema(t: (key: string) => string) {
  return z.object({
    code: z.string().trim().min(1, t("validation.code.required")),
    name: z.string().trim().min(1, t("validation.name.required")),
  });
}
type FormValues = z.infer<ReturnType<typeof makeSchema>>;
```

---

## 2. useForm Setup

```typescript
"use no memo";

const schema = makeSchema(t);

const methods = useForm<FormValues>({
  resolver: zodResolver(schema),
  defaultValues: { code: "", name: "" },
  mode: "onChange",
  reValidateMode: "onBlur",
});

const {
  register,
  formState: { errors, isValid, isSubmitting },
  reset,
} = methods;
```

---

## 3. Custom Step JSX with FormProvider

Wrap form content in `<FormProvider {...methods}>` when using `register`:

```typescript
{
  id: "my-step",
  label: t("my.modal.step.label"),
  config: {
    type: "custom",
    component: (
      <FormProvider {...methods}>
        <div>
          <InputBox label={t("field.code")} variant="text" {...register("code")} />
          {Boolean(errors.code) ? (
            <p className="text-red-600 text-sm mt-1">{errors.code?.message}</p>
          ) : null}
          <InputBox label={t("field.name")} variant="text" {...register("name")} />
          {Boolean(errors.name) ? (
            <p className="text-red-600 text-sm mt-1">{errors.name?.message}</p>
          ) : null}
        </div>
      </FormProvider>
    ),
  },
}
```

---

## 4. Submit Approaches

Since modal form submissions are triggered via `finalActions.onClick` (not a native `<form onSubmit>`), there are two valid approaches used in the codebase:

### Approach A: `trigger()` + `getValues()` (manual)

Used in `PointStatisticsFormulaReportModal.tsx` and `PointStatisticsChartReportModal.tsx`:

```typescript
const onSubmit = async () => {
  const valid = await methods.trigger();
  if (!valid) return;

  const values = methods.getValues();
  await createEntity({ entityId, ...values }).unwrap();
  reset();
  onClose();
};
```

### Approach B: `handleSubmit()` wrapper

Used in `addRelationsModal.tsx`:

```typescript
const onSubmit = handleSubmit((values) => {
  createEntity({ entityId, ...values });
  onClose();
});

// In finalActions:
finalActions: [
  {
    label: t("label.save"),
    variant: "save",
    disabled: isSubmitting || !isValid,
    onClick: () => onSubmit(),
  },
],
```

Both are valid. Use Approach A when you need `async/await` flow control (e.g., waiting for mutation before closing). Use Approach B for simpler fire-and-forget submissions.

---

## 5. Wiring into finalActions

Connect form state to the modal's action buttons:

```typescript
finalActions: [
  {
    label: isSubmitting ? t("button.submitting") : t("button.submit"),
    variant: "save",
    disabled: isSubmitting || !isValid,
    onClick: onSubmit,
  },
],
```

Because these modal configs use `react-hook-form`, add `"use no memo";` and return the config directly. Do not wrap the schema, submit handler, or config in `useMemo`/`useCallback`.

---

## 6. Full Example (config hook)

```typescript
export function useMyModalConfig(
  entityId: number,
  onClose: () => void,
): MultiStepModalConfig {
  "use no memo";

  const { t } = useTranslation();
  const [createEntity] = useCreateEntityMutation();
  const schema = makeSchema(t);

  const methods = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { code: "", name: "" },
    mode: "onChange",
    reValidateMode: "onBlur",
  });

  const {
    register,
    formState: { errors, isValid, isSubmitting },
    reset,
  } = methods;

  const onSubmit = async () => {
    const valid = await methods.trigger();
    if (!valid) return;

    const values = methods.getValues();
    await createEntity({ entityId, ...values }).unwrap();
    reset();
    onClose();
  };

  return {
    id: "my-modal",
    title: t("my.modal.title"),
    width: "2xl",
    height: "auto",
    steps: [
      {
        id: "my-step",
        label: t("my.modal.step.label"),
        config: {
          type: "custom",
          component: (
            <FormProvider {...methods}>
              <div>
                <InputBox label={t("field.code")} variant="text" {...register("code")} />
                {Boolean(errors.code) ? (
                  <p className="text-red-600 text-sm mt-1">{errors.code?.message}</p>
                ) : null}
                <InputBox label={t("field.name")} variant="text" {...register("name")} />
                {Boolean(errors.name) ? (
                  <p className="text-red-600 text-sm mt-1">{errors.name?.message}</p>
                ) : null}
              </div>
            </FormProvider>
          ),
        },
      },
    ],
    finalActions: [
      {
        label: isSubmitting ? t("button.submitting") : t("button.submit"),
        variant: "save",
        disabled: isSubmitting || !isValid,
        onClick: onSubmit,
      },
    ],
  };
}
```

---

## 7. Form Checklist

- [ ] `makeSchema` receives `t` for translated validation messages
- [ ] The function body starts with `"use no memo";`
- [ ] Schema is created directly with `makeSchema(t)`
- [ ] `useForm` uses `zodResolver(schema)` with `mode: "onChange"` and `reValidateMode: "onBlur"`
- [ ] Form content wrapped in `<FormProvider {...methods}>`
- [ ] Error rendering uses `Boolean(errors.field)` (not `errors.field &&`)
- [ ] `reset()` is called before `onClose()` in submit handlers
- [ ] No `useMemo` / `useCallback` added just to support the modal config
