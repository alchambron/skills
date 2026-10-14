"""Offline invariants for the report ledger, evidence retention and HTML boundary."""
import copy
import json
from pathlib import Path
import re
import tempfile
import unittest

from render_results import prepare, render, validate


def scenario(identifier="check-1", status="pass"):
    return {"id": identifier, "title": "Export keeps dates", "preconditions": "Disposable runtime", "route": "/invoices", "steps": ["Open invoices", "Export CSV"], "expected": "Original dates", "actual": "Dates unchanged in inspected export", "status": status, "screenshots": [], "exports": [], "cleanup": "Disposable database dropped"}


def ledger():
    return {"version": 1, "login": "reviewer", "collected_at": "2026-10-14T12:00:00Z", "collection": {"complete": True, "warnings": []}, "prs": [{"repository": "Example/Application", "number": 7, "url": "https://github.com/Example/Application/pull/7", "title": "Improve calendar export", "base_sha": "a" * 40, "head_sha": "b" * 40, "current_base_sha": "a" * 40, "current_head_sha": "b" * 40, "review": {"status": "clean", "findings": [], "standards": [], "spec": [], "limits": []}, "publication": {"status": "not_required"}, "live": {"status": "complete", "scope": "pr-head-only", "cleanup": {"database_dropped": True, "servers_stopped": True, "worktree_removed": True}}, "coverage_gaps": [], "scenarios": [scenario()]}]}


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.source = Path(self.temp.name)
        self.output = self.source / "report"
        self.output.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def prepare(self, data):
        return prepare(data, self.source, self.output)

    def test_counts_keep_observed_partial_spec_gaps_separate(self):
        data = ledger()
        statuses = ("pass", "fail", "blocked", "observed", "partial", "spec_gap")
        data["prs"][0]["scenarios"] = [scenario(str(i), status) for i, status in enumerate(statuses)]
        saved = self.prepare(data)
        self.assertEqual(saved["report"]["counts"], dict.fromkeys(statuses, 1))
        self.assertEqual(saved["report"]["non_stale_counts"]["pass"], 1)
        self.assertEqual(data["prs"][0]["scenarios"][0]["screenshots"], [])

    def test_duplicate_ids_rejected_per_pr_but_allowed_between_prs(self):
        data = ledger()
        data["prs"][0]["scenarios"].append(scenario())
        with self.assertRaisesRegex(ValueError, "duplicate scenario id"):
            validate(data)
        data = ledger()
        other = copy.deepcopy(data["prs"][0])
        other["number"] = 8
        data["prs"].append(other)
        validate(data)

    def test_invalid_status_and_missing_required_identity_rejected(self):
        data = ledger()
        data["prs"][0]["scenarios"][0]["status"] = "notes"
        with self.assertRaisesRegex(ValueError, "invalid status"):
            validate(data)
        data = ledger()
        del data["prs"][0]["head_sha"]
        with self.assertRaisesRegex(ValueError, "missing head_sha"):
            validate(data)

    def test_pass_without_actual_is_partial_not_pass(self):
        data = ledger()
        data["prs"][0]["scenarios"][0]["actual"] = None
        result = self.prepare(data)
        self.assertEqual(result["report"]["counts"]["pass"], 0)
        self.assertEqual(result["report"]["counts"]["partial"], 1)
        self.assertEqual(result["prs"][0]["scenarios"][0]["status"], "pass")

    def test_changed_base_or_head_marks_all_outcomes_stale(self):
        for field in ("current_head_sha", "current_base_sha"):
            data = ledger()
            data["prs"][0][field] = "c" * 40
            saved = self.prepare(data)
            self.assertTrue(saved["prs"][0]["stale"])
            self.assertEqual(saved["report"]["counts"]["pass"], 1)
            self.assertEqual(saved["report"]["non_stale_counts"]["pass"], 0)
            self.assertTrue(any("changed" in warning for warning in saved["report"]["warnings"]))

    def test_copy_evidence_and_missing_evidence_remain_explicit(self):
        image = self.source / "inspected.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        export = self.source / "download.csv"
        export.write_text("date,total\n2026-01-01,12\n")
        data = ledger()
        case = data["prs"][0]["scenarios"][0]
        case["screenshots"] = [str(image), "missing.png"]
        case["exports"] = [{"path": "download.csv", "file_name": "Invoices.csv", "verification": "Header and row inspected"}, {"path": "missing.csv", "file_name": "Missing.csv"}]
        saved = self.prepare(data)["prs"][0]["scenarios"][0]
        self.assertEqual(len(saved["screenshots"]), 1)
        for path in (saved["screenshots"][0], saved["exports"][0]["path"]):
            self.assertFalse(Path(path).is_absolute())
            self.assertTrue((self.output / path).is_file())
        image.unlink()
        export.unlink()
        self.assertTrue((self.output / saved["screenshots"][0]).exists())
        self.assertTrue(saved["exports"][1]["missing"])
        self.assertEqual(saved["exports"][1]["path"], "missing.csv")
        self.assertTrue(any("Screenshot unavailable" in gap for gap in saved["evidence_gaps"]))
        self.assertTrue(any("not verified" in gap for gap in saved["evidence_gaps"]))

    def test_verified_publication_needs_saved_github_readback_link(self):
        data = ledger()
        data["prs"][0]["publication"] = {"status": "verified"}
        saved = self.prepare(data)
        self.assertEqual(saved["prs"][0]["publication"]["display_status"], "unverified")
        data["prs"][0]["publication"]["review_url"] = "https://github.com/Example/Application/pull/7#pullrequestreview-123"
        self.assertEqual(self.prepare(data)["prs"][0]["publication"]["display_status"], "verified")

    def test_unavailable_pins_allowed_only_for_explicitly_blocked_candidate(self):
        data = ledger()
        pr = data["prs"][0]
        pr["head_sha"] = None
        del pr["base_sha"]
        pr["review"]["status"] = "blocked"
        pr["scenarios"] = []
        result = self.prepare(data)
        self.assertFalse(result["prs"][0]["revision_available"])
        self.assertFalse(result["prs"][0]["revision_refreshed"])
        self.assertFalse(result["prs"][0]["stale"])
        self.assertEqual(result["report"]["unpinned_pr_count"], 1)
        for invalid_status in ("clean", "issues", "pending"):
            pr["review"]["status"] = invalid_status
            with self.assertRaisesRegex(ValueError, "explicitly blocked"):
                validate(data)

    def test_unpinned_record_cannot_contribute_pass_or_fresh_counts(self):
        data = ledger()
        pr = data["prs"][0]
        pr["head_sha"] = None
        pr["review"]["status"] = "blocked"
        result = self.prepare(data)
        self.assertEqual(result["report"]["counts"]["pass"], 0)
        self.assertEqual(result["report"]["counts"]["blocked"], 1)
        self.assertEqual(sum(result["report"]["non_stale_counts"].values()), 0)

    def test_ghe_publication_permalink_must_match_https_origin_and_same_pr(self):
        data = ledger()
        pr = data["prs"][0]
        pr["url"] = "https://github.example.test/Example/Application/pull/7"
        valid = pr["url"] + "#discussion_r456"
        pr["publication"] = {"status": "reused", "reused_threads": [valid]}
        self.assertEqual(self.prepare(data)["prs"][0]["publication"]["display_status"], "reused")
        for invalid in (pr["url"], "https://github.example.test/Other/Repository/pull/7#discussion_r456", "https://github.example.test/Example/Application/pull/8#discussion_r456", "https://github.com/Example/Application/pull/7#discussion_r456", valid.replace("https:", "http:"), valid.replace("//", "//user:password@")):
            pr["publication"]["reused_threads"] = [invalid]
            self.assertEqual(self.prepare(data)["prs"][0]["publication"]["display_status"], "unverified", invalid)

    def test_inline_links_are_absolute_and_saved_guide_links_are_portable(self):
        data = ledger()
        export = self.source / "download.csv"
        export.write_text("date,total\n2026-01-01,12\n")
        data["prs"][0]["scenarios"][0]["exports"] = [{"file_name": "Report.csv", "path": export.name, "verification": "Contents inspected"}]
        input_path = self.source / "results.json"
        input_path.write_text(json.dumps(data))
        summary = render(input_path, self.output)
        def payload(path):
            return json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>', Path(path).read_text(), re.S)[1])
        full, inline = payload(summary["guide"]), payload(summary["inline"])
        for key in ("guide", "results"):
            self.assertTrue(Path(inline["report_links"][key]).is_absolute())
            self.assertFalse(Path(full["report_links"][key]).is_absolute())
        exported_inline = inline["prs"][0]["scenarios"][0]["exports"][0]["report_url"]
        self.assertTrue(Path(exported_inline).is_file())
        self.assertIn(exported_inline, inline["local_links"])
        self.assertFalse(Path(full["prs"][0]["scenarios"][0]["exports"][0]["report_url"]).is_absolute())

    def test_empty_unstarted_pr_remains_visible_and_not_pass(self):
        data = ledger()
        pr = data["prs"][0]
        pr["scenarios"] = []
        del pr["review"]
        del pr["live"]
        pr["publication"] = {"status": "failed", "error": "Review endpoint unavailable"}
        result = self.prepare(data)
        self.assertEqual(sum(result["report"]["counts"].values()), 0)
        self.assertEqual(result["prs"][0]["review"]["status"], "pending")
        self.assertEqual(result["prs"][0]["publication"]["error"], "Review endpoint unavailable")

    def test_html_boundary_is_text_and_embedded_json_survives_hostile_title(self):
        data = ledger()
        hostile = '</script><img src=x onerror="alert(1)">\u2028&'
        data["prs"][0]["title"] = hostile
        data["prs"][0]["review"] = {"status": "issues", "findings": [{"severity": "P2", "title": hostile, "body": hostile, "origin": "static", "path": "x.ts", "line": 9, "side": "RIGHT"}]}
        input_path = self.source / "results.json"
        input_path.write_text(json.dumps(data))
        summary = render(input_path, self.output)
        guide = Path(summary["guide"]).read_text()
        self.assertNotIn(hostile, guide)
        self.assertNotIn(".innerHTML", guide)
        self.assertIn(".textContent", guide)
        payload = re.search(r'<script id="report-data" type="application/json">(.*?)</script>', guide, re.S)[1]
        self.assertEqual(json.loads(payload)["prs"][0]["title"], hostile)
        self.assertIn("reviewReportSelfCheck", guide)
        self.assertTrue(Path(summary["inline"]).is_file())
        self.assertEqual(summary["pr_count"], 1)

    def test_inline_cap_keeps_all_observations_and_full_guide_evidence(self):
        data = ledger()
        data["prs"][0]["scenarios"] = [scenario(str(i)) for i in range(6)]
        for i, case in enumerate(data["prs"][0]["scenarios"]):
            image = self.source / f"{i}.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]))
            case["screenshots"] = [image.name]
        input_path = self.source / "results.json"
        input_path.write_text(json.dumps(data))
        summary = render(input_path, self.output)
        def payload(path):
            html = Path(path).read_text()
            return json.loads(re.search(r'<script id="report-data" type="application/json">(.*?)</script>', html, re.S)[1])
        full, inline = payload(summary["guide"]), payload(summary["inline"])
        self.assertEqual(sum(len(s["images"]) for s in full["prs"][0]["scenarios"]), 6)
        self.assertEqual(sum(len(s["images"]) for s in inline["prs"][0]["scenarios"]), 4)
        self.assertEqual(len(inline["prs"][0]["scenarios"]), 6)
        self.assertEqual([s["actual"] for s in full["prs"][0]["scenarios"]], [s["actual"] for s in inline["prs"][0]["scenarios"]])

    def test_repeated_screenshot_embedded_once_and_portable_ledger_rerenders(self):
        data = ledger()
        image = self.source / "same.png"
        image.write_bytes(b"\x89PNG\r\n\x1a\n")
        data["prs"][0]["scenarios"] = [scenario(str(i)) for i in range(12)]
        for case in data["prs"][0]["scenarios"]:
            case["screenshots"] = [image.name, "absent.png"]
        input_path = self.source / "results.json"
        input_path.write_text(json.dumps(data))
        summary = render(input_path, self.output)
        image.unlink()
        inline = Path(summary["inline"]).read_text()
        self.assertEqual(inline.count("data:image/png;base64,"), 1)
        second = render(Path(summary["results"]), self.source / "moved-report")
        saved = json.loads(Path(second["results"]).read_text())
        self.assertTrue(any("absent.png" in gap for gap in saved["prs"][0]["scenarios"][0]["evidence_gaps"]))
        self.assertEqual(len(saved["prs"][0]["scenarios"][0]["screenshots"]), 1)
        self.assertTrue((Path(second["results"]).parent / saved["prs"][0]["scenarios"][0]["screenshots"][0]).is_file())


if __name__ == "__main__":
    unittest.main()
