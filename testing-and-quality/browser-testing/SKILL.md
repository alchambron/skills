---
name: browser-testing
description: Test screens through T3 Code's built-in Navigator browser. Defines browser ownership and failure handling for review workflows, plus JouleMV development login guidance.
---

# Browser Testing Skill

## T3 Code Navigator

For browser work launched from T3 Code, always use its built-in **Navigator** through the `preview_*` tools, including tools exposed with an MCP prefix. This applies to the coordinating agent and every delegated review or test agent. Browser selection does not add live testing to a static-only review or grant permission to start services or change data.

1. Call `preview_status` to inspect available tabs, profiles, and control ownership. Reuse a suitable tab owned by this agent or an unclaimed tab when safe. If none is automation-capable, call `preview_open` before declaring Navigator unavailable. Use a listed `profileId` when saved login state is needed.
2. Retain the returned `tabId` and pass it on subsequent calls. Inspect with `preview_snapshot`, then use `preview_navigate` and the focused interaction tools with fresh snapshot locators. Read a human-controlled or another agent's tab with snapshots only; open an owned tab to interact.
3. Reuse the owned tab across sequential scenarios and PRs when session isolation permits. Concurrent browser-testing children each create one owned tab with `preview_open({reuseExistingTab:false})` and keep its `tabId`; avoid opening a tab per step or scenario. Include this Navigator requirement and the shared skill path in every browser-capable child or launched-thread brief. If a child lacks preview access, return its scenarios to the coordinator for sequential execution in Navigator.
4. Save visible outcomes with `preview_snapshot({tabId,save:true})`, inspect the saved image, and retain its path with the scenario result. Distinguish observed browser behavior from static reasoning, API checks, and automated tests.
5. If a preview call fails, inspect its actual error, correct actionable arguments, and retry the step at most twice. If tools are absent, try the supported T3 MCP transport when available. If Navigator remains unavailable, mark affected browser scenarios **Blocked**, record the exact error and missing evidence, and continue independent authorized review work. Never mark unexecuted scenarios as passed.

**No external-browser fallback:** do not launch or attach to Google Chrome, Chrome MCP/DevTools, standalone Playwright/Chromium, agent-browser, or another desktop/browser automation surface for these review scenarios. Do not install or start an external browser to work around a preview failure. Keep browser work inside T3 Code even when a generic helper suggests a fallback. Repository test suites remain separate evidence; do not use a headed test runner as a substitute for Navigator. One exception: `review-all` runs its planned scenarios as scripts in its own headless runner (`review-all/scripts/scenario_run.py`), as its live-testing contract describes. That runner is the primary engine for those scenarios, never a fallback for a failed Navigator call.

## JouleMV credentials

For JouleMV only, development credentials are defined in the frontend `.env` file:

- **User**: `VITE_TEST_USERNAME`
- **Password**: `VITE_TEST_PASSWORD`

Read values privately for login; keep them out of tool output, reports, and screenshots. Other projects use their own documented credentials or current authorized session.

## Workflow

1. Use the review workflow's verified target revision and frontend URL. Start the app only when authorized, following its repository configuration or isolated review launcher.
2. In T3 Code, use Navigator as described above for interactive checks. For `review-all` scripted scenarios, use its bundled runner under the exception above. On other hosts, follow the host browser policy and the invoking workflow.
3. Log in with the test credentials above
4. Verify the screen renders correctly and interactions work as expected
