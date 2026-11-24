#!/usr/bin/env python3
"""Static code complexity, structure, reach, and JouleMV perf-pattern analyzer.

Compares base vs head git blobs without mutating the working tree.
Uses Python 3.9+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


PROD_EXTENSIONS = {".java", ".ts", ".tsx", ".js", ".jsx", ".sql"}
TEST_PATH_RE = re.compile(
    r"(^|/)(__tests__|__mocks__|__fixtures__|test|tests|stories)/|"
    r"\.(test|spec|stories)\.[^.]+$|"
    r"(^|/)src/test/",
    re.IGNORECASE,
)
DOCS_CONFIG_RE = re.compile(
    r"(^|/)(\.agents|\.claude|\.github|docs)/|"
    r"\.(md|txt|json|ya?ml|lock|snap|svg|png|jpg|css\.map)$",
    re.IGNORECASE,
)

GLOBAL_PATH_RE = re.compile(
    r"(frontend/src/(app|router|api/baseApi|helpers/Request|contexts|providers)/|"
    r"src/main/java/.*/(security|filter|interceptor|config|tenant)/)",
    re.IGNORECASE,
)
SHARED_PATH_RE = re.compile(
    r"(frontend/src/(shared|hooks|util|api)/|"
    r"src/main/java/.*/(service|repository|dao|cache|common)/)",
    re.IGNORECASE,
)
CORE_SCREEN_RE = re.compile(
    r"(frontend/src/screens/(Home|Building|Meter|Chart|ChartDashboard|Formula|Point|PointList|Site|Portfolio)/|"
    r"src/main/java/.*/(building|meter|chart|formula|point|site|dashboard|energy|consumption))",
    re.IGNORECASE,
)

BRANCH_TOKEN_RE = re.compile(
    r"\b(?:if|else\s+if|for|while|case|catch)\b|&&|\|\||\?(?!\.)",
)
FUNC_DECL_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+\w+|"
    r"(?:export\s+)?const\s+\w+\s*=\s*(?:async\s*)?\([^)]*\)\s*=>|"
    r"(?:public|private|protected)\s+(?:static\s+)?(?:final\s+)?[\w<>\[\], ?]+\s+\w+\s*\([^)]*\)\s*(?:throws\s+[\w,\s]+)?\{"
    r")"
)

# Performance patterns tracked as (before_count, after_count) deltas.
JAVA_PERF_PATTERNS = {
    "cache_annotations": re.compile(r"@(?:Cacheable|CachePut|CacheEvict)\b|LoadingCache|Caffeine"),
    "readonly_tx": re.compile(r"@Transactional\s*\([^)]*readOnly\s*=\s*true"),
    "eager_fetch": re.compile(r"FetchType\.EAGER"),
    "select_star": re.compile(r"\bSELECT\s+\*", re.IGNORECASE),
    "blocking_sleep": re.compile(r"\bThread\.sleep\s*\("),
    "batch_ops": re.compile(r"\b(?:saveAll|deleteAllInBatch|batchUpdate|findAllById|IN\s*\(\s*:)\b"),
}

FRONTEND_PERF_PATTERNS = {
    "memoization_hooks": re.compile(r"\b(?:useMemo|useCallback|React\.memo|memo)\s*\("),
    "lazy_imports": re.compile(r"\bReact\.lazy\s*\(|\blazy\s*\(\s*\(\)\s*=>\s*import\("),
    "polling_intervals": re.compile(r"\b(?:pollingInterval|setInterval)\b"),
    "heavy_lib_imports": re.compile(
        r"""from\s+['"](?:xlsx|jspdf|jspdf-autotable|html2canvas-pro|maplibre-gl|recharts)['"]"""
    ),
    "raw_fetch_in_ui": re.compile(r"\b(?:fetch|axios\.(?:get|post|put|delete|patch))\s*\("),
    "selector_alloc": re.compile(
        r"useAppSelector|useSelector\s*\([^)]*=>\s*(?:\(\s*\{|\{|[^)]*\.(?:filter|map)\s*\()"
    ),
}

DB_CALL_RE = re.compile(
    r"\b\w*(?:Repository|Repo|Dao|DAO|JdbcTemplate|EntityManager|Service)\.\w+\s*\("
)


def strip_comments_and_strings(code: str, ext: str) -> str:
    """Remove comments and string literals so brace/branch counts stay accurate."""
    if ext == ".sql":
        code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
        code = re.sub(r"--[^\n]*", " ", code)
        code = re.sub(r"'(?:''|[^'])*'", "''", code)
        return code

    # Remove block comments, line comments, and quoted strings.
    pattern = re.compile(
        r"/\*.*?\*/|//[^\n]*|`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        flags=re.S,
    )
    return pattern.sub(lambda m: "\n" * m.group(0).count("\n") if m.group(0).startswith("/*") else " ", code)


