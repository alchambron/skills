"""Offline invariants for the report ledger, evidence retention and HTML boundary."""
import copy
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

from render_results import prepare, render, validate


def scenario(identifier="check-1", status="pass"):
    return {"id": identifier, "title": "Export keeps dates", "preconditions": "Disposable runtime", "route": "/invoices", "steps": ["Open invoices", "Export CSV"], "expected": "Original dates", "actual": "Dates unchanged in inspected export", "status": status, "screenshots": [], "exports": [], "cleanup": "Disposable database dropped"}


def ledger():
    return {"version": 1, "login": "reviewer", "collected_at": "2026-10-14T12:00:00Z", "collection": {"complete": True, "warnings": []}, "prs": [{"repository": "Example/Application", "number": 7, "url": "https://github.com/Example/Application/pull/7", "title": "Improve calendar export", "base_sha": "a" * 40, "head_sha": "b" * 40, "current_base_sha": "a" * 40, "current_head_sha": "b" * 40, "review": {"status": "clean", "findings": [], "standards": [], "spec": [], "limits": []}, "publication": {"status": "not_required"}, "live": {"status": "complete", "scope": "pr-head-only", "runtime_commit": "b" * 40, "environment": "Owned local runtime", "cleanup": {"database_dropped": True, "servers_stopped": True, "worktree_removed": True}}, "coverage_gaps": [], "scenarios": [scenario()]}]}


def mixed_ledger():
    """Synthetic identities deliberately reuse PR numbers and scenario IDs."""
    data = ledger()
    original = data["prs"][0]
    data["prs"] = []
    for repository, status in (("EnerZam/JouleMV", "pass"), ("EnerZam/WebSynco_React_Front", "fail"), ("EnerZam/WebSynco", "blocked"), ("Example/Application", "observed"), ("Elsewhere/JouleMV", "spec_gap")):
        pr = copy.deepcopy(original)
        pr.update(repository=repository, url=f"https://github.com/{repository}/pull/7", title="Synthetic offline test")
        pr["scenarios"] = [scenario(status=status)]
        data["prs"].append(pr)
    return data


