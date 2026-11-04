#!/usr/bin/env python3
"""Run scripted acceptance scenarios in an owned headless Chromium.

Commands:
  setup   install the pinned Playwright and its headless Chromium shell into the tool directory
  run     run one scenario script and print a compact JSON summary; the full result is OUT/result.json

  run --script S.mjs --out DIR [--runtime RUNTIME.json | --base-url URL] [--param KEY=VALUE ...]
      [--params FILE.json] [--context FILE.json] [--timeout SECONDS] [--action-timeout SECONDS] [--headed]
      [--save-session FILE]

`--runtime` reads `frontendUrl`, `storageState` and `params` from the runtime child's runtime.json.
`--save-session` writes the browser session (cookies, local storage) to FILE with mode 0600 after a passing run,
so a login script run once by the runtime child gives every scenario the same session.
Run exit codes: 0 pass, 1 a check failed, 2 the script stopped, the runtime is unreachable or setup failed.
Tools live in $REVIEW_TOOLS_DIR (default ~/.codex/review-state/review-all/tools/playwright); `run` installs them
on first use. The script contract is documented at the top of scenario_runner.mjs.
"""

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

PLAYWRIGHT_VERSION = "1.60.0"
RUNNER = Path(__file__).resolve().with_name("scenario_runner.mjs")
EXIT = {"pass": 0, "fail": 1, "error": 2}


class SetupError(RuntimeError):
    pass


def tool_dir():
    default = Path.home() / ".codex" / "review-state" / "review-all" / "tools" / "playwright"
    return Path(os.environ.get("REVIEW_TOOLS_DIR") or default)


def tool_env(directory):
    return {**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(directory / "browsers")}


