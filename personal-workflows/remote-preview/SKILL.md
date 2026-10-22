---
name: remote-preview
description: Launch a development server and provide a private Tailscale URL when the user asks to test a project from another device. Also inspect, restart, or stop a preview previously created by this skill.
---

# Remote Preview

Run the application in the current workspace and return a verified private URL the user can open from another Tailscale-connected device. The agent and server run on the host; the user's browser runs on their other device. Default to Tailscale Serve over HTTPS, with all setup performed on the host.

## Find the application

Read the workspace's agent instructions and documented development commands. Use existing project scripts, runtime versions, dependencies, environment setup, and fixture data. For a monorepo, identify the requested application before starting processes; ask only if multiple candidates remain plausible.

Resolve the command and its actual HTTP port from the project. Rails, React, and Rust servers need no special transport adapter: Serve proxies HTTP. A desktop-only application needs an existing browser-compatible mode; a Tauri frontend preview alone does not validate its native Rust integration. If no web interface exists, explain the limitation and ask before implementing one.

## Reuse or start the server

Keep preview state outside the repository in `~/.cache/remote-preview/<workspace-id>/`, where the ID is the first 16 hex characters of SHA-256 of the resolved absolute workspace path. Record the workspace, start command, actual local port, process identity, log path, public port, URL, and whether this skill owns the process and Serve mapping. Different worktrees have different IDs.

Inspect saved state and the port listener before reusing it. Verify its command and working directory belong to this workspace. For a new instance, choose an available local port supported by the project's launcher. An occupied port is not proof that this project is running.

Use a durable project runner, or start the command as a detached subprocess with its working directory set to the workspace, stdin disconnected, and stdout/stderr sent to the preview log. Python's `subprocess.Popen` with `start_new_session=True` and redirected streams is suitable. Record the process identity and verify that the server survives the launcher's exit. Keep existing project environment variables local to the process.

Prefer a loopback listener. Poll the actual application URL with bounded requests until its expected page or health endpoint responds; inspect the logs on failure. Allow for the project's startup time, but stop retrying when the process exits or a concrete configuration error appears. A socket accepting connections alone does not establish readiness.

## Publish a private URL

1. Run `tailscale status --json` and `tailscale serve status --json`. Require a running Tailscale backend and derive the host's full DNS name from `Self.DNSName`, removing its trailing dot.
2. Inspect existing Serve listeners and local listeners. Reuse this preview's saved HTTPS port only when its mapping still points to this workspace's verified local server. Otherwise choose a free public port starting at 8443. Reserve different ports for simultaneous previews. Preserve existing routes, including T3 routes; configure only the dedicated root handler on the selected port.
3. Run the following with the selected numeric ports:

   ```bash
   tailscale serve --bg --https=PUBLIC_PORT http://127.0.0.1:LOCAL_PORT
   ```

   Serve is private to the tailnet, subject to its access policy. Use Serve, not Funnel. If this HTTPS address/port already permits Funnel, choose a different port. Never reset the machine's whole Serve configuration.
4. If HTTPS/Serve needs one-time account setup, return the exact setup link or diagnostic Tailscale provides and the action the user must perform. Bound any command waiting for setup, retain only tracked preview resources, and report that publication is pending. For timed calls on macOS, use `/Applications/Tailscale.app/Contents/MacOS/Tailscale` when available: a shell wrapper may leave its child CLI waiting after the wrapper is terminated. Authentication belongs in the official Tailscale UI. Resume after the user completes setup; no project changes are needed for this account prerequisite.
5. Read Serve status again and verify the mapping. Request `https://HOST_DNS_NAME:PUBLIC_PORT/` with normal certificate validation and check the expected application response. If the host cannot test its own Tailscale endpoint, report the successful local and mapping checks separately and leave client reachability explicitly unverified. Do not claim an Air-side test from a host-side request.

For a changed browser origin, verify the project's allowed hosts, API requests, authentication redirects, and hot-reload WebSocket. Prefer the project's existing development proxy and relative API URLs. If separate API or WebSocket ports are needed, publish and configure them deliberately; `localhost` embedded in browser code refers to the user's device. Keep necessary development configuration changes scoped and report them.

## Finish, restart, or stop

Return the clickable private URL first, then the workspace/branch, running command, log location, and a concrete way to stop this preview. Leave a successfully requested preview available after the agent turn. Automatic restart after host reboot is a separate request: Serve's background persistence does not restart the application.

For `$remote-preview stop`, verify that the current dedicated mapping exactly matches recorded state before removing it with `tailscale serve --https=PUBLIC_PORT off`. Stop only an owned process whose current identity and working directory still match saved state, including its owned child processes. An independently running server may be reused but remains running when this skill's preview stops. Retain diagnostics if cleanup cannot be verified.

For `$remote-preview restart`, restart only the owned application process and recheck the existing mapping and application response. If the application is owned by another runner, use that runner's documented restart operation.

The user can invoke `$remote-preview` in each project or ask: “Lance ce projet et donne-moi son URL privée Tailscale.” For several applications, report a distinct URL per instance.

Reference for Serve syntax and one-time setup: https://tailscale.com/docs/features/tailscale-serve and https://tailscale.com/docs/reference/tailscale-cli/serve.