def measure_code_shape(code: str, ext: str) -> Dict[str, Any]:
    """Compute cyclomatic branches, max nesting depth, LOC, and duplicate lines."""
    if not code:
        return {
            "loc": 0,
            "branches": 0,
            "max_nesting": 0,
            "func_count": 0,
            "avg_func_loc": 0.0,
            "duplicate_windows": 0,
        }

    cleaned = strip_comments_and_strings(code, ext)
    lines = [ln.strip() for ln in cleaned.splitlines() if ln.strip()]
    loc = len(lines)
    branches = len(BRANCH_TOKEN_RE.findall(cleaned))

    depth = 0
    max_nesting = 0
    for ch in cleaned:
        if ch == "{":
            depth += 1
            if depth > max_nesting:
                max_nesting = depth
        elif ch == "}":
            depth = max(0, depth - 1)

    func_count = len(FUNC_DECL_RE.findall(cleaned))
    avg_func_loc = round(loc / func_count, 1) if func_count > 0 else float(loc)

    # Count repeated normalized 4-line windows inside the file.
    norm_lines = [re.sub(r"\s+", " ", ln) for ln in lines if len(ln) >= 12]
    seen = set()
    dups = 0
    for i in range(max(0, len(norm_lines) - 3)):
        window = tuple(norm_lines[i : i + 4])
        if window in seen:
            dups += 1
        else:
            seen.add(window)

    return {
        "loc": loc,
        "branches": branches,
        "max_nesting": max_nesting,
        "func_count": func_count,
        "avg_func_loc": avg_func_loc,
        "duplicate_windows": dups,
    }


def count_db_calls_in_loops(code: str) -> int:
    """Detect likely N+1 patterns: repo/DAO/service calls inside loops."""
    cleaned = strip_comments_and_strings(code, ".java")
    lines = cleaned.splitlines()
    loop_depth_stack: List[int] = []
    brace_depth = 0
    hits = 0
    loop_start_re = re.compile(r"\b(?:for|while)\s*\(|\.forEach\s*\(")

    for line in lines:
        if loop_start_re.search(line):
            loop_depth_stack.append(brace_depth + 1)
        brace_depth += line.count("{")
        if loop_depth_stack and DB_CALL_RE.search(line):
            hits += 1
        brace_depth = max(0, brace_depth - line.count("}"))
        while loop_depth_stack and brace_depth < loop_depth_stack[-1]:
            loop_depth_stack.pop()
    return hits


def detect_perf_patterns(code: str, path: str, ext: str) -> Dict[str, int]:
    """Count domain-specific performance markers in a single file version."""
    if not code:
        return {}
    counts: Dict[str, int] = {}
    if ext == ".java":
        cleaned = strip_comments_and_strings(code, ext)
        for name, rx in JAVA_PERF_PATTERNS.items():
            counts[name] = len(rx.findall(cleaned))
        counts["db_call_in_loop"] = count_db_calls_in_loops(code)
    elif ext in {".ts", ".tsx", ".js", ".jsx"}:
        for name, rx in FRONTEND_PERF_PATTERNS.items():
            if name == "raw_fetch_in_ui" and ("src/api/" in path or "src/helpers/Request/" in path):
                counts[name] = 0
            else:
                counts[name] = len(rx.findall(code))
    return counts


def classify_file(path: str) -> Tuple[str, float]:
    """Return (category, app_weight) for a repository-relative path."""
    p = Path(path)
    if TEST_PATH_RE.search(path):
        return ("test", 0.0)
    if DOCS_CONFIG_RE.search(path) or p.suffix.lower() not in PROD_EXTENSIONS:
        return ("non-runtime", 0.0)
    if GLOBAL_PATH_RE.search(path):
        return ("global", 1.0)
    if SHARED_PATH_RE.search(path):
        return ("shared", 0.6)
    if CORE_SCREEN_RE.search(path):
        return ("core-screen", 0.35)
    if path.startswith("src/main/sql/"):
        return ("sql-migration", 0.4)
    return ("secondary", 0.15)


def git_blob(repo_root: str, ref: str, rel_path: str) -> str:
    """Read file content at a git ref without touching the working tree."""
    res = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return res.stdout if res.returncode == 0 else ""


def estimate_fan_in(repo_root: str, rel_path: str) -> int:
    """Count how many other files reference this module/class name."""
    stem = Path(rel_path).stem
    if stem in {"index", "types", "utils", "constants", "styles", "App", "main"} or len(stem) < 4:
        return 0
    rg = shutil.which("rg")
    if rg:
        cmd = [rg, "-l", "--fixed-strings", stem, "src/main/java", "frontend/src"]
    else:
        cmd = ["git", "grep", "-l", "-F", stem, "--", "src/main/java", "frontend/src"]
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        return 0
    matches = [m for m in res.stdout.splitlines() if m and m != rel_path and not TEST_PATH_RE.search(m)]
    return len(matches)


