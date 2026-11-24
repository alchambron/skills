#!/usr/bin/env python3
"""Scientific, zero-estimation PR performance & structure analyzer (v2.0.0).

Never fabricates runtime performance percentages from static code heuristics.
Every performance number comes from real executed tools with interleaved A/B
sampling and Mann-Whitney U significance testing:

  1. bundle_diff.mjs  - Exact Vite production bundle diff (raw/gzip/brotli) +
                        zero-jitter interleaved localhost Playwright benchmark
                        (V8 ScriptDuration, TaskDuration, JSHeap, FCP, TBT)
  2. browser_bench.mjs- Interleaved live Playwright Web Vitals & API benchmark
  3. api_sql_probe.py - Interleaved live HTTP API latency & PostgreSQL
                        EXPLAIN (ANALYZE, BUFFERS) rolled-back query benchmark
  4. test_bench.py    - Optional interleaved Vitest execution benchmark
  5. code_metrics.py  - Deterministic code structure metrics (/10 score only)

Uses Python 3.9+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

import api_sql_probe  # noqa: E402
import code_metrics  # noqa: E402
import skill_versions  # noqa: E402
import unit_bench  # noqa: E402


def run_cmd(cmd: List[str], cwd: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check, timeout=timeout)


def resolve_repo_root(repo: str) -> str:
    return run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo).stdout.strip()


def fetch_pr_metadata(repo_root: str, target: Optional[str]) -> Dict[str, Any]:
    fields = "number,url,title,body,baseRefName,headRefName,headRefOid,files,comments"
    cmd = ["gh", "pr", "view"]
    if target:
        cmd.append(str(target))
    cmd.extend(["--json", fields])
    return json.loads(run_cmd(cmd, cwd=repo_root).stdout)


def _git_object_exists(repo_root: str, ref: str) -> bool:
    return run_cmd(["git", "cat-file", "-e", f"{ref}^{{commit}}"], cwd=repo_root, check=False).returncode == 0


def ensure_refs_available(repo_root: str, pr_number: int, base_branch: str, head_oid: str) -> Tuple[str, str]:
    base_local_ref = f"refs/remotes/origin/{base_branch}"
    run_cmd(["git", "fetch", "--no-tags", "origin", f"refs/heads/{base_branch}:{base_local_ref}"], cwd=repo_root, check=False)
    head_local_ref = f"refs/remotes/pr-perf/pr-{pr_number}"
    fetch_head = run_cmd(
        ["git", "fetch", "--no-tags", "origin", f"refs/pull/{pr_number}/head:{head_local_ref}"],
        cwd=repo_root,
        check=False,
    )
    resolved_head = head_oid if _git_object_exists(repo_root, head_oid) else (head_local_ref if fetch_head.returncode == 0 else "HEAD")
    merge_base = run_cmd(["git", "merge-base", base_local_ref, resolved_head], cwd=repo_root, check=False)
    resolved_base = merge_base.stdout.strip() if merge_base.returncode == 0 and merge_base.stdout.strip() else base_local_ref
    return resolved_base, resolved_head


def discover_preview_urls(
    comments: List[Dict[str, Any]],
    host_regex: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    pattern_text = host_regex or os.environ.get("PR_PERF_PREVIEW_HOST_REGEX")
    if not pattern_text:
        return None, None
    rx = re.compile(pattern_text, re.IGNORECASE)
    for comment in reversed(comments or []):
        body = comment.get("body") or ""
        m = rx.search(body)
        if m:
            web_url = m.group(0).rstrip("/")
            api_template = os.environ.get("PR_PERF_PREVIEW_API_TEMPLATE")
            api_url = api_template.format(slug=m.group(1)) if (api_template and m.lastindex) else None
            return web_url, api_url
    return None, None


def is_url_awake(url: str, timeout_s: float = 3.0) -> bool:
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "pr-perf-impact/2.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403)
    except Exception:  # noqa: BLE001
        return False


def run_node_tool(script_name: str, extra_args: List[str], repo_root: str, timeout: int = 300) -> Dict[str, Any]:
    script_path = str(SCRIPT_DIR / script_name)
    try:
        res = run_cmd(["node", script_path, *extra_args], cwd=repo_root, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"status": "skipped", "reason": f"{script_name} timed out after {timeout}s"}
    except FileNotFoundError:
        return {"status": "skipped", "reason": "node executable not found"}

    stdout = (res.stdout or "").strip()
    if stdout:
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            pass
    return {"status": "skipped", "reason": (res.stderr or stdout or f"exit {res.returncode}")[:300]}


def build_scientific_verdict(
    code: Dict[str, Any],
    bundle: Dict[str, Any],
    live_browser: Dict[str, Any],
    api_bench: Dict[str, Any],
    sql_explain: Dict[str, Any],
    unit_bench: Dict[str, Any],
) -> Dict[str, Any]:
    """Summarize strictly measured results with zero heuristic % fabrication."""
    if code.get("prod_files_count", 0) == 0:
        return {
            "status": "no_runtime_files",
            "headline": "0% (no production runtime files changed)",
            "measured_axes": [],
            "significant_changes": [],
            "unmeasured_reason": None,
        }

    measured_axes: List[str] = []
    significant_changes: List[Dict[str, Any]] = []
    neutral_findings: List[str] = []

    # 1. Exact deterministic bundle measurement.
    if bundle.get("status") == "ok":
        measured_axes.append("bundle")
        entry = bundle["entry_gzip_kb"]
        red = float(entry["reduction_pct"])
        if abs(red) >= 0.5:
            significant_changes.append(
                {
                    "axis": "bundle_entry_gzip",
                    "improvement_pct": red,
                    "detail": f"Entry gzip {entry['base']}→{entry['head']} KB ({entry['delta_kb']:+.2f} KB, {red:+.2f}% smaller)",
                    "p_value": 0.0,
                }
            )
        else:
            neutral_findings.append(
                f"Entry gzip flat: {entry['base']}→{entry['head']} KB ({entry['delta_kb']:+.2f} KB / {entry['delta_pct']:+.2f}%)"
            )

        # 1b. Zero-jitter localhost Playwright runtime measurements.
        lb = bundle.get("local_browser") or {}
        if lb.get("status") == "ok":
            measured_axes.append("local_browser")
            for key, label in (
                ("script_duration_ms", "Local V8 JS CPU"),
                ("task_duration_ms", "Local main-thread CPU"),
                ("js_heap_mb", "Local JS heap"),
                ("fcp_ms", "Local FCP"),
                ("tbt_ms", "Local TBT"),
            ):
                m = (lb.get("metrics") or {}).get(key)
                if not m:
                    continue
                if m.get("significant"):
                    significant_changes.append(
                        {
                            "axis": f"local_browser.{key}",
                            "improvement_pct": m["improvement_pct"],
                            "p_value": m["p_value"],
                            "detail": (
                                f"{label} {m['base']['median']}→{m['pr']['median']} "
                                f"({m['improvement_pct']:+.2f}%, p={m['p_value']}, n={m['base']['n']})"
                            ),
                        }
                    )
                elif m.get("base", {}).get("median") is not None and m.get("pr", {}).get("median") is not None:
                    neutral_findings.append(
                        f"{label} within noise: {m['base']['median']}→{m['pr']['median']} "
                        f"({m['improvement_pct']:+.2f}%, p={m['p_value']}, n={m['base']['n']})"
                    )

    # 2. Live remote Playwright benchmark.
    if live_browser.get("status") == "ok":
        measured_axes.append("live_browser")
        for route, rdata in (live_browser.get("routes") or {}).items():
            for key, label in (("lcp_ms", "Live LCP"), ("fcp_ms", "Live FCP"), ("api_median_ms", "Live API"), ("script_duration_ms", "Live JS CPU")):
                m = (rdata.get("metrics") or {}).get(key)
                if not m or m.get("base", {}).get("median") is None:
                    continue
                if m.get("significant"):
                    significant_changes.append(
                        {
                            "axis": f"live_browser[{route}].{key}",
                            "improvement_pct": m["improvement_pct"],
                            "p_value": m["p_value"],
                            "detail": f"{label} ({route}) {m['base']['median']}→{m['pr']['median']} ({m['improvement_pct']:+.2f}%, p={m['p_value']})",
                        }
                    )

    # 3. Live HTTP API latency benchmark.
    if api_bench.get("status") == "ok":
        measured_axes.append("live_api")
        for ep in api_bench.get("endpoints", []):
            if ep.get("status") != "ok":
                continue
            m = ep["latency_ms"]
            if m.get("significant"):
                significant_changes.append(
                    {
                        "axis": f"live_api[{ep['endpoint']}]",
                        "improvement_pct": m["improvement_pct"],
                        "p_value": m["p_value"],
                        "detail": f"API {ep['endpoint']} {m['base']['median']}→{m['pr']['median']} ms ({m['improvement_pct']:+.2f}%, p={m['p_value']})",
                    }
                )
            else:
                neutral_findings.append(
                    f"API {ep['endpoint']} within noise: {m['base']['median']}→{m['pr']['median']} ms ({m['improvement_pct']:+.2f}%, p={m['p_value']})"
                )

    # 4. PostgreSQL EXPLAIN (ANALYZE, BUFFERS).
    if sql_explain.get("status") == "ok":
        measured_axes.append("sql_explain")
        m = sql_explain["execution_ms"]
        if m.get("significant"):
            significant_changes.append(
                {
                    "axis": "sql_explain.execution_ms",
                    "improvement_pct": m["improvement_pct"],
                    "p_value": m["p_value"],
                    "detail": f"SQL execution {m['base']['median']}→{m['pr']['median']} ms ({m['improvement_pct']:+.2f}%, p={m['p_value']})",
                }
            )
        else:
            neutral_findings.append(
                f"SQL execution within noise: {m['base']['median']}→{m['pr']['median']} ms ({m['improvement_pct']:+.2f}%, p={m['p_value']})"
            )

    # 5. Interleaved unit-test execution benchmark.
    if unit_bench.get("status") == "ok":
        measured_axes.append("unit_test_bench")
        m = unit_bench["duration_ms"]
        if m.get("significant"):
            significant_changes.append(
                {
                    "axis": "unit_test_bench",
                    "improvement_pct": m["improvement_pct"],
                    "p_value": m["p_value"],
                    "detail": f"Shared unit tests {m['base']['median']}→{m['pr']['median']} ms ({m['improvement_pct']:+.2f}%, p={m['p_value']})",
                }
            )
        else:
            neutral_findings.append(
                f"Shared unit tests within noise: {m['base']['median']}→{m['pr']['median']} ms ({m['improvement_pct']:+.2f}%, p={m['p_value']})"
            )

    if not measured_axes:
        return {
            "status": "not_measured",
            "headline": "Not measured (no runtime benchmark ran)",
            "measured_axes": [],
            "significant_changes": [],
            "neutral_findings": [],
            "unmeasured_reason": "No executable frontend bundle, live preview/API URL, PostgreSQL DSN, or shared unit-test benchmark was available for this PR.",
        }

    if not significant_changes:
        return {
            "status": "no_significant_change",
            "headline": f"No significant change (statistically flat across {', '.join(measured_axes)})",
            "measured_axes": measured_axes,
            "significant_changes": [],
            "neutral_findings": neutral_findings,
            "unmeasured_reason": None,
        }

    imps = [c for c in significant_changes if c["improvement_pct"] > 0]
    regs = [c for c in significant_changes if c["improvement_pct"] < 0]
    if imps and not regs:
        best = max(imps, key=lambda c: c["improvement_pct"])
        headline = f"Measured improvement: up to {best['improvement_pct']:+.2f}% ({best['axis']})"
        status = "measured_improvement"
    elif regs and not imps:
        worst = min(regs, key=lambda c: c["improvement_pct"])
        headline = f"Measured regression: {worst['improvement_pct']:+.2f}% ({worst['axis']})"
        status = "measured_regression"
    else:
        headline = f"Mixed measured impact ({len(imps)} improved, {len(regs)} regressed)"
        status = "measured_mixed"

    return {
        "status": status,
        "headline": headline,
        "measured_axes": measured_axes,
        "significant_changes": significant_changes,
        "neutral_findings": neutral_findings,
        "unmeasured_reason": None,
    }


def _load_sql_arg(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    return Path(val[1:]).read_text(encoding="utf-8") if val.startswith("@") else val


def main() -> int:
    parser = argparse.ArgumentParser(description="Run strictly measured, zero-estimation PR performance benchmarks.")
    parser.add_argument("target", nargs="?", help="PR number or URL (defaults to current branch PR)")
    parser.add_argument("--repo", default=".", help="Repository path")
    parser.add_argument("--no-bundle", action="store_true", help="Skip Vite production bundle + localhost browser bench")
    parser.add_argument("--no-local-browser", action="store_true", help="Run Vite bundle diff without localhost Playwright")
    parser.add_argument("--no-browser", action="store_true", help="Skip live remote Playwright benchmark")
    parser.add_argument("--no-api", action="store_true", help="Skip live HTTP API latency probe")
    parser.add_argument("--bench-tests", action="store_true", help="Run interleaved Vitest timing on shared modified tests")
    parser.add_argument("--base-url", help="Baseline frontend URL (or set PR_PERF_BASE_WEB_URL)")
    parser.add_argument("--pr-url", help="PR preview frontend URL")
    parser.add_argument("--base-api", help="Baseline API URL (or set PR_PERF_BASE_API_URL)")
    parser.add_argument("--pr-api", help="PR preview API URL")
    parser.add_argument("--preview-host-regex", help="Regex to extract preview URL from PR comments")
    parser.add_argument("--pg-dsn", default=os.environ.get("PR_PERF_PG_DSN"), help="PostgreSQL DSN for EXPLAIN ANALYZE")
    parser.add_argument("--sql-base", help="Baseline SQL query (or @file) for EXPLAIN ANALYZE")
    parser.add_argument("--sql-pr", help="PR SQL query (or @file) for EXPLAIN ANALYZE")
    parser.add_argument("--routes", default="/", help="Comma-separated frontend routes for live Playwright benchmark")
    parser.add_argument("--endpoints", default="/actuator/health", help="Comma-separated API endpoints to probe")
    parser.add_argument("--runs", type=int, default=7, help="Measured interleaved A/B pairs (default: 7)")
    parser.add_argument("--login", action="store_true", help="Attempt login with .env test credentials in live browser bench")
    parser.add_argument("--check-updates", action="store_true", help="Check published skill-versions.json for newer releases")
    parser.add_argument("--json", action="store_true", help="Output full structured JSON")
    args = parser.parse_args()

    try:
        installed_version = skill_versions.read_version(SKILL_DIR)
    except (OSError, ValueError):
        installed_version = None
    update_info = skill_versions.check_update(SKILL_DIR) if args.check_updates else None

    repo_root = resolve_repo_root(args.repo)
    pr = fetch_pr_metadata(repo_root, args.target)
    pr_number = int(pr["number"])
    temp_head_ref = f"refs/remotes/pr-perf/pr-{pr_number}"
    base_ref, head_ref = ensure_refs_available(repo_root, pr_number, pr["baseRefName"], pr["headRefOid"])

    try:
        code_res = code_metrics.analyze_diff(repo_root, base_ref, head_ref)

        if args.no_bundle:
            bundle_res: Dict[str, Any] = {"status": "skipped", "reason": "Disabled via --no-bundle"}
        elif code_res.get("frontend_prod_files", 0) == 0:
            bundle_res = {"status": "skipped", "reason": "No frontend production files changed"}
        else:
            b_args = ["--repo", repo_root, "--base", base_ref, "--head", head_ref, "--runs", str(args.runs)]
            if args.no_local_browser:
                b_args.append("--no-local-browser")
            bundle_res = run_node_tool("bundle_diff.mjs", b_args, repo_root, timeout=360)

        if args.bench_tests:
            unit_res = unit_bench.benchmark_vitest(
                repo_root, base_ref, head_ref, code_res.get("changed_test_files", []), runs=max(3, min(args.runs, 7))
            )
        else:
            unit_res = {"status": "skipped", "reason": "Opt-in via --bench-tests"}
    finally:
        run_cmd(["git", "update-ref", "-d", temp_head_ref], cwd=repo_root, check=False)

    auto_web, auto_api = discover_preview_urls(pr.get("comments", []), args.preview_host_regex)
    pr_web_url = args.pr_url or auto_web
    base_web_url = args.base_url or os.environ.get("PR_PERF_BASE_WEB_URL")
    pr_api_url = args.pr_api or auto_api
    base_api_url = args.base_api or os.environ.get("PR_PERF_BASE_API_URL")

    if args.no_browser:
        live_browser_res: Dict[str, Any] = {"status": "skipped", "reason": "Disabled via --no-browser"}
    elif not pr_web_url or not base_web_url:
        live_browser_res = {"status": "skipped", "reason": "No live --base-url and --pr-url provided"}
    elif not is_url_awake(pr_web_url):
        live_browser_res = {"status": "skipped", "reason": f"Preview host not awake ({pr_web_url})"}
    else:
        lb_args = [
            "--repo", repo_root,
            "--base-url", base_web_url,
            "--pr-url", pr_web_url,
            "--routes", args.routes,
            "--runs", str(args.runs),
        ]
        if args.login:
            lb_args.append("--login")
        live_browser_res = run_node_tool("browser_bench.mjs", lb_args, repo_root, timeout=360)

    if args.no_api:
        api_res: Dict[str, Any] = {"status": "skipped", "reason": "Disabled via --no-api"}
    elif not pr_api_url or not base_api_url:
        api_res = {"status": "skipped", "reason": "No live --base-api and --pr-api provided"}
    elif not is_url_awake(pr_api_url):
        api_res = {"status": "skipped", "reason": f"Preview API not awake ({pr_api_url})"}
    else:
        eps = [e.strip() for e in args.endpoints.split(",") if e.strip()]
        api_res = api_sql_probe.benchmark_api(base_api_url, pr_api_url, eps, runs=max(5, args.runs))

    sql_explain_res = api_sql_probe.benchmark_sql_explain(
        args.pg_dsn, _load_sql_arg(args.sql_base), _load_sql_arg(args.sql_pr), runs=max(5, args.runs)
    )

    verdict = build_scientific_verdict(code_res, bundle_res, live_browser_res, api_res, sql_explain_res, unit_res)

    report = {
        "skill": {
            "name": skill_versions.SKILL_NAME,
            "version": installed_version,
            "update_check": update_info,
        },
        "pr": {
            "number": pr_number,
            "title": pr.get("title"),
            "url": pr.get("url"),
            "base": pr.get("baseRefName"),
            "head": pr.get("headRefName"),
            "head_oid": pr.get("headRefOid"),
        },
        "verdict": verdict,
        "structure": code_res["structure"],
        "tools": {
            "bundle_and_local_browser": bundle_res,
            "live_browser": live_browser_res,
            "live_api": api_res,
            "sql_explain": sql_explain_res,
            "unit_test_bench": unit_res,
            "code_metrics": code_res,
        },
    }

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    print(f"PR #{pr_number} — {pr.get('title')}")
    print(f"Measured performance: {verdict['headline']}")
    st = report["structure"]
    print(f"Structure: {st['score']}/10 ({st['direction']}) — {'; '.join(st['basis'][:3])}")
    for c in verdict.get("significant_changes", []):
        print(f"  * {c['detail']}")
    for n in verdict.get("neutral_findings", [])[:4]:
        print(f"  - {n}")
    if verdict.get("unmeasured_reason"):
        print(f"  ! {verdict['unmeasured_reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
