# Isolated feature testing

Read before preparing a runtime for the PR's planned acceptance scenarios. Use the manual guide's scenarios and expected outcomes, then execute them; do not stop at generating a checklist or reaching a login page.

## Target runtime

The default is a detached disposable worktree at the exact reviewed PR head, outside the user's checkout. Label all evidence `pr-head-only`. When the user asks for integration testing, build an isolated merge of the pinned base and head; verify both parents. Abort conflicts and report the integration limit rather than resolving them as part of review. A live deployed site is relevant only when its exact revision is verified and its mutation scope is authorized.

Use one bounded runtime per PR; run memory-heavy apps sequentially within each child. Children own distinct worktrees, ports, cloned databases, tabs and result files. The coordinator gathers results and owns GitHub publication.

Choose the target's [project profile](project-profiles.md) before preparing a runtime. For JouleMV only, from the disposable worktree run this skill's bundled helper:

```sh
python3 "$SKILL_DIR/scripts/agent_run.py" doctor
python3 "$SKILL_DIR/scripts/agent_run.py" start --schema-mode current
```

Choose `current` only when the reviewed change adds no tenant SQL migration and the existing cloned schema can exercise its behavior. It clones local data unchanged and does not verify migration execution. For SQL migration changes, choose `--schema-mode migrate`; a migration failure remains a blocker unless the actual prerequisite can be corrected within the owned fixture scope. Do not bypass the migration being tested. The helper checks local source DB identity and ownership, disables external notification transports, uses private Vite caches, and records commit/schema mode in its receipts.

Read the command's current `--help`, JSON and logs paths rather than caching ports, JDK requirements or source database names. Require the returned commit to match the intended target, verify the checkout has no unexpected source edits, and save it as `live.runtime_commit` with `live.scope` and `live.environment`. For an integration commit, save its verified parents as `live.integration_parents`. Keep these fields distinct from the PR pins and any dependency repository revisions. HTTP responsiveness, including a legacy `/login` 401, is transport readiness only; real credential login and actual API data must succeed before any feature scenario passes. A missing dependency or server prerequisite is a concrete blocker, not a reason to claim a test succeeded.

For WebSynco React, follow the profile's paired frontend/backend runtime procedure and owned database clone. Record both revisions and the tenant/schema; the standard shared `wt` backend is not isolated test data. Other repositories need their own verified startup procedure. For backend-only changes without a UI, provide technical acceptance checks using the actual changed code and suitable local dependencies. For notification changes, a loopback SMTP collector can prove the message/query behavior without external delivery; identify fixture-schema, scheduler and delivery limits. Do not present mocked network data as live backend evidence.

## Browser and fixtures

All browser actions follow [T3 Code Navigator](live-testing.md#browser-and-fixtures) using built-in `preview_*` exclusively. Start with `preview_status`; reuse a suitable tab owned by the current agent or unclaimed, and call `preview_open` only when needed. Keep the returned `tabId` on every subsequent call. Never drive a human-owned tab or another agent's tab. Each concurrently testing child owns one tab and reuses it across scenarios and PRs where safe; when a new tab is needed, prevent reuse of another child's tab with `reuseExistingTab: false`. Prefer semantic locators from fresh snapshots.

Include this contract and the shared Navigator link in every launched or delegated brief that permits browser work. Coordinate sequential browser work through an agent with preview access when a child lacks it, or mark only the affected browser evidence Blocked. For missing preview tools or errors, correct actionable arguments and retry at most twice; if still unavailable or failing, record the exact blocker and continue independent authorized review work. Never fall back to Chrome, DevTools, CUA, standalone Playwright or another browser launch. This routing does not require browser work in static-only scopes or alter publication/approval boundaries.

Read development credentials privately from the target project's documented source or current user session and feed them directly to the login tools. Never print values, tokens, complete environment files or credential-bearing logs. Test permissions according to that project's policy, including any explicit frontend-gate deferral; backend authorization still requires real server evidence. Controlled client-state simulations may help diagnosis but do not prove backend authorization.

Prefer existing records, then disposable test records in the owned clone. Seed boundary data or change a test account's role only inside that clone with explicit database identity checks. Record the fixture and restoration. Missing original tickets, production accounts, language controls, root fixtures, or rich data remain visible coverage gaps. Distinguish seed/schema skew from defects in the reviewed change; compare canonical source definitions where IDs or types are uncertain.

## Evidence

For each scenario save its stable ID, preconditions, verified route, concrete actions, expected and observed result. Plan every distinct changed user flow and directly affected regression during static review. A known defect blocks dependent checks; run independent high-risk checks when useful and retain deferred scenarios as blocked with their reason. Continue useful independent checks after a failure; mark dependent checks blocked with a reason. An unavailable screenshot alone does not turn an independently observed behavior into a fabricated pass or failure.

Save `preview_snapshot({tabId,save:true})` for each visible observed outcome. Inspect the saved image for legibility **and agreement with the final rendered outcome**. A crisp screenshot of an earlier state is not proof. Wait for the relevant values, resize if useful, and retry capture at most twice; then retain page/API evidence and mark visual evidence unavailable. Save no blank frames as acceptance pictures.

Copy each downloaded export into the run evidence directory immediately, before another download can evict it. Verify its filename and relevant contents, such as rows, amounts, headers and click-time timestamp. Retain the file and verification description. Display precision and export precision are separate expectations unless the feature spec ties them together.

Use unique scenario IDs; distinguish variants and keep retried attempts under one final outcome. Keep convention observations, preexisting behavior, feature specification gaps and environmental blockers distinct from verified new defects.

## Cleanup and failure handling

After any start attempt, run the target profile's cleanup in a finally-style path. JouleMV uses the same helper's `stop` from the owned worktree; WebSynco uses its recorded owned backend/frontend processes, database and checkouts. Verify the owned database was dropped and server processes stopped; inspect `status` or ownership-checked process/database evidence if the receipt is ambiguous. An `already_stopped` result is sufficient only when owned resources are confirmed absent. Cleanup receipts pin the database endpoint and process ownership from startup. If ownership cannot be verified, retain the receipt and report cleanup pending; do not reconstruct ownership from a changed environment file. Legacy receipts without an endpoint require manual ownership verification before cleanup. Never stop another thread's runtime or drop a source/shared database.

Remove the detached worktree, including privately copied `.env` files, only after cleanup succeeds and evidence has been retained outside it. A copied helper or bounded test adapter may diagnose a startup incompatibility; record exactly what it changes. Prefer the bundled schema-mode/readiness controls over ad hoc patches, and keep PR source untouched.

Report failed cleanup explicitly and retain ownership metadata for a safe retry. A runtime watchdog is a backstop, not a cleanup receipt.
