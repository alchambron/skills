# Isolated feature testing

Read after a PR's static review has no actionable defect. Use the manual guide's scenarios and expected outcomes, then execute them; do not stop at generating a checklist or reaching a login page.

## Target runtime

The default is a detached disposable worktree at the exact reviewed PR head, outside the user's checkout. Label all evidence `pr-head-only`. When the user asks for integration testing, build an isolated merge of the pinned base and head; verify both parents. Abort conflicts and report the integration limit rather than resolving them as part of review. A live deployed site is relevant only when its exact revision is verified and its mutation scope is authorized.

Use one bounded runtime per PR; run memory-heavy apps sequentially within each child. Children own distinct worktrees, ports, cloned databases, tabs and result files. The coordinator gathers results and owns GitHub publication.

For JouleMV, from the disposable worktree run this skill's bundled helper:

```sh
python3 "$SKILL_DIR/scripts/agent_run.py" doctor
python3 "$SKILL_DIR/scripts/agent_run.py" start --schema-mode current
```

Choose `current` only when the reviewed change adds no tenant SQL migration and the existing cloned schema can exercise its behavior. It clones local data unchanged and does not verify migration execution. For SQL migration changes, choose `--schema-mode migrate`; a migration failure remains a blocker unless the actual prerequisite can be corrected within the owned fixture scope. Do not bypass the migration being tested. The helper checks local source DB identity and ownership, disables external notification transports, uses private Vite caches, and records commit/schema mode in its receipts.

Read the command's current `--help`, JSON and logs paths rather than caching ports, JDK requirements or source database names. Require the returned commit to match the intended target. HTTP responsiveness, including a legacy `/login` 401, is transport readiness only; real credential login and actual API data must succeed before any feature scenario passes. A missing dependency or server prerequisite is a concrete blocker, not a reason to claim a test succeeded.

Other repositories need their own verified startup procedure. For backend-only changes without a UI, provide technical acceptance checks using the actual changed code and suitable local dependencies. For notification changes, a loopback SMTP collector can prove the message/query behavior without external delivery; identify fixture-schema, scheduler and delivery limits. Do not present mocked network data as live backend evidence.

## Browser and fixtures

Use native T3 `preview_status`, then `preview_open` when needed. Each parallel child opens with `reuseExistingTab: false` and uses its returned `tabId` on every subsequent call. Honor the user's tab ownership. Prefer semantic locators from fresh snapshots; follow the runtime's browser-fallback rules when tools fail. The older browser skill's credentials guidance remains useful even when T3 preview is the available browser.

Read development credentials privately from the documented environment source and feed them directly to the login tools. Never print values, tokens, complete environment files or credential-bearing logs. Use real backend permissions for authorization scenarios. Controlled client-state simulations may help diagnosis but do not prove backend authorization.

Prefer existing records, then disposable test records in the owned clone. Seed boundary data or change a test account's role only inside that clone with explicit database identity checks. Record the fixture and restoration. Missing original tickets, production accounts, language controls, root fixtures, or rich data remain visible coverage gaps. Distinguish seed/schema skew from defects in the reviewed change; compare canonical source definitions where IDs or types are uncertain.

## Evidence

For each scenario save its stable ID, preconditions, verified route, concrete actions, expected and observed result. Test every distinct changed user flow, plus directly affected regressions. Continue useful independent checks after a failure; mark dependent checks blocked with a reason. An unavailable screenshot alone does not turn an independently observed behavior into a fabricated pass or failure.

Save `preview_snapshot({save:true})` for each visible observed outcome. Inspect the saved image for legibility **and agreement with the final rendered outcome**. A crisp screenshot of an earlier state is not proof. Wait for the relevant values, resize if useful, and retry capture at most twice; then retain page/API evidence and mark visual evidence unavailable. Save no blank frames as acceptance pictures.

Copy each downloaded export into the run evidence directory immediately, before another download can evict it. Verify its filename and relevant contents, such as rows, amounts, headers and click-time timestamp. Retain the file and verification description. Display precision and export precision are separate expectations unless the feature spec ties them together.

Use unique scenario IDs; distinguish variants and keep retried attempts under one final outcome. Keep convention observations, preexisting behavior, feature specification gaps and environmental blockers distinct from verified new defects.

## Cleanup and failure handling

After any start attempt, use the same helper from the owned worktree to run `stop` in a finally-style path. Verify the owned database was dropped and server processes stopped; inspect `status` or ownership-checked process/database evidence if the receipt is ambiguous. An `already_stopped` result is sufficient only when owned resources are confirmed absent. Never stop another thread's runtime or drop a source/shared database.

Remove the detached worktree, including privately copied `.env` files, only after cleanup succeeds and evidence has been retained outside it. A copied helper or bounded test adapter may diagnose a startup incompatibility; record exactly what it changes. Prefer the bundled schema-mode/readiness controls over ad hoc patches, and keep PR source untouched.

Report failed cleanup explicitly and retain ownership metadata for a safe retry. A runtime watchdog is a backstop, not a cleanup receipt.
