#!/usr/bin/env python3
"""Unit tests for pr-performance-impact v2.0.0 scientific measurement tools."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

import analyze_pr  # noqa: E402
import api_sql_probe  # noqa: E402
import code_metrics  # noqa: E402
import skill_versions  # noqa: E402
import stats_util  # noqa: E402


class StatsUtilTests(unittest.TestCase):
    def test_detects_statistically_significant_improvement(self) -> None:
        base = [100.0, 102.0, 99.0, 101.0, 103.0, 100.5, 99.5]
        pr = [78.0, 80.0, 79.0, 81.0, 77.5, 79.5, 80.5]
        res = stats_util.compare_ab(base, pr, lower_is_better=True)
        self.assertEqual(res["verdict"], "significant_improvement")
        self.assertTrue(res["significant"])
        self.assertGreater(res["improvement_pct"], 15.0)
        self.assertLess(res["p_value"], 0.01)

    def test_detects_statistically_significant_regression(self) -> None:
        base = [50.0, 51.0, 49.0, 50.5, 52.0, 49.5, 50.0]
        pr = [65.0, 66.0, 64.0, 67.0, 65.5, 64.5, 66.5]
        res = stats_util.compare_ab(base, pr, lower_is_better=True)
        self.assertEqual(res["verdict"], "significant_regression")
        self.assertTrue(res["significant"])
        self.assertLess(res["improvement_pct"], -20.0)
        self.assertLess(res["p_value"], 0.01)

    def test_rejects_noisy_overlapping_samples_as_no_significant_change(self) -> None:
        base = [100.0, 105.0, 95.0, 102.0, 98.0, 101.0, 99.0]
        pr = [99.0, 104.0, 96.0, 101.0, 97.0, 103.0, 98.0]
        res = stats_util.compare_ab(base, pr, lower_is_better=True)
        self.assertEqual(res["verdict"], "no_significant_change")
        self.assertFalse(res["significant"])

    def test_handles_insufficient_samples(self) -> None:
        res = stats_util.compare_ab([10.0, 11.0], [5.0, 6.0])
        self.assertEqual(res["verdict"], "insufficient_samples")
        self.assertFalse(res["significant"])


class CodeMetricsTests(unittest.TestCase):
    def test_measure_code_shape_ignores_comments_and_strings(self) -> None:
        code = """
        // if (a && b) { while (true) {} }
        public void run(boolean ok) {
            String msg = "if (x || y) { { { } } }";
            if (ok) {
                for (int i = 0; i < 10; i++) {
                    System.out.println(msg);
                }
            }
        }
        """
        shape = code_metrics.measure_code_shape(code, ".java")
        self.assertEqual(shape["branches"], 2)
        self.assertEqual(shape["max_nesting"], 3)

    def test_classify_file_buckets(self) -> None:
        self.assertEqual(code_metrics.classify_file("frontend/src/screens/Home/index.test.tsx"), "test")
        self.assertEqual(code_metrics.classify_file("docs/README.md"), "non-runtime")
        self.assertEqual(code_metrics.classify_file("frontend/src/app/store.ts"), "frontend")
        self.assertEqual(code_metrics.classify_file("src/main/sql/update-tenant.sql"), "sql")

    def test_structure_score_rewards_simplification_and_tests(self) -> None:
        summary = {
            "prod_files_count": 3,
            "test_files_count": 2,
            "totals": {
                "branches_delta": -8,
                "max_nesting_delta": -1,
                "duplicate_windows_delta": -2,
                "net_prod_loc": -45,
                "avg_func_loc_delta": -8.0,
            },
        }
        res = code_metrics.compute_structure_score(summary)
        self.assertGreaterEqual(res["score"], 9)
        self.assertEqual(res["direction"], "Better")

    def test_structure_score_penalizes_complexity_regression(self) -> None:
        summary = {
            "prod_files_count": 1,
            "test_files_count": 0,
            "totals": {
                "branches_delta": 55,
                "max_nesting_delta": 3,
                "duplicate_windows_delta": 5,
                "net_prod_loc": 700,
                "avg_func_loc_delta": 25.0,
            },
        }
        res = code_metrics.compute_structure_score(summary)
        self.assertLessEqual(res["score"], 3)
        self.assertEqual(res["direction"], "Worse")


class ScientificVerdictTests(unittest.TestCase):
    def test_never_fabricates_percent_when_no_runtime_tool_ran(self) -> None:
        code = {"prod_files_count": 3}
        skipped = {"status": "skipped", "reason": "not available"}
        res = analyze_pr.build_scientific_verdict(code, skipped, skipped, skipped, skipped, skipped)
        self.assertEqual(res["status"], "not_measured")
        self.assertIn("Not measured", res["headline"])
        self.assertEqual(res["measured_axes"], [])

    def test_reports_zero_when_no_runtime_files_changed(self) -> None:
        code = {"prod_files_count": 0}
        skipped = {"status": "skipped"}
        res = analyze_pr.build_scientific_verdict(code, skipped, skipped, skipped, skipped, skipped)
        self.assertEqual(res["status"], "no_runtime_files")
        self.assertTrue(res["headline"].startswith("0%"))

    def test_reports_statistically_flat_when_measurements_within_noise(self) -> None:
        code = {"prod_files_count": 1}
        bundle = {
            "status": "ok",
            "entry_gzip_kb": {"base": 1160.86, "head": 1160.83, "delta_kb": -0.03, "delta_pct": 0.0, "reduction_pct": 0.0},
            "local_browser": {
                "status": "ok",
                "metrics": {
                    "script_duration_ms": {
                        "base": {"n": 7, "median": 120.0},
                        "pr": {"n": 7, "median": 119.0},
                        "improvement_pct": 0.83,
                        "p_value": 0.62,
                        "significant": False,
                    }
                },
            },
        }
        skipped = {"status": "skipped"}
        res = analyze_pr.build_scientific_verdict(code, bundle, skipped, skipped, skipped, skipped)
        self.assertEqual(res["status"], "no_significant_change")
        self.assertIn("bundle", res["measured_axes"])
        self.assertIn("local_browser", res["measured_axes"])

    def test_reports_measured_improvement_from_significant_local_browser_metric(self) -> None:
        code = {"prod_files_count": 2}
        bundle = {
            "status": "ok",
            "entry_gzip_kb": {"base": 500.0, "head": 450.0, "delta_kb": -50.0, "delta_pct": -10.0, "reduction_pct": 10.0},
            "local_browser": {
                "status": "ok",
                "metrics": {
                    "script_duration_ms": {
                        "base": {"n": 7, "median": 200.0},
                        "pr": {"n": 7, "median": 160.0},
                        "improvement_pct": 20.0,
                        "p_value": 0.002,
                        "significant": True,
                    }
                },
            },
        }
        skipped = {"status": "skipped"}
        res = analyze_pr.build_scientific_verdict(code, bundle, skipped, skipped, skipped, skipped)
        self.assertEqual(res["status"], "measured_improvement")
        self.assertIn("+20.00%", res["headline"])


class SkillVersionTests(unittest.TestCase):
    def test_reads_v2_version_file(self) -> None:
        version = skill_versions.read_version(SCRIPTS_DIR.parent)
        self.assertEqual(version, "2.0.0")
        self.assertEqual(skill_versions.parse_version(version), (2, 0, 0))

    def test_check_update_detects_newer_release(self) -> None:
        class FakeResp:
            returncode = 0
            stdout = '{"schema_version": 1, "skills": {"pr-performance-impact": {"version": "2.1.0", "path": "git-and-review-workflow/pr-performance-impact"}}}'

        res = skill_versions.check_update(SCRIPTS_DIR.parent, runner=lambda *a, **kw: FakeResp())
        self.assertEqual(res["status"], "checked")
        self.assertTrue(res["update_available"])

    def test_sql_explain_skips_cleanly_without_dsn(self) -> None:
        res = api_sql_probe.benchmark_sql_explain(None, None, None)
        self.assertEqual(res["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
