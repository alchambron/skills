#!/usr/bin/env python3
"""Scientific HTTP API latency benchmarker & PostgreSQL EXPLAIN ANALYZE runner.

- Uses interleaved A/B sampling (base, pr, base, pr, ...) with warmups discarded.
- Computes median, IQR, p95, % delta, Cliff's delta, and Mann-Whitney U p-value.
- Never fabricates runtime performance % from static SQL text.
Uses Python 3.9+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import stats_util  # noqa: E402


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


def benchmark_endpoint(
    base_url: str,
    pr_url: str,
    endpoint: str,
    runs: int = 9,
    warmups: int = 2,
    timeout_s: float = 10.0,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Interleaved A/B HTTP latency & payload size benchmark on one endpoint."""
    hdrs = dict(headers or {})
    b_full = base_url.rstrip("/") + "/" + endpoint.lstrip("/")
    p_full = pr_url.rstrip("/") + "/" + endpoint.lstrip("/")

    for _ in range(warmups):
        _probe_once(b_full, hdrs, timeout_s)
        _probe_once(p_full, hdrs, timeout_s)

    base_samples: List[Dict[str, Any]] = []
    pr_samples: List[Dict[str, Any]] = []
    for i in range(runs):
        # Alternate order each pair to eliminate ordering bias.
        if i % 2 == 0:
            base_samples.append(_probe_once(b_full, hdrs, timeout_s))
            pr_samples.append(_probe_once(p_full, hdrs, timeout_s))
        else:
            pr_samples.append(_probe_once(p_full, hdrs, timeout_s))
            base_samples.append(_probe_once(b_full, hdrs, timeout_s))

    b_ms = [s["ms"] for s in base_samples if s["status"] > 0]
    p_ms = [s["ms"] for s in pr_samples if s["status"] > 0]
    if len(b_ms) < 3 or len(p_ms) < 3:
        return {
            "endpoint": endpoint,
            "status": "error",
            "base_error": base_samples[0].get("error") if base_samples else "no samples",
            "pr_error": pr_samples[0].get("error") if pr_samples else "no samples",
        }

    latency_cmp = stats_util.compare_ab(b_ms, p_ms, lower_is_better=True)
    b_bytes = stats_util.summarize_samples([float(s["bytes"]) for s in base_samples])["median"] or 0.0
    p_bytes = stats_util.summarize_samples([float(s["bytes"]) for s in pr_samples])["median"] or 0.0
    payload_red_pct = round(((b_bytes - p_bytes) / b_bytes) * 100.0, 2) if b_bytes > 0 else 0.0

    return {
        "endpoint": endpoint,
        "status": "ok",
        "runs": runs,
        "base_http_status": base_samples[0]["status"],
        "pr_http_status": pr_samples[0]["status"],
        "latency_ms": latency_cmp,
        "base_bytes": int(b_bytes),
        "pr_bytes": int(p_bytes),
        "payload_reduction_pct": payload_red_pct,
    }


