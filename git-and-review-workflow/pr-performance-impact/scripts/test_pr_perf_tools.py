#!/usr/bin/env python3
"""Unit tests for the pr-performance-impact skill measurement scripts."""

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

    def test_detects_db_calls_inside_loops(self) -> None:
        bad_java = """
        public void sync(List<Long> ids) {
            for (Long id : ids) {
                meterRepository.findById(id);
            }
        }
        """
        good_java = """
        public void sync(List<Long> ids) {
            meterRepository.findAllById(ids);
        }
        """
        self.assertEqual(code_metrics.count_db_calls_in_loops(bad_java), 1)
        self.assertEqual(code_metrics.count_db_calls_in_loops(good_java), 0)

    def test_classify_file_weights(self) -> None:
        self.assertEqual(code_metrics.classify_file("frontend/src/screens/Home/index.test.tsx"), ("test", 0.0))
        self.assertEqual(code_metrics.classify_file("docs/README.md"), ("non-runtime", 0.0))
        self.assertEqual(code_metrics.classify_file("frontend/src/app/store.ts"), ("global", 1.0))
        self.assertEqual(code_metrics.classify_file("frontend/src/screens/Building/index.tsx"), ("core-screen", 0.35))

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


class ApiSqlProbeTests(unittest.TestCase):
    def test_detects_unfiltered_nls_res_join(self) -> None:
        bad_sql = """
        SELECT b.id, bn.name
        FROM building b
        JOIN building_nls_res bn ON bn.building_id = b.id
        WHERE b.active = true;
        """
        good_sql = """
        SELECT b.id, bn.name
        FROM building b
        JOIN building_nls_res bn ON bn.building_id = b.id AND bn.locale = :locale
        WHERE b.active = true;
        """
        self.assertEqual(api_sql_probe.count_unfiltered_nls_res(bad_sql), 1)
        self.assertEqual(api_sql_probe.count_unfiltered_nls_res(good_sql), 0)

    def test_percentile_math(self) -> None:
        self.assertEqual(api_sql_probe.percentile([10.0, 20.0, 30.0, 40.0, 100.0], 95), 100.0)
        self.assertEqual(api_sql_probe.percentile([], 95), 0.0)


class AnalyzePrSynthesisTests(unittest.TestCase):
    def test_discover_preview_urls_uses_configured_regex(self) -> None:
        comments = [
            {"body": "Preview ready at https://pr-1910.preview.example.com"},
        ]
        web, api = analyze_pr.discover_preview_urls(
            comments,
            host_regex=r"https://([a-z0-9-]+)\.preview\.example\.com",
        )
        self.assertEqual(web, "https://pr-1910.preview.example.com")
        self.assertIsNone(api)

    def test_synthesize_non_runtime_is_zero_percent_high_confidence(self) -> None:
        code = {"prod_files_count": 0, "max_app_weight": 0.0, "pattern_deltas": {}}
        res = analyze_pr.synthesize_impact(code, {"status": "skipped"}, {"status": "skipped"}, {"status": "skipped"}, {"status": "skipped"})
        self.assertEqual(res["overall_app_label"], "~0%")
        self.assertEqual(res["confidence"], "High")

    def test_synthesize_combines_bundle_and_browser_measurements(self) -> None:
        code = {"prod_files_count": 2, "max_app_weight": 0.6, "pattern_deltas": {"lazy_imports": 1}}
        bundle = {
            "status": "ok",
            "entry_gzip_kb": {"base": 300.0, "head": 270.0, "delta_kb": -30.0, "delta_pct": -10.0, "reduction_pct": 10.0},
            "total_js_gzip_kb": {"base": 900.0, "head": 855.0, "delta_kb": -45.0, "delta_pct": -5.0, "reduction_pct": 5.0},
        }
        browser = {"status": "ok", "median_runtime_improvement_pct": 12.0, "routes": {"/": {}}}
        res = analyze_pr.synthesize_impact(code, {"status": "skipped"}, bundle, browser, {"status": "skipped"})
        self.assertGreater(res["overall_app_pct_low"], 0.0)
        self.assertEqual(res["confidence"], "High")
        self.assertIn("measured", res["basis"])

    def test_synthesize_negligible_bundle_delta_reports_zero_percent(self) -> None:
        code = {"prod_files_count": 1, "max_app_weight": 0.35, "pattern_deltas": {}}
        bundle = {
            "status": "ok",
            "entry_gzip_kb": {"base": 1160.86, "head": 1160.83, "delta_kb": -0.03, "delta_pct": 0.0, "reduction_pct": 0.0},
            "total_js_gzip_kb": {"base": 2500.0, "head": 2499.9, "delta_kb": -0.1, "delta_pct": 0.0, "reduction_pct": 0.0},
        }
        res = analyze_pr.synthesize_impact(code, {"status": "skipped"}, bundle, {"status": "skipped"}, {"status": "skipped"})
        self.assertEqual(res["overall_app_label"], "~0%")
        self.assertEqual(res["confidence"], "Medium")


class SkillVersionTests(unittest.TestCase):
    def test_reads_canonical_version_file(self) -> None:
        skill_dir = SCRIPTS_DIR.parent
        version = skill_versions.read_version(skill_dir)
        self.assertEqual(version, "1.0.0")
        self.assertEqual(skill_versions.parse_version(version), (1, 0, 0))

    def test_check_update_detects_newer_release(self) -> None:
        class FakeResp:
            returncode = 0
            stdout = '{"schema_version": 1, "skills": {"pr-performance-impact": {"version": "1.1.0", "path": "git-and-review-workflow/pr-performance-impact"}}}'

        res = skill_versions.check_update(SCRIPTS_DIR.parent, runner=lambda *a, **kw: FakeResp())
        self.assertEqual(res["status"], "checked")
        self.assertTrue(res["update_available"])
        self.assertEqual(res["latest_version"], "1.1.0")
        self.assertIn("1.1.0", res["update_message"])

    def test_check_update_handles_unavailable_catalog(self) -> None:
        class FailResp:
            returncode = 1
            stdout = ""

        res = skill_versions.check_update(SCRIPTS_DIR.parent, runner=lambda *a, **kw: FailResp())
        self.assertEqual(res["status"], "unavailable")
        self.assertFalse(res["update_available"])


if __name__ == "__main__":
    unittest.main()
