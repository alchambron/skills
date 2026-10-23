---
name: joulemv-sentry-prod-events
description: Show the latest five individual Sentry error events for JouleMV backend and frontend production, excluding expired JWT errors, in linked tables with Toronto timestamps.
---

# JouleMV production Sentry events

Use this workflow for the user's production error report. Default projects are `joulemv-backend-prod` and `joulemv-frontend-prod` in organization `enerzam-0h`, environment `production`. Test projects are outside this report unless requested.

## Fetch

Read the installed Sentry skill for credential and redaction guidance, then run:

```bash
python3 /Users/alchambron/.codex/skills/joulemv-sentry-prod-events/scripts/latest_events.py
```

The helper calls the installed read-only Sentry plugin through `/Users/alchambron/.config/sentry/codex-sentry.py`. That wrapper privately loads the shared credentials. Use this setup without asking for credential setup again; report an actual access failure if it occurs. Never expose credentials.

The helper queries live data, excludes `ExpiredJwtException`, gathers individual events across recent issues, and sorts each project's events newest first. Repeated events from one issue count separately. It preserves network failures, 401s, and database failures unless evidence identifies them as expired JWTs. In particular, a JDBC error inside `isTokenExpired` is a database error and remains included.

"Remove JWT errors" means filter the report. This workflow does not delete, resolve, suppress, or change Sentry issues or application code.

## Present

Provide one Markdown table per production project, with these columns:

| Date / time | Endpoint or task | Error | Release | Event |
|---|---|---|---|---|

Use the returned Toronto timestamps and identify the year and timezone. Summarize errors in plain language, shorten event IDs to eight characters as link labels, and link each individual event using its returned URL. Show five rows per project when available. If fewer are returned, explicitly state how many were available; surface any completeness warning rather than claiming a complete latest-five report.

State briefly that expired JWTs were excluded from the report. Do not infer network errors are caused by expired sessions. Keep investigation and proposed fixes outside the default report unless requested.