def get_changed_files(repo_root: str, base_ref: str, head_ref: str) -> List[Dict[str, Any]]:
    """Return changed files with numstat additions/deletions and status."""
    status_res = subprocess.run(
        ["git", "diff", "--name-status", f"{base_ref}...{head_ref}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    numstat_res = subprocess.run(
        ["git", "diff", "--numstat", f"{base_ref}...{head_ref}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    stats_map: Dict[str, Tuple[int, int]] = {}
    for line in numstat_res.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            add = int(parts[0]) if parts[0].isdigit() else 0
            rem = int(parts[1]) if parts[1].isdigit() else 0
            stats_map[parts[-1]] = (add, rem)

    files = []
    for line in status_res.stdout.splitlines():
        parts = line.split("\t")
        if not parts:
            continue
        code = parts[0]
        if code.startswith(("R", "C")) and len(parts) >= 3:
            old_path, new_path = parts[1], parts[2]
        elif len(parts) >= 2:
            old_path = new_path = parts[1]
        else:
            continue
        add, rem = stats_map.get(new_path, (0, 0))
        files.append(
            {
                "status": code[0],
                "old_path": old_path,
                "path": new_path,
                "additions": add,
                "deletions": rem,
            }
        )
    return files


def compute_structure_score(summary: Dict[str, Any]) -> Dict[str, Any]:
    """Compute a transparent, reproducible 1-10 code structure score."""
    score = 6.5
    reasons: List[str] = []

    prod_count = summary["prod_files_count"]
    test_count = summary["test_files_count"]
    branch_delta = summary["totals"]["branches_delta"]
    nesting_delta = summary["totals"]["max_nesting_delta"]
    dup_delta = summary["totals"]["duplicate_windows_delta"]
    net_loc = summary["totals"]["net_prod_loc"]
    avg_func_delta = summary["totals"]["avg_func_loc_delta"]

    if prod_count == 0:
        # Docs/tests/config only: judge cleanliness neutrally-positive.
        return {"score": 7, "direction": "Neutral", "basis": ["No production runtime files changed"]}

    # Complexity reduction vs growth.
    if branch_delta <= -5:
        score += 1.2
        reasons.append(f"Reduced branching complexity ({branch_delta:+d})")
    elif branch_delta < 0:
        score += 0.6
        reasons.append(f"Lower branching complexity ({branch_delta:+d})")
    elif branch_delta > 40:
        score -= 1.4
        reasons.append(f"Large branching increase ({branch_delta:+d})")
    elif branch_delta > 15:
        score -= 0.7
        reasons.append(f"Added branching complexity ({branch_delta:+d})")

    # Nesting depth.
    if nesting_delta < 0:
        score += 0.8
        reasons.append(f"Flattened nesting depth ({nesting_delta:+d})")
    elif nesting_delta >= 2:
        score -= 1.0
        reasons.append(f"Deeper nesting ({nesting_delta:+d} levels)")
    elif nesting_delta == 1:
        score -= 0.4

    # Duplication.
    if dup_delta < 0:
        score += 0.8
        reasons.append(f"Removed duplicated blocks ({dup_delta:+d})")
    elif dup_delta >= 3:
        score -= 1.0
        reasons.append(f"Added duplicated blocks ({dup_delta:+d})")
    elif dup_delta > 0:
        score -= 0.4

    # Function length modularity.
    if avg_func_delta <= -5.0:
        score += 0.7
        reasons.append(f"Shorter functions ({avg_func_delta:+.1f} LOC avg)")
    elif avg_func_delta >= 15.0:
        score -= 0.8
        reasons.append(f"Longer functions ({avg_func_delta:+.1f} LOC avg)")

    # Simplification (net code deletion while keeping cohesion).
    if net_loc < -20:
        score += 0.6
        reasons.append(f"Net code simplification ({net_loc:+d} prod LOC)")
    elif net_loc > 600 and prod_count <= 2:
        score -= 0.8
        reasons.append(f"Concentrated large addition ({net_loc:+d} LOC in {prod_count} files)")

    # Companion tests signal healthy structure/testability.
    if test_count > 0:
        score += 0.6
        reasons.append(f"Includes {test_count} test file(s)")

    clamped = max(1, min(10, int(round(score))))
    if clamped >= 7:
        direction = "Better"
    elif clamped >= 5:
        direction = "Neutral"
    else:
        direction = "Worse"

    return {
        "score": clamped,
        "raw_score": round(score, 2),
        "direction": direction,
        "basis": reasons or ["Small, contained change"],
    }


def analyze_diff(repo_root: str, base_ref: str, head_ref: str) -> Dict[str, Any]:
    """Run full static complexity, reach, and pattern delta analysis."""
    changed = get_changed_files(repo_root, base_ref, head_ref)
    file_reports: List[Dict[str, Any]] = []
    pattern_deltas: Dict[str, int] = {}

    branches_delta_total = 0
    max_nesting_delta_worst = 0
    dup_delta_total = 0
    net_prod_loc = 0
    avg_func_deltas: List[float] = []
    max_weight = 0.0
    max_fan_in = 0
    prod_files = 0
    test_files = 0
    frontend_prod_files = 0
    backend_prod_files = 0
    sql_files = 0

    for entry in changed:
        path = entry["path"]
        old_path = entry["old_path"]
        ext = Path(path).suffix.lower()
        category, base_weight = classify_file(path)

        if category == "test":
            test_files += 1
        if category in ("test", "non-runtime"):
            continue

        prod_files += 1
        if path.startswith("frontend/"):
            frontend_prod_files += 1
        elif path.startswith("src/main/sql/"):
            sql_files += 1
        elif path.startswith("src/main/"):
            backend_prod_files += 1

        before_code = "" if entry["status"] == "A" else git_blob(repo_root, base_ref, old_path)
        after_code = "" if entry["status"] == "D" else git_blob(repo_root, head_ref, path)

        before_shape = measure_code_shape(before_code, ext)
        after_shape = measure_code_shape(after_code, ext)

        b_delta = after_shape["branches"] - before_shape["branches"]
        n_delta = after_shape["max_nesting"] - before_shape["max_nesting"]
        d_delta = after_shape["duplicate_windows"] - before_shape["duplicate_windows"]
        loc_delta = after_shape["loc"] - before_shape["loc"]
        f_delta = round(after_shape["avg_func_loc"] - before_shape["avg_func_loc"], 1)

        branches_delta_total += b_delta
        if abs(n_delta) > abs(max_nesting_delta_worst):
            max_nesting_delta_worst = n_delta
        dup_delta_total += d_delta
        net_prod_loc += loc_delta
        if before_shape["func_count"] > 0 and after_shape["func_count"] > 0:
            avg_func_deltas.append(f_delta)

        fan_in = estimate_fan_in(repo_root, path)
        effective_weight = base_weight
        if fan_in >= 15:
            effective_weight = max(effective_weight, 0.85)
        elif fan_in >= 5:
            effective_weight = max(effective_weight, 0.55)
        max_weight = max(max_weight, effective_weight)
        max_fan_in = max(max_fan_in, fan_in)

        before_pat = detect_perf_patterns(before_code, old_path, ext)
        after_pat = detect_perf_patterns(after_code, path, ext)
        file_pat_deltas: Dict[str, int] = {}
        for key in set(before_pat) | set(after_pat):
            delta = after_pat.get(key, 0) - before_pat.get(key, 0)
            if delta != 0:
                file_pat_deltas[key] = delta
                pattern_deltas[key] = pattern_deltas.get(key, 0) + delta

        file_reports.append(
            {
                "path": path,
                "category": category,
                "app_weight": effective_weight,
                "fan_in": fan_in,
                "loc_delta": loc_delta,
                "branches_delta": b_delta,
                "max_nesting_delta": n_delta,
                "duplicate_windows_delta": d_delta,
                "pattern_deltas": file_pat_deltas,
            }
        )

    avg_func_delta = round(sum(avg_func_deltas) / len(avg_func_deltas), 1) if avg_func_deltas else 0.0
    summary = {
        "status": "ok",
        "total_changed_files": len(changed),
        "prod_files_count": prod_files,
        "test_files_count": test_files,
        "frontend_prod_files": frontend_prod_files,
        "backend_prod_files": backend_prod_files,
        "sql_files": sql_files,
        "max_app_weight": round(max_weight, 2),
        "max_fan_in": max_fan_in,
        "totals": {
            "net_prod_loc": net_prod_loc,
            "branches_delta": branches_delta_total,
            "max_nesting_delta": max_nesting_delta_worst,
            "duplicate_windows_delta": dup_delta_total,
            "avg_func_loc_delta": avg_func_delta,
        },
        "pattern_deltas": pattern_deltas,
        "top_files": sorted(file_reports, key=lambda r: (r["app_weight"], abs(r["loc_delta"])), reverse=True)[:10],
    }
    summary["structure"] = compute_structure_score(summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze PR complexity, structure, and JouleMV perf patterns.")
    parser.add_argument("--repo", default=".", help="Git repository root")
    parser.add_argument("--base", required=True, help="Base git ref/SHA")
    parser.add_argument("--head", required=True, help="Head git ref/SHA")
    args = parser.parse_args()

    result = analyze_diff(args.repo, args.base, args.head)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
