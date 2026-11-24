#!/usr/bin/env python3
"""Static code structure & change-scope analyzer (zero runtime % guessing).

Measures exact before/after code shape (cyclomatic branches, max nesting depth,
function length, duplicate blocks, net LOC, companion tests) and identifies
which runtime benchmark axes apply to the PR.
Uses Python 3.9+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple


PROD_EXTENSIONS = {".java", ".ts", ".tsx", ".js", ".jsx", ".sql", ".py", ".go", ".rs"}
FRONTEND_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".css", ".scss"}
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


def strip_comments_and_strings(code: str, ext: str) -> str:
    """Remove comments and string literals so brace/branch counts stay accurate."""
    if ext == ".sql":
        code = re.sub(r"/\*.*?\*/", " ", code, flags=re.S)
        code = re.sub(r"--[^\n]*", " ", code)
        code = re.sub(r"'(?:''|[^'])*'", "''", code)
        return code

    pattern = re.compile(
        r"/\*.*?\*/|//[^\n]*|`(?:\\.|[^`\\])*`|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'",
        flags=re.S,
    )
    return pattern.sub(lambda m: "\n" * m.group(0).count("\n") if m.group(0).startswith("/*") else " ", code)


def measure_code_shape(code: str, ext: str) -> Dict[str, Any]:
    """Compute cyclomatic branches, max nesting depth, LOC, and duplicate blocks."""
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


def classify_file(path: str) -> str:
    """Classify a repo-relative path into runtime/test/non-runtime buckets."""
    p = Path(path)
    ext = p.suffix.lower()
    if TEST_PATH_RE.search(path):
        return "test"
    if DOCS_CONFIG_RE.search(path):
        if path in {"package.json", "frontend/package.json", "pom.xml"}:
            return "build-config"
        return "non-runtime"
    if path.startswith("frontend/") and ext in FRONTEND_EXTENSIONS:
        return "frontend"
    if ext == ".sql":
        return "sql"
    if ext == ".java" and path.startswith("src/main/"):
        return "backend"
    if ext in FRONTEND_EXTENSIONS and path.startswith("src/"):
        return "frontend"
    if ext in PROD_EXTENSIONS:
        return "backend"
    return "non-runtime"


def git_blob(repo_root: str, ref: str, rel_path: str) -> str:
    res = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return res.stdout if res.returncode == 0 else ""


def estimate_fan_in(repo_root: str, rel_path: str) -> int:
    """Count production files referencing this module/class name."""
    stem = Path(rel_path).stem
    if stem in {"index", "types", "utils", "constants", "styles", "App", "main"} or len(stem) < 4:
        return 0
    search_roots = [d for d in ("src/main/java", "frontend/src", "src") if (Path(repo_root) / d).exists()]
    if not search_roots:
        return 0
    rg = shutil.which("rg")
    cmd = (
        [rg, "-l", "--fixed-strings", stem, *search_roots]
        if rg
        else ["git", "grep", "-l", "-F", stem, "--", *search_roots]
    )
    res = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True)
    if res.returncode not in (0, 1):
        return 0
    matches = [m for m in res.stdout.splitlines() if m and m != rel_path and not TEST_PATH_RE.search(m)]
    return len(matches)


def get_changed_files(repo_root: str, base_ref: str, head_ref: str) -> List[Dict[str, Any]]:
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
    """Compute a deterministic 1-10 code structure score from exact static deltas."""
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
        return {"score": 7, "raw_score": 7.0, "direction": "Neutral", "basis": ["No production runtime files changed"]}

    if branch_delta <= -5:
        score += 1.2
        reasons.append(f"Reduced branching ({branch_delta:+d})")
    elif branch_delta < 0:
        score += 0.6
        reasons.append(f"Lower branching ({branch_delta:+d})")
    elif branch_delta > 40:
        score -= 1.4
        reasons.append(f"Large branching increase ({branch_delta:+d})")
    elif branch_delta > 15:
        score -= 0.7
        reasons.append(f"Added branching ({branch_delta:+d})")

    if nesting_delta < 0:
        score += 0.8
        reasons.append(f"Flattened nesting ({nesting_delta:+d})")
    elif nesting_delta >= 2:
        score -= 1.0
        reasons.append(f"Deeper nesting ({nesting_delta:+d} levels)")
    elif nesting_delta == 1:
        score -= 0.4

    if dup_delta < 0:
        score += 0.8
        reasons.append(f"Removed duplicated blocks ({dup_delta:+d})")
    elif dup_delta >= 3:
        score -= 1.0
        reasons.append(f"Added duplicated blocks ({dup_delta:+d})")
    elif dup_delta > 0:
        score -= 0.4

    if avg_func_delta <= -5.0:
        score += 0.7
        reasons.append(f"Shorter functions ({avg_func_delta:+.1f} LOC avg)")
    elif avg_func_delta >= 15.0:
        score -= 0.8
        reasons.append(f"Longer functions ({avg_func_delta:+.1f} LOC avg)")

    if net_loc < -20:
        score += 0.6
        reasons.append(f"Net code reduction ({net_loc:+d} prod LOC)")
    elif net_loc > 600 and prod_count <= 2:
        score -= 0.8
        reasons.append(f"Concentrated addition ({net_loc:+d} LOC in {prod_count} files)")

    if test_count > 0:
        score += 0.6
        reasons.append(f"Includes {test_count} test file(s)")

    clamped = max(1, min(10, int(round(score))))
    direction = "Better" if clamped >= 7 else ("Neutral" if clamped >= 5 else "Worse")

    return {
        "score": clamped,
        "raw_score": round(score, 2),
        "direction": direction,
        "basis": reasons or ["Small, contained change"],
    }


def analyze_diff(repo_root: str, base_ref: str, head_ref: str) -> Dict[str, Any]:
    changed = get_changed_files(repo_root, base_ref, head_ref)
    file_reports: List[Dict[str, Any]] = []

    branches_delta_total = 0
    max_nesting_delta_worst = 0
    dup_delta_total = 0
    net_prod_loc = 0
    avg_func_deltas: List[float] = []
    prod_files = 0
    test_files: List[str] = []
    frontend_prod_files = 0
    backend_prod_files = 0
    sql_files = 0

    for entry in changed:
        path = entry["path"]
        old_path = entry["old_path"]
        ext = Path(path).suffix.lower()
        category = classify_file(path)

        if category == "test":
            test_files.append(path)
            continue
        if category == "non-runtime":
            continue

        prod_files += 1
        if category == "frontend" or path in {"frontend/package.json", "package.json"}:
            frontend_prod_files += 1
        elif category == "sql":
            sql_files += 1
        elif category == "backend" or path == "pom.xml":
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
        file_reports.append(
            {
                "path": path,
                "category": category,
                "fan_in": fan_in,
                "loc_delta": loc_delta,
                "branches_delta": b_delta,
                "max_nesting_delta": n_delta,
                "duplicate_windows_delta": d_delta,
            }
        )

    avg_func_delta = round(sum(avg_func_deltas) / len(avg_func_deltas), 1) if avg_func_deltas else 0.0
    summary = {
        "status": "ok",
        "total_changed_files": len(changed),
        "prod_files_count": prod_files,
        "test_files_count": len(test_files),
        "changed_test_files": test_files,
        "frontend_prod_files": frontend_prod_files,
        "backend_prod_files": backend_prod_files,
        "sql_files": sql_files,
        "totals": {
            "net_prod_loc": net_prod_loc,
            "branches_delta": branches_delta_total,
            "max_nesting_delta": max_nesting_delta_worst,
            "duplicate_windows_delta": dup_delta_total,
            "avg_func_loc_delta": avg_func_delta,
        },
        "top_files": sorted(file_reports, key=lambda r: (r["fan_in"], abs(r["loc_delta"])), reverse=True)[:10],
    }
    summary["structure"] = compute_structure_score(summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze PR structure metrics (no runtime % estimation).")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args()

    result = analyze_diff(args.repo, args.base, args.head)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