# Exercise the embedded JS state without a browser, network, or extra packages.
# This small DOM double checks selection/count behavior; layout needs browser QA.
DOM_RUNNER = r"""
const fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(process.argv[1],'utf8');
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.listeners={};this._text='';this._value=null}
  append(...children){this.children.push(...children)}
  replaceChildren(...children){this.children=children;this._text='';if(this.tagName==='select')this._value=null}
  setAttribute(key,value){this[key]=value}
  addEventListener(key,callback){this.listeners[key]=callback}
  get textContent(){return this._text+this.children.map(child=>child.textContent).join(' ')}
  set textContent(value){this._text=String(value);this.children=[]}
  get options(){return this.children.flatMap(child=>child.tagName==='option'?[child]:child.options)}
  get value(){return this._value===null?(this.tagName==='select'?(this.options[0]?.value||''):''):this._value}
  set value(value){this._value=String(value)}
}
const elements={};
for(const match of html.matchAll(/<([a-z]+)[^>]*\bid="([^"]+)"[^>]*>/g))elements[match[2]]=new Element(match[1]);
elements['report-data'].textContent=html.match(/<script id="report-data" type="application\/json">(.*?)<\/script>/s)[1];
const document={getElementById:id=>elements[id],createElement:tag=>new Element(tag),documentElement:{scrollWidth:800}};
const window={innerWidth:1000};
vm.runInNewContext(html.match(/<script>\s*('use strict';.*?)<\/script>/s)[1],{document,window});
function snapshot(){return {...window.reviewReportSelfCheck(),actions:elements.actions.textContent,overview:elements.overview.textContent,detail:elements['pr-detail'].textContent,scenarioDetail:elements.detail.textContent,selectionHeading:elements['selection-heading'].textContent,batchSummary:elements['batch-summary'].textContent,revisionWarning:elements['revision-warning'].textContent,prOptions:elements.pr.options.map(option=>({value:option.value,label:option.textContent})),projectOptions:elements.project.options.map(option=>({value:option.value,label:option.textContent})),prDisabled:!!elements.pr.disabled,scenarioDisabled:!!elements.scenario.disabled}}
const states=[snapshot()];
for(const action of JSON.parse(process.argv[2])){elements[action.id].value=action.value;elements[action.id].listeners.change();states.push(snapshot())}
process.stdout.write(JSON.stringify(states));
"""


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

    def render(self, data):
        input_path = self.source / "results.json"
        input_path.write_text(json.dumps(data))
        return render(input_path, self.output)

    def interact(self, data, actions):
        if not shutil.which("node"):
            self.skipTest("Node required for offline embedded-JS state checks")
        summary = self.render(data)
        result = subprocess.run(["node", "-e", DOM_RUNNER, summary["guide"], json.dumps(actions)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_old_ledger_infers_profiles_and_ignores_conflicting_hints(self):
        data = mixed_ledger()
        data["prs"][0]["repository"] = "enerzam/jOuLeMv"
        data["prs"][0].update(project_type="websynco-react-front", project_label="Wrong project")
        saved = self.prepare(data)
        self.assertEqual([pr["project_type"] for pr in saved["prs"]], ["joulemv", "websynco-react-front", "websynco-backend", "other", "other"])
        self.assertEqual(saved["prs"][0]["project_label"], "JouleMV")
        self.assertEqual(saved["prs"][3]["project_label"], "Repository-specific")
        self.assertEqual(saved["prs"][0]["repository"], "enerzam/jOuLeMv")
        self.assertEqual(len(saved["report"]["projects"]), 5)
        self.assertNotIn("project_type", data["prs"][1])

    def test_group_counts_partition_batch_and_keep_revision_evidence(self):
        data = mixed_ledger()
        data["prs"][0]["current_head_sha"] = "c" * 40
        data["prs"][1]["scenarios"][0]["status"] = "pass"
        data["prs"][1]["scenarios"][0]["actual"] = None
        data["prs"][2]["head_sha"] = None
        data["prs"][2]["review"]["status"] = "blocked"
        report = self.prepare(data)["report"]
        self.assertEqual(report["pr_count"], 5)
        self.assertEqual(report["scenario_count"], 5)
        self.assertEqual(report["counts"]["pass"], 1)
        self.assertEqual(report["counts"]["partial"], 1)
        self.assertEqual(report["non_stale_counts"]["pass"], 0)
        for status, count in report["counts"].items():
            self.assertEqual(sum(group["counts"][status] for group in report["projects"]), count)
        joule, front, backend, first_other, second_other = report["projects"]
        self.assertEqual(joule["stale_pr_count"], 1)
        self.assertEqual(front["counts"]["partial"], 1)
        self.assertEqual(backend["unpinned_pr_count"], 1)
        self.assertEqual(sum(backend["non_stale_counts"].values()), 0)
        self.assertNotEqual(first_other["id"], second_other["id"])
        for index, project in enumerate(report["projects"]):
            self.assertEqual(project["pr_indexes"], [index])
            self.assertEqual(project["repositories"][0]["counts"], project["counts"])

    def test_project_filter_keeps_colliding_pr_and_scenario_identities_distinct(self):
        data = mixed_ledger()
        for pr in data["prs"]:
            pr["scenarios"][0]["actual"] = pr["repository"] + " synthetic observation"
        initial, front, joule, batch = self.interact(data, [
            {"id": "project", "value": "websynco-react-front"},
            {"id": "project", "value": "joulemv"},
            {"id": "project", "value": ""},
        ])
        self.assertEqual(initial["prCount"], 5)
        self.assertEqual(len(initial["prOptions"]), 5)
        self.assertEqual(len({option["value"] for option in initial["prOptions"]}), 5)
        self.assertEqual(front["visiblePRCount"], 1)
        self.assertEqual(front["selectedPRIdentity"], {"repository": "EnerZam/WebSynco_React_Front", "number": 7})
        self.assertEqual(front["selectedScenarioIdentity"], {"repository": "EnerZam/WebSynco_React_Front", "number": 7, "id": "check-1"})
        self.assertIn("EnerZam/WebSynco_React_Front synthetic observation", front["scenarioDetail"])
        self.assertNotIn("EnerZam/JouleMV", front["overview"])
        self.assertEqual(front["selectedCounts"]["fail"], 1)
        self.assertEqual(front["counts"]["pass"], 1)
        self.assertIn("Selected project", front["selectionHeading"])
        self.assertIn("5 PR(s)", front["batchSummary"])
        self.assertEqual(joule["selectedPRIdentity"]["repository"], "EnerZam/JouleMV")
        self.assertEqual(batch["visiblePRCount"], 5)
        self.assertEqual(batch["selectedCounts"], initial["counts"])

    def test_selection_survives_project_filter_when_pr_still_visible(self):
        data = mixed_ledger()
        data["prs"][1]["scenarios"].append(scenario("second"))
        states = self.interact(data, [{"id": "pr", "value": "1"}, {"id": "scenario", "value": "1"}, {"id": "project", "value": "websynco-react-front"}, {"id": "project", "value": ""}])
        for state in states[-2:]:
            self.assertEqual(state["selectedPR"], "1")
            self.assertEqual(state["selectedScenarioIdentity"]["id"], "second")

    def test_unknown_repositories_filter_independently_and_metadata_is_text(self):
        data = mixed_ledger()
        hostile = '</script><img src=x onerror="alert(1)">\u2028&'
        unknown = data["prs"][3]
        unknown["repository"] = "Example/" + hostile
        unknown.update(project_type="joulemv", project_label=hostile)
        unknown["runtime_provenance"] = {"frontend": {"repository": unknown["repository"], "sha": "b" * 40}, "schema_mode": "current", "database": {"source_identity": hostile, "owned_clone_identity": "synthetic-owned-clone"}}
        project_id = "other:" + unknown["repository"].casefold()
        _, state, elsewhere = self.interact(data, [{"id": "project", "value": project_id}, {"id": "project", "value": "other:elsewhere/joulemv"}])
        self.assertEqual(state["visiblePRCount"], 1)
        self.assertEqual(state["selectedPRIdentity"]["repository"], unknown["repository"])
        self.assertIn("Runtime provenance", state["detail"])
        self.assertIn("synthetic-owned-clone", state["detail"])
        self.assertIn(hostile, state["overview"])
        self.assertNotIn("Elsewhere/JouleMV", state["overview"])
        self.assertEqual(elsewhere["selectedCounts"]["spec_gap"], 1)
        self.assertEqual(elsewhere["selectedPRIdentity"]["repository"], "Elsewhere/JouleMV")
        guide = (self.output / "scenario-guide.html").read_text()
        self.assertNotIn(hostile, guide)
        saved = json.loads((self.output / "results.json").read_text())
        self.assertEqual(saved["prs"][3]["runtime_provenance"], unknown["runtime_provenance"])
        self.assertEqual(saved["prs"][3]["project_type"], "other")

    def test_blocked_project_without_scenarios_and_empty_batch(self):
        data = mixed_ledger()
        backend = data["prs"][2]
        backend.update(head_sha=None, base_sha=None, scenarios=[])
        backend["review"]["status"] = "blocked"
        backend["live"]["status"] = "blocked"
        _, state = self.interact(data, [{"id": "project", "value": "websynco-backend"}])
        self.assertEqual(state["visiblePRCount"], 1)
        self.assertEqual(state["visibleScenarioCount"], 0)
        self.assertEqual(sum(state["selectedCounts"].values()), 0)
        self.assertEqual(state["selectedUnpinnedPRCount"], 1)
        self.assertIn("Selected project: 0 PR(s) changed revision; 1 PR(s) have unavailable pins", state["revisionWarning"])
        self.assertTrue(state["scenarioDisabled"])
        self.assertIn("Live status: blocked", state["scenarioDetail"])
        self.assertIsNone(state["selectedScenarioIdentity"])
        data["prs"] = []
        empty = self.interact(data, [])[0]
        self.assertEqual(empty["projectOptions"], [{"value": "", "label": "Entire batch · all projects"}])
        self.assertEqual(empty["visiblePRCount"], 0)
        self.assertIsNone(empty["selectedPRIdentity"])
        self.assertTrue(empty["prDisabled"])
        self.assertTrue(empty["scenarioDisabled"])
        self.assertIn("No PRs", empty["overview"])

    def test_multiple_prs_in_repository_share_group_without_merging_scenarios(self):
        data = ledger()
        other = copy.deepcopy(data["prs"][0])
        other["number"] = 8
        data["prs"].append(other)
        project = self.prepare(data)["report"]["projects"][0]
        self.assertEqual(project["pr_indexes"], [0, 1])
        self.assertEqual(project["scenario_count"], 2)
        self.assertEqual(len(project["repositories"]), 1)
        self.assertEqual(project["repositories"][0]["pr_count"], 2)

    def test_archived_cleanup_and_closed_targets_keep_recovery_visible(self):
        data = ledger()
        pr = data["prs"][0]
        pr["pending_cleanup"] = [{"head_sha": "c" * 40, "live": {"cleanup": {"servers_stopped": False}}}]
        result = self.prepare(data)["prs"][0]
        self.assertIn("cleanup", [action["kind"] for action in result["next_actions"]])
        self.assertNotIn("complete", [action["kind"] for action in result["next_actions"]])
        pr["pr_state"] = "closed"
        pr["publication"] = {"status": "pending"}
        result = self.prepare(data)["prs"][0]
        self.assertEqual([action["kind"] for action in result["next_actions"]], ["closed", "cleanup"])

    def test_project_filter_preserves_unknown_freshness_and_earlier_cleanup_actions(self):
        data = mixed_ledger()
        pr = data["prs"][0]
        del pr["current_head_sha"]
        pr["pending_cleanup"] = [{"head_sha": "c" * 40, "live": {"cleanup": {"servers_stopped": False}}}]
        pr["revision_history"] = [{"head_sha": "c" * 40, "review": {"status": "clean"}}]
        initial, joule, front = self.interact(data, [
            {"id": "project", "value": "joulemv"},
            {"id": "project", "value": "websynco-react-front"},
        ])
        self.assertEqual(joule["selectedUnknownPRCount"], 1)
        self.assertEqual(joule["selectedCurrentCounts"]["pass"], 0)
        self.assertEqual(joule["selectedCounts"]["pass"], 1)
        self.assertIn("Refresh revision evidence", joule["actions"])
        self.assertIn("Verify owned resource cleanup", joule["actions"])
        self.assertIn("Outstanding cleanup from earlier revisions", joule["detail"])
        self.assertIn("Earlier revision evidence", joule["detail"])
        self.assertNotIn("EnerZam/JouleMV", front["actions"])
        self.assertEqual(front["selectedUnknownPRCount"], 0)
        self.assertEqual(front["selectedCurrentCounts"]["fail"], 1)
        self.assertIn("Address verified findings", front["actions"])
        self.assertIn("EnerZam/JouleMV", initial["actions"])

    def test_closed_project_keeps_provenance_and_cleanup_without_publication_action(self):
        data = mixed_ledger()
        pr = data["prs"][0]
        pr["pr_state"] = "closed"
        pr["publication"]["status"] = "pending"
        pr["pending_cleanup"] = [{"head_sha": "c" * 40, "live": {"cleanup": {"servers_stopped": False}}}]
        pr["runtime_provenance"] = {"frontend": {"repository": pr["repository"], "sha": pr["head_sha"]}}
        _, joule = self.interact(data, [{"id": "project", "value": "joulemv"}])
        self.assertIn("PR closed", joule["actions"])
        self.assertIn("Verify owned resource cleanup", joule["actions"])
        self.assertNotIn("Complete publication", joule["actions"])
        self.assertIn("Runtime provenance", joule["detail"])

    def test_failure_at_other_runtime_is_not_a_verified_current_finding(self):
        data = ledger()
        pr = data["prs"][0]
        pr["live"]["runtime_commit"] = "c" * 40
        pr["scenarios"][0]["status"] = "fail"
        result = self.prepare(data)
        saved = result["prs"][0]
        self.assertEqual(saved["scenarios"][0]["status"], "fail")
        self.assertEqual(saved["scenarios"][0]["display_status"], "partial")
        self.assertEqual(result["report"]["current_counts"]["fail"], 0)
        self.assertNotIn("fixes", [action["kind"] for action in saved["next_actions"]])

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

    def test_missing_refresh_is_unknown_and_excluded_from_current_counts(self):
        for keys in (("current_head_sha",), ("current_base_sha",), ("current_head_sha", "current_base_sha")):
            data = ledger()
            for key in keys:
                del data["prs"][0][key]
            saved = self.prepare(data)
            self.assertEqual(saved["prs"][0]["freshness"], "unknown")
            self.assertEqual(saved["report"]["counts"]["pass"], 1)
            self.assertEqual(saved["report"]["current_counts"]["pass"], 0)
            self.assertEqual(saved["report"]["non_stale_counts"]["pass"], 0)
            self.assertEqual(saved["report"]["unknown_pr_count"], 1)
            self.assertIn("refresh", [a["kind"] for a in saved["prs"][0]["next_actions"]])

    def test_legacy_complete_without_runtime_evidence_is_displayed_as_partial(self):
        data = ledger()
        data["prs"][0]["live"] = {"status": "complete", "scope": "pr-head-only"}
        saved = self.prepare(data)
        pr = saved["prs"][0]
        self.assertEqual(pr["live"]["status"], "complete")
        self.assertEqual(pr["live"]["display_status"], "incomplete")
        self.assertEqual(pr["scenarios"][0]["status"], "pass")
        self.assertEqual(pr["scenarios"][0]["display_status"], "partial")
        self.assertEqual(saved["report"]["current_counts"]["pass"], 0)
        self.assertEqual({a["kind"] for a in pr["next_actions"]}, {"testing", "cleanup"})

    def test_mismatched_commit_and_integration_parents_cannot_establish_pass(self):
        for updates in ({"runtime_commit": "c" * 40}, {"scope": "integration", "runtime_commit": "c" * 40}, {"scope": "integration", "runtime_commit": "c" * 40, "integration_parents": ["a" * 40, "d" * 40]}):
            data = ledger()
            data["prs"][0]["live"].update(updates)
            saved = self.prepare(data)
            self.assertEqual(saved["report"]["current_counts"]["pass"], 0)
            self.assertEqual(saved["prs"][0]["live"]["display_status"], "incomplete")
        data["prs"][0]["live"]["integration_parents"] = ["a" * 40, "b" * 40]
        self.assertEqual(self.prepare(data)["report"]["current_counts"]["pass"], 1)

    def test_complete_requires_executed_acceptance_scenarios(self):
        for scenarios in ([], [scenario(status="blocked")], [scenario(status="observed")], [scenario(status="partial")], [scenario(status="spec_gap")]):
            data = ledger()
            data["prs"][0]["scenarios"] = scenarios
            pr = self.prepare(data)["prs"][0]
            self.assertEqual(pr["live"]["display_status"], "incomplete")
            self.assertNotIn("complete", [a["kind"] for a in pr["next_actions"]])

    def test_cleanup_gap_does_not_erase_observed_pass(self):
        for receipt in (None, {}, {"database_dropped": True, "servers_stopped": False, "worktree_removed": None}):
            data = ledger()
            data["prs"][0]["live"]["cleanup"] = receipt
            pr = self.prepare(data)["prs"][0]
            self.assertEqual(pr["live"]["display_status"], "complete")
            self.assertEqual(pr["scenarios"][0]["display_status"], "pass")
            self.assertEqual(pr["live"]["cleanup_status"], "pending")
            self.assertEqual([a["kind"] for a in pr["next_actions"]], ["cleanup"])

    def test_cleanup_exemption_requires_technical_scope_and_reason(self):
        data = ledger()
        live = data["prs"][0]["live"]
        del live["cleanup"]
        live["cleanup_required"] = False
        self.assertEqual(self.prepare(data)["prs"][0]["live"]["cleanup_status"], "pending")
        live["scope"] = "technical"
        self.assertEqual(self.prepare(data)["prs"][0]["live"]["cleanup_status"], "pending")
        live["cleanup_reason"] = "Read-only source checks; no runtime resources created"
        self.assertEqual(self.prepare(data)["prs"][0]["live"]["cleanup_status"], "not_required")

    def test_action_summary_keeps_simultaneous_findings_refresh_and_blockers(self):
        data = ledger()
        pr = data["prs"][0]
        pr["current_head_sha"] = "c" * 40
        pr["review"]["status"] = "issues"
        pr["publication"]["status"] = "failed"
        pr["live"]["status"] = "blocked"
        pr["live"]["cleanup"]["servers_stopped"] = None
        self.assertEqual({a["kind"] for a in self.prepare(data)["prs"][0]["next_actions"]}, {"refresh", "fixes", "publication", "blocked", "cleanup"})

    def test_complete_action_requires_complete_recorded_scope(self):
        pr = self.prepare(ledger())["prs"][0]
        self.assertEqual([a["kind"] for a in pr["next_actions"]], ["complete"])
        self.assertIn("not approval", pr["next_actions"][0]["label"])

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
