#!/usr/bin/env python3
"""Unified precision PR performance & structure analyzer.

Orchestrates up to 4 measurement tools without modifying the working tree:
  1. code_metrics.py   - Complexity, nesting, duplication, caller reach, perf patterns
  2. api_sql_probe.py  - SQL/JPA query/index profiler + live HTTP API latency benchmark
  3. bundle_diff.mjs   - Ephemeral worktree Vite production build diff (raw, gzip, brotli, entry chunk)
  4. browser_bench.mjs - Playwright Web Vitals (TTFB, FCP, LCP, CLS, TBT), JS heap, & API timing

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
sys.path.insert(0, str(SCRIPT_DIR))

import api_sql_probe  # noqa: E402
import code_metrics  # noqa: E402


def run_cmd(cmd: List[str], cwd: str, check: bool = True, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, check=check, timeout=timeout)


def resolve_repo_root(repo: str) -> str:
    res = run_cmd(["git", "rev-parse", "--show-toplevel"], cwd=repo)
    return res.stdout.strip()


def fetch_pr_metadata(repo_root: str, target: Optional[str]) -> Dict[str, Any]:
    fields = "number,url,title,body,baseRefName,headRefName,headRefOid,files,comments"
    cmd = ["gh", "pr", "view"]
    if target:
        cmd.append(str(target))
    cmd.extend(["--json", fields])
    res = run_cmd(cmd, cwd=repo_root)
    return json.loads(res.stdout)


def ensure_refs_available(repo_root: str, pr_number: int, base_branch: str, head_oid: str) -> Tuple[str, str]:
    """Fetch PR base & head into local refs without modifying current branch."""
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


def _git_object_exists(repo_root: str, ref: str) -> bool:
    res = run_cmd(["git", "cat-file", "-e", f"{ref}^{{commit}}"], cwd=repo_root, check=False)
    return res.returncode == 0


def discover_preview_urls(
    comments: List[Dict[str, Any]],
    host_regex: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Extract ephemeral preview web & optional API URLs from PR comments when a pattern is configured."""
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
    """Fast check whether a preview host responds without hanging on cold wake."""
    req = urllib.request.Request(url, method="GET", headers={"User-Agent": "pr-perf-impact/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 500
    except urllib.error.HTTPError as exc:
        return exc.code in (401, 403)
    except Exception:  # noqa: BLE001
        return False


def run_node_tool(script_name: str, extra_args: List[str], repo_root: str, timeout: int = 240) -> Dict[str, Any]:
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


def synthesize_impact(
    code: Dict[str, Any],
    sql: Dict[str, Any],
    bundle: Dict[str, Any],
    browser: Dict[str, Any],
    api_bench: Dict[str, Any],
) -> Dict[str, Any]:
    """Combine measured + static signals into an honest overall app % range and confidence."""
    prod_files = code.get("prod_files_count", 0)
    weight = float(code.get("max_app_weight", 0.0))

    if prod_files == 0:
        return {
            "overall_app_pct_low": 0.0,
            "overall_app_pct_high": 0.0,
            "overall_app_label": "~0%",
            "targeted_flow_label": None,
            "basis": "measured + static (no runtime files changed)",
            "confidence": "High",
            "signals": ["No production runtime source files changed"],
        }

    measured_parts: List[Tuple[float, float, str]] = []
    static_delta = 0.0
    signals: List[str] = []
    targeted_notes: List[str] = []
    has_runtime_measure = False
    has_bundle_measure = False

    if browser.get("status") == "ok" and browser.get("median_runtime_improvement_pct") is not None:
        has_runtime_measure = True
        b_pct = float(browser["median_runtime_improvement_pct"])
        measured_parts.append((b_pct, 0.55, f"Browser Web Vitals median {b_pct:+.1f}%"))
        signals.append(f"Playwright runtime median: {b_pct:+.1f}% across {len(browser.get('routes', {}))} route(s)")
        if abs(b_pct) >= 5.0:
            targeted_notes.append(f"{b_pct:+.1f}% on tested route(s)")

    if api_bench.get("status") == "ok" and api_bench.get("median_speed_improvement_pct") is not None:
        has_runtime_measure = True
        a_pct = float(api_bench["median_speed_improvement_pct"])
        measured_parts.append((a_pct, 0.45, f"Live API latency {a_pct:+.1f}%"))
        signals.append(f"Live API median latency delta: {a_pct:+.1f}%")
        if abs(a_pct) >= 5.0:
            targeted_notes.append(f"{a_pct:+.1f}% on probed API")

    if bundle.get("status") == "ok":
        has_bundle_measure = True
        entry = bundle["entry_gzip_kb"]
        total_js = bundle["total_js_gzip_kb"]
        entry_red = float(entry["reduction_pct"])
        total_red = float(total_js["reduction_pct"])
        bundle_app_pct = (entry_red * 0.35) + (total_red * 0.10)
        measured_parts.append((bundle_app_pct, 0.30, f"Entry gzip {entry['delta_kb']:+.1f} KB ({entry['delta_pct']:+.1f}%)"))
        signals.append(
            f"Bundle entry gzip: {entry['base']}→{entry['head']} KB ({entry['delta_kb']:+.1f} KB / {entry['delta_pct']:+.1f}%), "
            f"total JS gzip: {total_js['delta_kb']:+.1f} KB"
        )

    pat = code.get("pattern_deltas", {})
    if pat.get("db_call_in_loop", 0) != 0:
        d = pat["db_call_in_loop"]
        static_delta += (-4.0 * d) * max(weight, 0.25)
        signals.append(f"DB calls inside loops delta: {d:+d}")
    if pat.get("cache_annotations", 0) != 0:
        d = pat["cache_annotations"]
        static_delta += (2.5 * d) * max(weight, 0.25)
        signals.append(f"Caching annotations delta: {d:+d}")
    if pat.get("batch_ops", 0) != 0:
        d = pat["batch_ops"]
        static_delta += (2.0 * d) * max(weight, 0.25)
        signals.append(f"Batch DB ops delta: {d:+d}")
    if pat.get("eager_fetch", 0) != 0:
        d = pat["eager_fetch"]
        static_delta += (-3.0 * d) * max(weight, 0.25)
        signals.append(f"FetchType.EAGER delta: {d:+d}")
    if pat.get("lazy_imports", 0) != 0:
        d = pat["lazy_imports"]
        static_delta += (1.5 * d) * max(weight, 0.3)
        signals.append(f"Lazy imports delta: {d:+d}")
    if pat.get("heavy_lib_imports", 0) != 0:
        d = pat["heavy_lib_imports"]
        static_delta += (-1.5 * d) * max(weight, 0.3)
        signals.append(f"Heavy frontend library imports delta: {d:+d}")
    if pat.get("polling_intervals", 0) != 0:
        d = pat["polling_intervals"]
        static_delta += (-1.5 * d) * max(weight, 0.3)
        signals.append(f"Polling/interval timers delta: {d:+d}")
    if pat.get("raw_fetch_in_ui", 0) != 0:
        d = pat["raw_fetch_in_ui"]
        static_delta += (-1.0 * d) * max(weight, 0.25)
        signals.append(f"Raw fetch/axios in UI delta: {d:+d}")

    sql_deltas = (sql or {}).get("deltas", {})
    if sql_deltas.get("indexes_added", 0) > 0:
        d = sql_deltas["indexes_added"]
        static_delta += (2.5 * d) * max(weight, 0.35)
        signals.append(f"SQL indexes added: +{d}")
    if sql_deltas.get("indexes_dropped", 0) > 0:
        d = sql_deltas["indexes_dropped"]
        static_delta += (-2.5 * d) * max(weight, 0.35)
        signals.append(f"SQL indexes dropped: -{d}")
    if sql_deltas.get("unfiltered_nls_res_joins", 0) > 0:
        d = sql_deltas["unfiltered_nls_res_joins"]
        static_delta += (-5.0 * d) * max(weight, 0.4)
        signals.append(f"Unfiltered _nls_res join(s) added: +{d} (multiplies rows by locale count)")
    if sql_deltas.get("leading_wildcard_like", 0) != 0:
        d = sql_deltas["leading_wildcard_like"]
        static_delta += (-1.5 * d) * max(weight, 0.25)
        signals.append(f"Leading-wildcard LIKE delta: {d:+d}")

    if measured_parts:
        w_sum = sum(w for _, w, _ in measured_parts)
        measured_center = sum(val * w for val, w, _ in measured_parts) / w_sum
        reach_factor = 1.0 if (has_bundle_measure and not has_runtime_measure) else max(0.35, min(1.0, weight + 0.35))
        center = (measured_center * reach_factor) + (static_delta * 0.35)
    else:
        center = static_delta

    center = max(-40.0, min(40.0, center))

    if has_runtime_measure:
        spread = max(1.0, abs(center) * 0.25)
        confidence = "High"
        basis = "measured (live runtime + bundle/static)" if has_bundle_measure else "measured (live runtime + static)"
    elif has_bundle_measure:
        spread = max(1.0, abs(center) * 0.4)
        confidence = "Medium"
        basis = "hybrid (measured bundle + static metrics)"
    else:
        spread = max(1.0, abs(center) * 0.5) if abs(center) >= 0.5 else 0.5
        confidence = "Medium" if signals else "Low"
        basis = "static metrics (complexity + SQL/pattern scan)"

    if abs(center) < 0.25:
        low, high = 0.0, 0.0
        label = "~0%"
    else:
        low = round(center - spread, 1)
        high = round(center + spread, 1)
        if abs(low) < 0.2 and abs(high) < 0.2:
            label = "~0%"
        else:
            label = f"~{low:+.1f}% to {high:+.1f}%"

    return {
        "overall_app_pct_low": low,
        "overall_app_pct_high": high,
        "overall_app_label": label,
        "targeted_flow_label": "; ".join(targeted_notes) if targeted_notes else None,
        "basis": basis,
        "confidence": confidence,
        "signals": signals or ["No major runtime bottlenecks or speedups detected in changed paths"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run multi-tool precision performance & structure analysis on a PR.")
    parser.add_argument("target", nargs="?", help="PR number or URL (defaults to current branch PR)")
    parser.add_argument("--repo", default=".", help="Repository path")
    parser.add_argument("--no-bundle", action="store_true", help="Skip Vite production bundle diff")
    parser.add_argument("--no-browser", action="store_true", help="Skip Playwright live browser benchmark")
    parser.add_argument("--no-api", action="store_true", help="Skip live HTTP API latency probe")
    parser.add_argument("--base-url", help="Baseline frontend URL (or set PR_PERF_BASE_WEB_URL)")
    parser.add_argument("--pr-url", help="PR preview frontend URL")
    parser.add_argument("--base-api", help="Baseline API URL (or set PR_PERF_BASE_API_URL)")
    parser.add_argument("--pr-api", help="PR preview API URL")
    parser.add_argument("--preview-host-regex", help="Regex to extract preview URL from PR comments")
    parser.add_argument("--routes", default="/", help="Comma-separated frontend routes for Playwright benchmark")
    parser.add_argument("--endpoints", default="/actuator/health", help="Comma-separated API endpoints to probe")
    parser.add_argument("--runs", type=int, default=3, help="Measured iterations per route/endpoint")
    parser.add_argument("--login", action="store_true", help="Attempt login with .env test credentials in browser bench")
    parser.add_argument("--json", action="store_true", help="Output full structured JSON")
    args = parser.parse_args()

    repo_root = resolve_repo_root(args.repo)
    pr = fetch_pr_metadata(repo_root, args.target)
    pr_number = int(pr["number"])
    temp_head_ref = f"refs/remotes/pr-perf/pr-{pr_number}"
    base_ref, head_ref = ensure_refs_available(repo_root, pr_number, pr["baseRefName"], pr["headRefOid"])

    try:
        code_res = code_metrics.analyze_diff(repo_root, base_ref, head_ref)
        changed_paths = [f["path"] for f in pr.get("files", []) if f.get("path")]

        sql_res = api_sql_probe.analyze_sql_and_jpa(repo_root, base_ref, head_ref, changed_paths)

        if args.no_bundle:
            bundle_res: Dict[str, Any] = {"status": "skipped", "reason": "Disabled via --no-bundle"}
        elif code_res.get("frontend_prod_files", 0) == 0:
            bundle_res = {"status": "skipped", "reason": "No frontend production files changed"}
        else:
            bundle_res = run_node_tool(
                "bundle_diff.mjs",
                ["--repo", repo_root, "--base", base_ref, "--head", head_ref],
                repo_root,
            )
    finally:
        run_cmd(["git", "update-ref", "-d", temp_head_ref], cwd=repo_root, check=False)

    auto_web, auto_api = discover_preview_urls(pr.get("comments", []), args.preview_host_regex)
    pr_web_url = args.pr_url or auto_web
    base_web_url = args.base_url or os.environ.get("PR_PERF_BASE_WEB_URL")
    pr_api_url = args.pr_api or auto_api
    base_api_url = args.base_api or os.environ.get("PR_PERF_BASE_API_URL")

    if args.no_browser:
        browser_res: Dict[str, Any] = {"status": "skipped", "reason": "Disabled via --no-browser"}
    elif not pr_web_url or not base_web_url:
        browser_res = {"status": "skipped", "reason": "Pass --base-url and --pr-url (or env vars) to benchmark live"}
    elif not is_url_awake(pr_web_url):
        browser_res = {"status": "skipped", "reason": f"Preview host not awake ({pr_web_url})"}
    else:
        b_args = [
            "--repo", repo_root,
            "--base-url", base_web_url,
            "--pr-url", pr_web_url,
            "--routes", args.routes,
            "--runs", str(args.runs),
        ]
        if args.login:
            b_args.append("--login")
        browser_res = run_node_tool("browser_bench.mjs", b_args, repo_root)

    if args.no_api:
        api_res: Dict[str, Any] = {"status": "skipped", "reason": "Disabled via --no-api"}
    elif not pr_api_url or not base_api_url:
        api_res = {"status": "skipped", "reason": "Pass --base-api and --pr-api to benchmark live API"}
    elif not is_url_awake(pr_api_url):
        api_res = {"status": "skipped", "reason": f"Preview API not awake ({pr_api_url})"}
    else:
        eps = [e.strip() for e in args.endpoints.split(",") if e.strip()]
        api_res = api_sql_probe.benchmark_api(base_api_url, pr_api_url, eps, runs=max(3, args.runs))

    synthesis = synthesize_impact(code_res, sql_res, bundle_res, browser_res, api_res)

    report = {
        "pr": {
            "number": pr_number,
            "title": pr.get("title"),
            "url": pr.get("url"),
            "base": pr.get("baseRefName"),
            "head": pr.get("headRefName"),
            "head_oid": pr.get("headRefOid"),
        },
        "synthesis": synthesis,
        "structure": code_res["structure"],
        "tools": {
            "code_metrics": code_res,
            "sql_jpa": sql_res,
            "bundle_diff": bundle_res,
            "browser_bench": browser_res,
            "api_benchmark": api_res,
        },
    }

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    tools_summary = ", ".join(
        f"{name}:{payload.get('status', 'unknown')}"
        for name, payload in report["tools"].items()
    )
    print(f"PR #{pr_number} — {pr.get('title')}")
    print(f"Overall app performance: {synthesis['overall_app_label']} | {synthesis['confidence']} confidence ({synthesis['basis']})")
    if synthesis["targeted_flow_label"]:
        print(f"Targeted flow: {synthesis['targeted_flow_label']}")
    st = report["structure"]
    print(f"Structure: {st['score']}/10 ({st['direction']}) — {'; '.join(st['basis'][:3])}")
    print(f"Tools: {tools_summary}")
    for sig in synthesis["signals"][:4]:
        print(f"  - {sig}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
