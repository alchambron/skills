#!/usr/bin/env python3
"""Interleaved local unit-test execution benchmarker (Vitest / Maven JUnit).

Benchmarks shared unit tests that exist on BOTH PR base and head inside
ephemeral detached git worktrees, comparing execution latency with warmup
discard and Mann-Whitney U significance testing.
Uses Python 3.9+ stdlib only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import stats_util  # noqa: E402


def git(repo: str, args: List[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check)


def blob_exists(repo: str, ref: str, rel_path: str) -> bool:
    return git(repo, ["cat-file", "-e", f"{ref}:{rel_path}"], check=False).returncode == 0


def find_frontend_node_modules(repo: str, subdir: str) -> Optional[Path]:
    candidates = [Path(repo) / subdir / "node_modules" if subdir else Path(repo) / "node_modules"]
    res = git(repo, ["rev-parse", "--git-common-dir"], check=False)
    if res.returncode == 0 and res.stdout.strip():
        main_repo = (Path(repo) / res.stdout.strip()).resolve().parent
        candidates.append(main_repo / subdir / "node_modules" if subdir else main_repo / "node_modules")
    for cand in candidates:
        if (cand / ".bin" / "vitest").exists():
            return cand
    return None


def _run_vitest_once(fe_dir: Path, vitest_bin: Path, rel_tests: List[str]) -> Tuple[bool, float]:
    t0 = time.perf_counter()
    res = subprocess.run(
        [str(vitest_bin), "run", "--reporter=dot", *rel_tests],
        cwd=str(fe_dir),
        capture_output=True,
        text=True,
        env={**os.environ, "VITE_DISABLE_STORYBOOK": "1"},
        timeout=120,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    return res.returncode == 0, elapsed_ms


def benchmark_vitest(
    repo: str,
    base_ref: str,
    head_ref: str,
    test_paths: List[str],
    runs: int = 5,
) -> Dict[str, Any]:
    subdir = "frontend" if (Path(repo) / "frontend" / "package.json").exists() else ""
    prefix = f"{subdir}/" if subdir else ""
    shared_tests = [
        p[len(prefix) :]
        for p in test_paths
        if p.startswith(prefix)
        and (p.endswith(".test.ts") or p.endswith(".test.tsx") or p.endswith(".spec.ts") or p.endswith(".spec.tsx"))
        and ".stories." not in p
        and blob_exists(repo, base_ref, p)
        and blob_exists(repo, head_ref, p)
    ][:5]

    if not shared_tests:
        return {"status": "skipped", "reason": "No shared modified Vitest files exist on both base and head"}

    nm = find_frontend_node_modules(repo, subdir)
    if not nm:
        return {"status": "skipped", "reason": "Vitest binary not found in node_modules"}
    vitest_bin = nm / ".bin" / "vitest"

    tmp_root = Path(tempfile.mkdtemp(prefix="pr-perf-vitest-"))
    base_wt = tmp_root / "base"
    head_wt = tmp_root / "head"
    added: List[Path] = []
    try:
        for wt, ref in ((base_wt, base_ref), (head_wt, head_ref)):
            git(repo, ["worktree", "add", "--detach", "--no-checkout", str(wt), ref])
            added.append(wt)
            if subdir:
                git(str(wt), ["sparse-checkout", "init", "--cone"])
                git(str(wt), ["sparse-checkout", "set", subdir])
            git(str(wt), ["checkout", "--force", ref])
            fe_dir = wt / subdir if subdir else wt
            if not (fe_dir / "node_modules").exists():
                os.symlink(str(nm), str(fe_dir / "node_modules"))

        base_fe = base_wt / subdir if subdir else base_wt
        head_fe = head_wt / subdir if subdir else head_wt

        # 1 warmup on each.
        ok_b, _ = _run_vitest_once(base_fe, vitest_bin, shared_tests)
        ok_h, _ = _run_vitest_once(head_fe, vitest_bin, shared_tests)
        if not ok_b or not ok_h:
            return {"status": "skipped", "reason": "Shared Vitest benchmark files did not pass on both base and head"}

        b_ms: List[float] = []
        h_ms: List[float] = []
        for i in range(runs):
            if i % 2 == 0:
                ok1, t1 = _run_vitest_once(base_fe, vitest_bin, shared_tests)
                ok2, t2 = _run_vitest_once(head_fe, vitest_bin, shared_tests)
                if ok1:
                    b_ms.append(t1)
                if ok2:
                    h_ms.append(t2)
            else:
                ok2, t2 = _run_vitest_once(head_fe, vitest_bin, shared_tests)
                ok1, t1 = _run_vitest_once(base_fe, vitest_bin, shared_tests)
                if ok1:
                    b_ms.append(t1)
                if ok2:
                    h_ms.append(t2)

        return {
            "status": "ok",
            "runner": "vitest",
            "tests": shared_tests,
            "runs": runs,
            "duration_ms": stats_util.compare_ab(b_ms, h_ms, lower_is_better=True),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "reason": f"Vitest benchmark error: {str(exc)[:240]}"}
    finally:
        for wt in added:
            git(repo, ["worktree", "remove", "--force", str(wt)], check=False)
        shutil.rmtree(tmp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark shared Vitest unit tests across PR base vs head.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--tests", nargs="*", default=[])
    parser.add_argument("--runs", type=int, default=5)
    args = parser.parse_args()

    res = benchmark_vitest(args.repo, args.base, args.head, args.tests, runs=args.runs)
    json.dump(res, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