def installed(directory):
    try:
        package = json.loads((directory / "node_modules" / "playwright" / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return package.get("version") == PLAYWRIGHT_VERSION and any((directory / "browsers").glob("chromium_headless_shell-*"))


def quiet(command, directory):
    completed = subprocess.run(command, cwd=directory, env=tool_env(directory), capture_output=True, text=True)
    if completed.returncode:
        tail = (completed.stderr or completed.stdout).strip().splitlines()[-5:]
        raise SetupError(f"{' '.join(command[:3])} failed: {' | '.join(tail)}")


def setup(directory=None):
    directory = directory or tool_dir()
    directory.mkdir(parents=True, exist_ok=True)
    with open(directory / ".setup.lock", "a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        changed = not installed(directory)
        if changed:
            if not shutil.which("node") or not shutil.which("npm"):
                raise SetupError("node and npm are required on PATH")
            (directory / "package.json").write_text(json.dumps(
                {"private": True, "dependencies": {"playwright": PLAYWRIGHT_VERSION}}, indent=1) + "\n", encoding="utf-8")
            quiet(["npm", "install", "--no-audit", "--no-fund", "--loglevel=error"], directory)
            quiet(["node", str(directory / "node_modules" / "playwright" / "cli.js"), "install", "--only-shell", "chromium"], directory)
            if not installed(directory):
                raise SetupError("Playwright installed but its headless Chromium shell is missing")
    return {"toolDir": str(directory), "playwright": PLAYWRIGHT_VERSION, "changed": changed}


def reachable(url, timeout=5):
    try:
        with urlopen(url, timeout=timeout):
            return True
    except HTTPError:
        return True  # The server answered.
    except (URLError, OSError, ValueError):
        return False


def parse_params(pairs):
    params = {}
    for pair in pairs:
        key, separator, value = pair.partition("=")
        if not separator or not key:
            raise ValueError(f"--param expects KEY=VALUE, got {pair!r}")
        params[key] = value
    return params


def summarize(result, result_path):
    checks = result.get("checks", [])
    failed_step = next((step["name"] for step in result.get("steps", []) if not step.get("ok")), None)
    return {
        "status": result.get("status"), "checks": f"{sum(bool(c.get('ok')) for c in checks)}/{len(checks)}",
        "failedChecks": [c["name"] for c in checks if not c.get("ok")][:10], "failedStep": failed_step,
        "error": result.get("error"), "screenshots": result.get("screenshots", []),
        "failureEvidence": [e.get("aria") or e.get("screenshot") for e in result.get("failureEvidence", [])],
        "consoleErrors": len(result.get("consoleErrors", [])), "failedRequests": len(result.get("failedRequests", [])),
        "trace": result.get("trace"), "savedSession": result.get("savedSession"), "result": str(result_path),
        "durationMs": result.get("durationMs"),
    }


def error_summary(message, out):
    return {"status": "error", "error": message, "result": str(Path(out) / "result.json")}


def run(args):
    out = Path(args.out).resolve()
    script = Path(args.script).resolve()
    if not script.is_file():
        return error_summary(f"Scenario script not found: {script}", out)
    base_url, storage_state, params = args.base_url, None, {}
    if args.runtime:
        runtime = json.loads(Path(args.runtime).read_text(encoding="utf-8"))
        base_url = base_url or runtime.get("frontendUrl")
        # A login run creates the session, so it starts without one.
        storage_state = None if args.save_session else runtime.get("storageState")
        params.update(runtime.get("params") or {})
    if args.params:
        params.update(json.loads(Path(args.params).read_text(encoding="utf-8")))
    params.update(parse_params(args.param))
    if base_url and not reachable(base_url):
        return error_summary(f"Runtime unreachable at {base_url}; report it to the coordinator instead of starting one", out)
    if storage_state and not Path(storage_state).is_file():
        return error_summary(f"Saved session missing: {storage_state}", out)
    directory = tool_dir()
    try:
        setup(directory)
    except (SetupError, OSError) as exc:
        return error_summary(f"Scenario tools unavailable: {exc}", out)
    options = {"script": str(script), "out": str(out), "baseUrl": base_url, "storageState": storage_state,
               "params": params, "toolDir": str(directory), "headed": args.headed,
               "saveSession": str(Path(args.save_session).resolve()) if args.save_session else None,
               "timeoutMs": int(args.timeout * 1000), "actionTimeoutMs": int(args.action_timeout * 1000),
               "context": json.loads(Path(args.context).read_text(encoding="utf-8")) if args.context else {}}
    result_path = out / "result.json"
    result_path.unlink(missing_ok=True)
    try:
        completed = subprocess.run(["node", str(RUNNER), json.dumps(options)], env=tool_env(directory),
                                   capture_output=True, text=True, timeout=args.timeout + 90)
    except subprocess.TimeoutExpired:
        return error_summary("Runner did not exit after the scenario timeout", out)
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        tail = " | ".join((completed.stderr or completed.stdout).strip().splitlines()[-5:])
        return error_summary(f"Runner wrote no result (exit {completed.returncode}): {tail}", out)
    return summarize(result, result_path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("setup")
    command = commands.add_parser("run")
    command.add_argument("--script", required=True)
    command.add_argument("--out", required=True)
    command.add_argument("--runtime")
    command.add_argument("--base-url")
    command.add_argument("--param", action="append", default=[])
    command.add_argument("--params")
    command.add_argument("--context", help="JSON file of extra Playwright context options (locale, timezoneId, viewport)")
    command.add_argument("--timeout", type=float, default=300.0, help="Whole-scenario limit in seconds")
    command.add_argument("--action-timeout", type=float, default=10.0, help="Per-action limit in seconds")
    command.add_argument("--headed", action="store_true")
    command.add_argument("--save-session", help="Save the session to this file (mode 0600) when the run passes")
    args = parser.parse_args(argv)
    try:
        if args.command == "setup":
            print(json.dumps(setup()))
            return 0
        summary = run(args)
    except (SetupError, OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    print(json.dumps(summary))
    return EXIT.get(summary.get("status"), 2)


if __name__ == "__main__":
    sys.exit(main())
