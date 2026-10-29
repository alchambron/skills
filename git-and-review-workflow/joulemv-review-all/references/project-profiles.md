# Project profiles

Select a profile from the PR's exact repository identity, then verify the target checkout's instructions and layout. The collector and renderer share `scripts/project_profiles.py`; its labels organize the batch, not permission to reuse a runtime. Unknown repositories and forks use repository-specific discovery rather than automatically borrowing a similarly named project's setup.

Every subagent brief carries its repository, project profile, pinned base/head, applicable instruction paths and requirements sources. Briefs permitting browser work also carry the [T3 Code Navigator](live-testing.md#browser-and-fixtures) contract in [live-testing.md](live-testing.md#browser-and-fixtures). Runtime receipts and evidence stay keyed by repository and PR. A sibling repository used for integration is a dependency, not an additional review target unless it was selected in the batch.

## JouleMV (`EnerZam/JouleMV`)

Use `joulemv-code-review` and its Standards/Spec dependencies. Load the root and `frontend/` agent guidance and the frontend's local skill index. Use the bundled JouleMV-only `scripts/agent_run.py` as described in [live-testing.md](live-testing.md). Its database layout, credentials, SQL handling and tool requirements belong to this profile.

## WebSynco React frontend (`EnerZam/WebSynco_React_Front`)

### Review standards and spec

The frontend lives at the repository root. Read `AGENTS.md` → `AGENT.md`, then `.claude/skills/INDEX.md` and the relevant local skills. Use the generic `code-review` axes with these standards. Load local `testing`/`verification` for tests and the actual package/lockfile for dependencies. JouleMV's screen, error-handling, locale and modal conventions do not define this project's contract.

In particular, inspect the target revision's `docs/adr/0001-defer-frontend-tenant-guards-and-permission-gates.md` when applicable. Follow its frontend-gate deferral while continuing to review backend authorization, login/session behavior and tenant isolation. Translation and shared `@enerzam/ui` usage follow the WebSynco skills and locked declarations.

Resolve requirements through the repository's documented issue tracker and linked tickets. If unavailable, use the selected PR's description and human discussion as the bounded spec; record the original-spec gap. Use `WEBSYNCO_LEGACY_FRONTEND_PATH` only as a source-discovery hint and record the inspected backend revision for legacy behavior claims.

### Paired runtime

Read the pinned frontend's `scripts/devstack/README.md`, package scripts, Vite proxy configuration and authentication guidance. Discover the backend through the documented local path settings or sibling fallback; its own `AGENTS.md` and `websynco/docs/react-integration.md` define backend setup. Read only required settings privately.

For each PR:

1. Create a detached frontend worktree at the reviewed head. Use its lockfile and Node requirement; install with its documented peer-dependency flags and authorized GitHub Packages access. Missing package access is a startup blocker, not grounds to substitute a UI-library version.
2. Pin a compatible WebSynco backend revision in a separate owned checkout. Confirm that it implements the frontend's actual authentication/API contract before choosing it. An old HTML login page returning HTTP 200 does not establish cookie-API compatibility. Record both repository identities and SHAs; keep the user's existing backend branch and services intact.
3. Restore an authorized local database or backup into a fresh owned database. Include required common tenant metadata and the selected tenant schema, and verify fixed-tenant/hostname configuration. Retain source and owned-clone identities without secrets. The development `wt` setup shares its backend/database; ordinary `wt up` is not an isolated acceptance environment.
4. Create owned configuration, secrets, upload, temporary and Tomcat/log folders outside tracked source. Reuse documented local-development settings while replacing every writable filesystem path, including `WEBSYNCO_UPLOADED_FILES_FOLDER` and `WEBSYNCO_TOMCAT_BASE_FOLDER`, with an owned location. Resolve and record those paths before startup; avoid writable links to shared storage. Copy required file fixtures into owned storage so database-generated attachment IDs cannot overwrite the user's files. Set JDBC to the owned database clone and tenant selection to the test tenant, and direct notifications to local collectors. Check actual startup tasks, garbage collection and external integrations. Uploaded files and external configuration are separate from a database backup; missing files block their scenarios, not unrelated flows.
5. Inspect the pinned backend POM before building or starting it. Normal Maven lifecycle commands can execute SQL. For changes without migrations, use the verified migration-disabled profile; for migration changes, apply the reviewed migration to the owned clone and verify the result. `--skip-sql` or a property name alone is not proof the installed plugin honors it. A profile that skips tests supplies build evidence only. Confirm the database schema and runtime requirements against this backend pin.
6. Assign unused frontend/backend ports and bind owned services to loopback. Launch a durable backend process with the private environment and verified Maven profile. Launch the frontend from its root with its Vite proxy pointing at that backend and a strict, unique port. Prefer the repository's launcher only when it can use the owned runtime without starting or modifying shared services; otherwise the documented `npm run dev -- --host 127.0.0.1 --port <frontend-port> --strictPort` is a frontend launch pattern to verify against the pinned scripts. Retain PID/process identity, URLs and logs for cleanup.
7. Preserve same-origin `/WebSynco` routing, tenant Host, cookies and Fetch Metadata. Use the backend's supported development cookie settings for local HTTP. Read the test credentials privately from a documented source or the current user session; use real cookie login and verify `api/getParameters` returns JSON with the intended user, tenant and zone before feature scenarios. Keep credentials and cookie/token values out of reports and browser storage.

Record the frontend/backend SHAs, URLs, tenant, schema mode/version, owned database/process identities and resolved filesystem ownership in `runtime_provenance` using the [results contract](results-format.md). Frontend-head-only evidence uses the exact frontend PR head with the recorded backend dependency pin. A selected backend PR requires its own review and evidence; it is not silently substituted for the dependency of every frontend PR. Recheck both runtime pins before reporting results.

Execute the normal feature scenarios exclusively in [T3 Code Navigator](live-testing.md#browser-and-fixtures), following the shared [browser contract](live-testing.md#browser-and-fixtures) for tab reuse, ownership and browser-only blockers. Fixture-backed Vitest and Storybook checks supplement live evidence; inspect the local testing skill rather than requiring a Playwright E2E suite that this checkout may not configure.

### Cleanup

Stop only the recorded owned processes, remove only the owned database after verifying its identity, and remove owned filesystem roots and detached checkouts after retaining evidence. Confirm each resolved filesystem root belongs to the run before removing it. `wt down --all` would stop shared services and is outside this cleanup. If preparation failed partway, retain the resource manifest and report each cleanup outcome explicitly. Continue other projects when a WebSynco prerequisite is blocked.

## WebSynco backend (`EnerZam/WebSynco`)

Read its root `AGENTS.md` and relevant integration guide. Select the actual Maven application (`websynco/` or `websynco-request-module/`); they have different runtime contracts. Use generic Standards/Spec review and focused technical acceptance for backend-only behavior. For main-application React flows, use the paired-runtime procedure above with the backend at its reviewed head and a recorded compatible frontend pin. Preserve backend tenant/transaction/security rules. Do not apply the main application's startup or tenant assumptions to the request module without checking its source.

## Other repositories

Read their own instructions, issue-tracker configuration and startup/test documentation. Keep each exact repository visible under the repository-specific report group. Reuse the shared pinning, publication, evidence and cleanup workflow; discover runtime requirements separately. Shared ownership, naming or an `@enerzam/ui` dependency does not imply JouleMV/WebSynco database or authentication compatibility.