def benchmark_api(
    base_api: Optional[str],
    pr_api: Optional[str],
    endpoints: List[str],
    runs: int = 9,
    timeout_s: float = 10.0,
) -> Dict[str, Any]:
    if not base_api or not pr_api:
        return {"status": "skipped", "reason": "No live base/PR API URLs provided"}
    headers: Dict[str, str] = {"Accept": "application/json"}
    token = os.environ.get("PR_PERF_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    results = [benchmark_endpoint(base_api, pr_api, ep, runs=runs, timeout_s=timeout_s, headers=headers) for ep in endpoints]
    ok = [r for r in results if r.get("status") == "ok"]
    if not ok:
        return {"status": "skipped", "reason": "Endpoints unreachable", "endpoints": results}

    sig_imps = [r["latency_ms"]["improvement_pct"] for r in ok if r["latency_ms"]["verdict"] == "significant_improvement"]
    sig_regs = [r["latency_ms"]["improvement_pct"] for r in ok if r["latency_ms"]["verdict"] == "significant_regression"]
    all_imps = [r["latency_ms"]["improvement_pct"] for r in ok if r["latency_ms"]["improvement_pct"] is not None]

    return {
        "status": "ok",
        "runs_per_endpoint": runs,
        "median_improvement_pct": round(stats_util.percentile(all_imps, 50.0), 2) if all_imps else 0.0,
        "significant_improvements": len(sig_imps),
        "significant_regressions": len(sig_regs),
        "endpoints": results,
    }


def _explain_once(psql_bin: str, dsn: str, sql: str, timeout_s: float = 30.0) -> Optional[Dict[str, float]]:
    """Run EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) inside a rolled-back transaction."""
    wrapped = f"BEGIN; EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {sql.rstrip().rstrip(';')}; ROLLBACK;"
    res = subprocess.run(
        [psql_bin, dsn, "-X", "-q", "-t", "-A", "-v", "ON_ERROR_STOP=1", "-c", wrapped],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    if res.returncode != 0:
        return None
    # Extract the JSON array returned by EXPLAIN FORMAT JSON.
    text = res.stdout.strip()
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return None
    try:
        payload = json.loads(text[start : end + 1])
        root = payload[0]
        plan = root.get("Plan", {})
        return {
            "execution_ms": float(root.get("Execution Time", 0.0)),
            "planning_ms": float(root.get("Planning Time", 0.0)),
            "shared_hit_blocks": float(plan.get("Shared Hit Blocks", 0.0)),
            "shared_read_blocks": float(plan.get("Shared Read Blocks", 0.0)),
        }
    except (ValueError, KeyError, IndexError, TypeError):
        return None


def benchmark_sql_explain(
    dsn: Optional[str],
    base_sql: Optional[str],
    pr_sql: Optional[str],
    runs: int = 7,
    warmups: int = 2,
) -> Dict[str, Any]:
    """Interleaved PostgreSQL EXPLAIN (ANALYZE, BUFFERS) benchmark in read-only ROLLBACK tx."""
    if not dsn or not base_sql or not pr_sql:
        return {"status": "skipped", "reason": "Requires --pg-dsn (or PR_PERF_PG_DSN) with --sql-base and --sql-pr"}
    psql_bin = shutil.which("psql")
    if not psql_bin:
        return {"status": "skipped", "reason": "psql client not found in PATH"}

    for _ in range(warmups):
        _explain_once(psql_bin, dsn, base_sql)
        _explain_once(psql_bin, dsn, pr_sql)

    base_rows: List[Dict[str, float]] = []
    pr_rows: List[Dict[str, float]] = []
    for i in range(runs):
        if i % 2 == 0:
            b = _explain_once(psql_bin, dsn, base_sql)
            p = _explain_once(psql_bin, dsn, pr_sql)
        else:
            p = _explain_once(psql_bin, dsn, pr_sql)
            b = _explain_once(psql_bin, dsn, base_sql)
        if b:
            base_rows.append(b)
        if p:
            pr_rows.append(p)

    if len(base_rows) < 3 or len(pr_rows) < 3:
        return {"status": "skipped", "reason": "PostgreSQL EXPLAIN ANALYZE failed or returned insufficient samples"}

    exec_cmp = stats_util.compare_ab(
        [r["execution_ms"] for r in base_rows],
        [r["execution_ms"] for r in pr_rows],
        lower_is_better=True,
    )
    buffers_cmp = stats_util.compare_ab(
        [r["shared_hit_blocks"] + r["shared_read_blocks"] for r in base_rows],
        [r["shared_hit_blocks"] + r["shared_read_blocks"] for r in pr_rows],
        lower_is_better=True,
    )
    return {
        "status": "ok",
        "runs": runs,
        "execution_ms": exec_cmp,
        "shared_buffers": buffers_cmp,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Interleaved HTTP API and PostgreSQL EXPLAIN ANALYZE benchmarker.")
    parser.add_argument("--base-api", help="Baseline API origin")
    parser.add_argument("--pr-api", help="PR preview API origin")
    parser.add_argument("--endpoints", default="/actuator/health", help="Comma-separated API paths")
    parser.add_argument("--pg-dsn", default=os.environ.get("PR_PERF_PG_DSN"), help="PostgreSQL connection URI")
    parser.add_argument("--sql-base", help="Baseline SQL query (or @file path) for EXPLAIN ANALYZE")
    parser.add_argument("--sql-pr", help="PR SQL query (or @file path) for EXPLAIN ANALYZE")
    parser.add_argument("--runs", type=int, default=9)
    args = parser.parse_args()

    def load_sql(val: Optional[str]) -> Optional[str]:
        if not val:
            return None
        if val.startswith("@"):
            return Path(val[1:]).read_text(encoding="utf-8")
        return val

    out: Dict[str, Any] = {}
    if args.base_api and args.pr_api:
        eps = [e.strip() for e in args.endpoints.split(",") if e.strip()]
        out["api_benchmark"] = benchmark_api(args.base_api, args.pr_api, eps, runs=args.runs)
    if args.pg_dsn and args.sql_base and args.sql_pr:
        out["sql_explain"] = benchmark_sql_explain(
            args.pg_dsn, load_sql(args.sql_base), load_sql(args.sql_pr), runs=max(5, args.runs)
        )

    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
