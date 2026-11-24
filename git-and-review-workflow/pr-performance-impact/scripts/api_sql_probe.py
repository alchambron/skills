#!/usr/bin/env python3
"""SQL/JPA static profiler + live HTTP API latency benchmarker.

Uses Python 3.9+ stdlib only. Never modifies repository state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


SQL_PATTERNS = {
    "indexes_added": re.compile(r"\bCREATE\s+(?:UNIQUE\s+)?INDEX\b", re.IGNORECASE),
    "indexes_dropped": re.compile(r"\bDROP\s+INDEX\b", re.IGNORECASE),
    "select_star": re.compile(r"\bSELECT\s+\*", re.IGNORECASE),
    "leading_wildcard_like": re.compile(r"\bI?LIKE\s+'%", re.IGNORECASE),
    "joins": re.compile(r"\bJOIN\b", re.IGNORECASE),
    "distinct": re.compile(r"\bSELECT\s+DISTINCT\b", re.IGNORECASE),
    "order_or_group": re.compile(r"\b(?:ORDER|GROUP)\s+BY\b", re.IGNORECASE),
    "locking_ddl": re.compile(
        r"\b(?:VACUUM\s+FULL|LOCK\s+TABLE|REINDEX\s+(?!CONCURRENTLY))",
        re.IGNORECASE,
    ),
}

NLS_RES_JOIN_RE = re.compile(
    r"\bJOIN\s+(?:\w+\.)?(\w+_nls_res)\s+(?:AS\s+)?(\w+)\s+ON\s+([^\n;]+)",
    re.IGNORECASE,
)


def git_blob(repo_root: str, ref: str, rel_path: str) -> str:
    res = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return res.stdout if res.returncode == 0 else ""


def extract_sql_surface(code: str, ext: str) -> str:
    """Extract SQL text from .sql files or Java @Query / native SQL literals."""
    if ext == ".sql":
        return code
    # Extract string literals inside @Query(...) or createNativeQuery(...) or SQL blocks.
    blocks = re.findall(r'"""(.*?)"""', code, flags=re.S)
    quoted = re.findall(r'"([^"\n]*(?:SELECT|UPDATE|DELETE|INSERT|JOIN|_nls_res)[^"\n]*)"', code, flags=re.IGNORECASE)
    return "\n".join(blocks + quoted)


def count_unfiltered_nls_res(sql_text: str) -> int:
    """Count _nls_res joins missing an explicit locale = ... constraint."""
    missing = 0
    for match in NLS_RES_JOIN_RE.finditer(sql_text):
        alias = match.group(2)
        on_clause = match.group(3)
        # Look ahead a bit in case ON spans multiple lines.
        window = sql_text[match.start() : match.end() + 180]
        if not re.search(rf"\b{re.escape(alias)}\.locale\s*=", window, re.IGNORECASE):
            if not re.search(r"\blocale\s*=", on_clause, re.IGNORECASE):
                missing += 1
    return missing


def analyze_sql_and_jpa(repo_root: str, base_ref: str, head_ref: str, paths: List[str]) -> Dict[str, Any]:
    """Compute exact before/after SQL & JPA query deltas across changed files."""
    sql_targets = [p for p in paths if p.endswith(".sql") or (p.endswith(".java") and p.startswith("src/main/"))]
    if not sql_targets:
        return {"status": "skipped", "reason": "No backend Java or SQL files changed"}

    deltas: Dict[str, int] = {}
    unfiltered_nls_added = 0
    files_with_sql = 0

    for rel_path in sql_targets:
        ext = Path(rel_path).suffix.lower()
        before_sql = extract_sql_surface(git_blob(repo_root, base_ref, rel_path), ext)
        after_sql = extract_sql_surface(git_blob(repo_root, head_ref, rel_path), ext)
        if not before_sql.strip() and not after_sql.strip():
            continue
        files_with_sql += 1

        for name, rx in SQL_PATTERNS.items():
            d = len(rx.findall(after_sql)) - len(rx.findall(before_sql))
            if d != 0:
                deltas[name] = deltas.get(name, 0) + d

        nls_delta = count_unfiltered_nls_res(after_sql) - count_unfiltered_nls_res(before_sql)
        if nls_delta > 0:
            unfiltered_nls_added += nls_delta

    if unfiltered_nls_added > 0:
        deltas["unfiltered_nls_res_joins"] = unfiltered_nls_added

    return {
        "status": "ok",
        "files_scanned": len(sql_targets),
        "files_with_sql": files_with_sql,
        "deltas": deltas,
    }


def _probe_once(url: str, headers: Dict[str, str], timeout_s: float) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read()
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            return {"ok": True, "status": resp.status, "ms": elapsed_ms, "bytes": len(body)}
    except urllib.error.HTTPError as exc:
        body = exc.read() if hasattr(exc, "read") else b""
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": False, "status": exc.code, "ms": elapsed_ms, "bytes": len(body), "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return {"ok": False, "status": 0, "ms": elapsed_ms, "bytes": 0, "error": str(exc)}


def percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[idx]


def benchmark_endpoint(
    base_url: str,
    pr_url: str,
    endpoint: str,
    runs: int = 5,
    timeout_s: float = 10.0,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Compare live HTTP latency & payload size on one endpoint across base vs PR."""
    hdrs = dict(headers or {})
    b_full = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
    p_full = pr_url.rstrip("/") + "/" + endpoint.lstrip("/")

    # Warmup once on each target so TLS/JIT cold start does not skew median.
    _probe_once(b_full, hdrs, timeout_s)
    _probe_once(p_full, hdrs, timeout_s)

    base_samples = [_probe_once(b_full, hdrs, timeout_s) for _ in range(runs)]
    pr_samples = [_probe_once(p_full, hdrs, timeout_s) for _ in range(runs)]

    b_ms = [s["ms"] for s in base_samples if s["status"] > 0]
    p_ms = [s["ms"] for s in pr_samples if s["status"] > 0]
    if not b_ms or not p_ms:
        return {
            "endpoint": endpoint,
            "status": "error",
            "base_error": base_samples[0].get("error"),
            "pr_error": pr_samples[0].get("error"),
        }

    b_med = statistics.median(b_ms)
    p_med = statistics.median(p_ms)
    # Positive % = faster (improvement).
    speed_pct = ((b_med - p_med) / b_med * 100.0) if b_med > 0 else 0.0

    b_bytes = int(statistics.median([s["bytes"] for s in base_samples]))
    p_bytes = int(statistics.median([s["bytes"] for s in pr_samples]))
    bytes_pct = ((b_bytes - p_bytes) / b_bytes * 100.0) if b_bytes > 0 else 0.0

    return {
        "endpoint": endpoint,
        "status": "ok",
        "base_http_status": base_samples[0]["status"],
        "pr_http_status": pr_samples[0]["status"],
        "base_median_ms": round(b_med, 1),
        "pr_median_ms": round(p_med, 1),
        "base_p95_ms": round(percentile(b_ms, 95), 1),
        "pr_p95_ms": round(percentile(p_ms, 95), 1),
        "speed_improvement_pct": round(speed_pct, 2),
        "base_bytes": b_bytes,
        "pr_bytes": p_bytes,
        "payload_reduction_pct": round(bytes_pct, 2),
    }


