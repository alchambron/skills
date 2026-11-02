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

## Unblock execution

Treat a startup or scenario failure as a decision point. Inspect the exact error and the target's documented prerequisites, then perform the necessary reversible remediation already authorized by the task: for example, install the pinned dependency with existing authorized registry access, create owned runtime directories, select a supported local toolchain, correct isolated runtime configuration, or seed disposable records in the ownership-checked test clone. Retry the affected check after a concrete change; preserve earlier attempts and stop repeating an unchanged failure. Keep the reviewed source, lockfile requirements, migration under test, shared services and source databases intact. An adapter or fixture that substitutes for behavior under test limits the evidence; it cannot establish that behavior passed.

Check documented local configuration and existing session authorization before asking again. If progress needs genuinely missing information, a secret, or permission beyond the granted scope, ask promptly with the specific dependency and affected checks. Use the host's secret-request tool for credentials, feeding its private result directly to the authorized consumer; never request secret values in chat. A pending request is not authorization. Continue independent scenarios, PRs and evidence work while waiting. If input remains unavailable, retain the affected coverage and the exact remaining user action rather than ending silently at a scenario plan.

Record each failure and remediation attempt in the affected scenario's structured `actual` or the shared runtime's `live.environment`, with evidence references: command/action and exact nonsecret error, attempted change and retry result, affected/unverified checks, remaining dependency and user action, and cleanup performed or pending. Use the existing ledger fields rather than inventing a pass to clear a blocker.

## Browser and fixtures

Use built-in T3 Code Navigator `preview_*` for browser work, following the current host instructions for available tools and permitted fallback. Start with `preview_status`; reuse a suitable tab owned by the current agent or unclaimed, and call `preview_open` only when needed. Keep the returned `tabId` on every subsequent call. Never drive a human-owned tab or another agent's tab. Each concurrently testing child owns one tab and reuses it across scenarios and PRs where safe; when a new tab is needed, prevent reuse of another child's tab with `reuseExistingTab: false`. Prefer semantic locators from fresh snapshots.

Include this contract in every launched or delegated brief that permits browser work. Coordinate sequential work through an agent with browser access when a child lacks it. Correct actionable arguments and follow the host's retry/fallback conditions; quote the exact error before an allowed fallback. A missing browser capability limits the affected browser checks: retain the blocker, request necessary user action promptly, and continue independent authorized work. Browser fallback grants no additional access, publication or approval permission. Static-only scopes do not require browser work.

Read development credentials privately from the target project's documented source or current user session and feed them directly to the login tools; if unavailable, use the secret-request procedure above. Never print values, tokens, complete environment files or credential-bearing logs. Test permissions according to that project's policy, including any explicit frontend-gate deferral; backend authorization still requires real server evidence. Controlled client-state simulations may help diagnosis but do not prove backend authorization.

Prefer existing records, then disposable test records in the owned clone. Seed boundary data or change a test account's role only inside that clone with explicit database identity checks. Record the fixture and restoration. Create necessary disposable fixtures when the authorized clone and canonical definitions support them. Missing original tickets, production accounts, language controls, root fixtures, or rich data remain visible coverage gaps after applicable setup attempts; request any necessary missing input promptly. Distinguish seed/schema skew from defects in the reviewed change; compare canonical source definitions where IDs or types are uncertain.

## Evidence

For each scenario save its stable ID, preconditions, verified route, concrete actions, expected and observed result. Plan every distinct changed user flow and directly affected regression during static review. A known defect blocks dependent checks; run independent high-risk checks when useful and retain deferred scenarios as blocked with their reason. Continue useful independent checks after a failure; mark dependent checks blocked with a reason. An unavailable screenshot alone does not turn an independently observed behavior into a fabricated pass or failure.

Save `preview_snapshot({tabId,save:true})` for each visible observed outcome, or the equivalent capture from a host-authorized fallback. Inspect the saved image for legibility **and agreement with the final rendered outcome**. A crisp screenshot of an earlier state is not proof. Wait for the relevant values, resize if useful, and retry capture at most twice; then retain page/API evidence and mark visual evidence unavailable. Save no blank frames as acceptance pictures.

Copy each downloaded export into the run evidence directory immediately, before another download can evict it. Verify its filename and relevant contents, such as rows, amounts, headers and click-time timestamp. Retain the file and verification description. Display precision and export precision are separate expectations unless the feature spec ties them together.

Use unique scenario IDs; distinguish variants and keep retried attempts under one final outcome. Mark a scenario `pass` only when its required behavior and variants were exercised with supporting evidence. Use `partial` for exercised subcases with unverified required steps, `blocked` for unexecuted checks with a concrete dependency, `observed` when behavior lacks an acceptance judgment, and `spec_gap` when a missing requirement prevents that judgment. A reproduced acceptance defect is `fail`, even when other checks remain. Keep convention observations, preexisting behavior and environmental blockers distinct from verified new defects. Report review outcomes and acceptance counts separately; completed static reviews never imply finished runtime coverage.

## Cleanup and failure handling

After any start attempt, run the target profile's cleanup in a finally-style path. JouleMV uses the same helper's `stop` from the owned worktree; WebSynco uses its recorded owned backend/frontend processes, database and checkouts. Verify the owned database was dropped and server processes stopped; inspect `status` or ownership-checked process/database evidence if the receipt is ambiguous. An `already_stopped` result is sufficient only when owned resources are confirmed absent. Cleanup receipts pin the database endpoint and process ownership from startup. If ownership cannot be verified, retain the receipt and report cleanup pending; do not reconstruct ownership from a changed environment file. Legacy receipts without an endpoint require manual ownership verification before cleanup. Never stop another thread's runtime or drop a source/shared database.

Remove the detached worktree, including privately copied `.env` files, only after cleanup succeeds and evidence has been retained outside it. A copied helper or bounded test adapter may diagnose a startup incompatibility; record exactly what it changes. Prefer the bundled schema-mode/readiness controls over ad hoc patches, and keep PR source untouched.

Report failed cleanup explicitly and retain ownership metadata for a safe retry. A runtime watchdog is a backstop, not a cleanup receipt.
