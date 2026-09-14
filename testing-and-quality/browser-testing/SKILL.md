---
name: browser-testing
description: Use when testing screens in the browser using Chrome MCP. Covers how to connect to the frontend and test with development credentials.
---

# Browser Testing Skill

## How to Test

Use the **Chrome MCP** to test screens in the browser.

## Credentials

Development credentials are defined in the `.env` file:

- **User**: `VITE_TEST_USERNAME`
- **Password**: `VITE_TEST_PASSWORD`

## Workflow

1. Make sure the frontend dev server is running (`npm run dev`)
2. Use Chrome MCP to navigate to the target screen
3. Log in with the test credentials above
4. Verify the screen renders correctly and interactions work as expected