def benchmark_api(
    base_api: Optional[str],
    pr_api: Optional[str],
    endpoints: List[str],
    runs: int = 5,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    if not base_api or not pr_api:
        return {"status": "skipped", "reason": "No live base/PR API URLs available"}
    headers: Dict[str, str] = {"Accept": "application/json"}
    token = os.environ.get("PR_PERF_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    results = [benchmark_endpoint(base_api, pr_api, ep, runs=runs, timeout_s=timeout_s, headers=headers) for ep in endpoints]
    ok = [r for r in results if r.get("status") == "ok"]
    if not ok:
        return {"status": "skipped", "reason": "Endpoints unreachable", "endpoints": results}

    median_speed_pct = round(statistics.median([r["speed_improvement_pct"] for r in ok]), 2)
    return {
        "status": "ok",
        "runs_per_endpoint": runs,
        "median_speed_improvement_pct": median_speed_pct,
        "endpoints": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SQL/JPA profiler and live HTTP API latency benchmarker.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", help="Base git ref")
    parser.add_argument("--head", help="Head git ref")
    parser.add_argument("--files", nargs="*", default=[], help="Changed file paths")
    parser.add_argument("--base-api", help="Baseline API origin")
    parser.add_argument("--pr-api", help="PR preview API origin")
    parser.add_argument("--endpoints", default="/actuator/health", help="Comma-separated API paths")
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    out: Dict[str, Any] = {}
    if args.base and args.head:
        out["sql_jpa"] = analyze_sql_and_jpa(args.repo, args.base, args.head, args.files)
    if args.base_api and args.pr_api:
        eps = [e.strip() for e in args.endpoints.split(",") if e.strip()]
        out["api_benchmark"] = benchmark_api(args.base_api, args.pr_api, eps, runs=args.runs)

    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
