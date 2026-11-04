#!/usr/bin/env python3
"""Render the saved review ledger without network access or source changes."""
import argparse
import base64
import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys
from urllib.parse import urlsplit

from project_profiles import project_profile

SCENARIO_STATUSES = ("pass", "fail", "blocked", "observed", "partial", "spec_gap")
SECTION_STATUSES = {
    "review": ("pending", "clean", "issues", "blocked"),
    "publication": ("pending", "verified", "reused", "failed", "not_requested", "not_required"),
    "live": ("pending", "complete", "blocked", "not_required"),
}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(data):
    require(isinstance(data, dict) and data.get("version") == 1, "Expected an object with version: 1")
    for key in ("login", "collected_at"):
        require(isinstance(data.get(key), str) and data[key].strip(), f"Missing {key}")
    require(isinstance(data.get("prs"), list), "prs must be an array")
    identities = set()
    for pr in data["prs"]:
        require(isinstance(pr, dict), "Every PR must be an object")
        for key in ("repository", "url", "title"):
            require(isinstance(pr.get(key), str) and pr[key].strip(), f"PR missing {key}")
        for key in ("base_sha", "head_sha"):
            if not pr.get(key):
                require(isinstance(pr.get("review"), dict) and pr["review"].get("status") == "blocked", f"PR missing {key}; unavailable pins require an explicitly blocked review")
        require(isinstance(pr.get("number"), int) and not isinstance(pr["number"], bool) and pr["number"] > 0, "PR number must be positive")
        for key in ("base_sha", "head_sha", "current_base_sha", "current_head_sha"):
            if key in pr and pr[key] is not None:
                require(isinstance(pr[key], str) and re.fullmatch(r"[0-9a-fA-F]{40}", pr[key]), f"PR #{pr['number']}: {key} must be a full SHA")
        identity = (pr["repository"], pr["number"])
        require(identity not in identities, f"Duplicate PR {identity}")
        identities.add(identity)
        for section, statuses in SECTION_STATUSES.items():
            if section in pr:
                require(isinstance(pr[section], dict) and pr[section].get("status") in statuses, f"PR #{pr['number']}: invalid {section} status")
        for key in ("findings", "standards", "spec", "limits"):
            require(isinstance(pr.get("review", {}).get(key, []), list), f"review.{key} must be an array")
        publication = pr.get("publication", {})
        require(isinstance(publication.get("reused_threads", []), list), "publication.reused_threads must be an array of URLs")
        require(all(isinstance(url, str) for url in publication.get("reused_threads", [])), "publication.reused_threads must contain URL strings")
        require(isinstance(pr.get("coverage_gaps", []), list), "coverage_gaps must be an array")
        require(isinstance(pr.get("scenarios"), list), f"PR #{pr['number']}: scenarios must be an array (empty is allowed)")
        ids = set()
        for scenario in pr["scenarios"]:
            require(isinstance(scenario, dict), "Every scenario must be an object")
            for key in ("id", "title", "route"):
                require(isinstance(scenario.get(key), str), f"Scenario missing {key}")
            require(bool(scenario["id"].strip()), "Scenario id must not be empty")
            require(scenario["id"] not in ids, f"PR #{pr['number']}: duplicate scenario id {scenario['id']}")
            ids.add(scenario["id"])
            require(scenario.get("status") in SCENARIO_STATUSES, f"Scenario {scenario['id']}: invalid status")
            for key in ("preconditions", "expected", "actual"):
                require(key in scenario, f"Scenario {scenario['id']}: missing {key}")
            require(isinstance(scenario.get("steps"), list), f"Scenario {scenario['id']}: steps must be an array")
            require(isinstance(scenario.get("screenshots", []), list), "screenshots must be an array")
            require(all(isinstance(p, str) for p in scenario.get("screenshots", [])), "screenshots must contain local path strings")
            require(isinstance(scenario.get("exports", []), list), "exports must be an array")
            for export in scenario.get("exports", []):
                require(isinstance(export, dict) and isinstance(export.get("path"), str) and isinstance(export.get("file_name"), str), "Export requires path and file_name")


def meaningful(value):
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None and value != "" and value != [] and value != {}


def outcome_summary(prs):
    counts = {key: 0 for key in SCENARIO_STATUSES}
    non_stale_counts = counts.copy()
    for pr in prs:
        for scenario in pr["scenarios"]:
            counts[scenario["display_status"]] += 1
            if pr["freshness"] == "current":
                non_stale_counts[scenario["display_status"]] += 1
    return {
        "pr_count": len(prs), "scenario_count": sum(counts.values()),
        "counts": counts, "current_counts": non_stale_counts, "non_stale_counts": non_stale_counts,
        "stale_pr_count": sum(bool(pr["stale"]) for pr in prs),
        "unknown_pr_count": sum(pr["freshness"] == "unknown" for pr in prs),
        "unpinned_pr_count": sum(not pr["revision_available"] for pr in prs),
    }


def project_groups(prs):
    """Keep presentation groups separate from resumable repository/number identities."""
    groups = {}
    for index, pr in enumerate(prs):
        profile = project_profile(pr["repository"])
        pr.update(profile)
        project_id = profile["project_type"]
        if project_id == "other":
            project_id += ":" + pr["repository"].casefold()
        group = groups.setdefault(project_id, {
            "id": project_id, **profile, "pr_indexes": [], "repositories": [],
        })
        group["pr_indexes"].append(index)
        repository = next((repo for repo in group["repositories"] if repo["repository"].casefold() == pr["repository"].casefold()), None)
        if repository is None:
            repository = {"repository": pr["repository"], "pr_indexes": []}
            group["repositories"].append(repository)
        repository["pr_indexes"].append(index)
    for group in groups.values():
        group.update(outcome_summary([prs[index] for index in group["pr_indexes"]]))
        for repository in group["repositories"]:
            repository.update(outcome_summary([prs[index] for index in repository["pr_indexes"]]))
    return list(groups.values())


def full_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{40}", value) is not None


def live_evidence_gaps(pr):
    """Check provenance without trusting the ledger's completion claim."""
    live = pr.get("live", {})
    gaps = []
    scope = live.get("scope")
    if scope not in ("pr-head-only", "integration", "technical"):
        gaps.append("Test scope is not recorded as pr-head-only, integration, or technical")
    commit = live.get("runtime_commit")
    if not full_sha(commit):
        gaps.append("Tested runtime/source commit is unavailable")
    elif scope in ("pr-head-only", "technical") and commit.lower() != str(pr.get("head_sha", "")).lower():
        gaps.append("Tested runtime/source commit differs from the pinned head")
    if scope == "integration":
        parents = live.get("integration_parents")
        if not isinstance(parents, list) or len(parents) != 2 or not all(full_sha(parent) for parent in parents) or {parent.lower() for parent in parents} != {str(pr.get(key, "")).lower() for key in ("base_sha", "head_sha")}:
            gaps.append("Integration commit parents do not match the pinned base and head")
    environment = live.get("environment")
    if not ((isinstance(environment, str) and environment.strip()) or (isinstance(environment, dict) and environment)):
        gaps.append("Test environment is not recorded")
    return gaps


def cleanup_status(live):
    if live.get("cleanup_required") is False and live.get("scope") == "technical" and isinstance(live.get("cleanup_reason"), str) and live["cleanup_reason"].strip():
        return "not_required"
    receipt = live.get("cleanup", {})
    if isinstance(receipt, dict) and all(receipt.get(key) is True for key in ("database_dropped", "servers_stopped", "worktree_removed")):
        return "complete"
    return "pending"


def next_actions(pr):
    """Keep simultaneous blockers visible instead of reducing a PR to one verdict."""
    actions = []
    def add(kind, label):
        actions.append({"kind": kind, "label": label})
    if pr.get("pr_state") == "closed":
        add("closed", "PR closed; retain evidence and finish owned cleanup only")
        if pr.get("pending_cleanup") or pr["live"].get("cleanup_status") == "pending":
            add("cleanup", "Verify owned resource cleanup")
        return actions
    if pr["freshness"] != "current":
        add("refresh", "Refresh revision evidence" if pr["freshness"] == "unknown" else "Revalidate changed revision")
    if pr["review"]["status"] == "issues" or pr["review"].get("findings") or any(s["display_status"] == "fail" for s in pr["scenarios"]):
        add("fixes", "Address verified findings")
    if pr["review"]["status"] in ("pending", "blocked"):
        add("review", "Complete static review" if pr["review"]["status"] == "pending" else "Resolve review blocker")
    if pr["publication"]["display_status"] in ("pending", "failed", "unverified"):
        add("publication", "Complete publication/readback within authorized scope")
    if pr["live"]["display_status"] == "blocked":
        add("blocked", "Resolve test blocker")
    elif pr["live"]["display_status"] in ("pending", "incomplete") or any(s["display_status"] not in ("pass", "fail") for s in pr["scenarios"]):
        add("testing", "Complete planned checks and evidence")
    if pr.get("pending_cleanup") or pr["live"].get("cleanup_status") == "pending":
        add("cleanup", "Verify owned resource cleanup")
    if not actions:
        add("complete", "Recorded review and check scope complete; not approval")
    return actions


def publication_link_matches_pr(reference, pr):
    """Accept a saved review/thread permalink on the same HTTPS PR, including GHE."""
    if not isinstance(reference, str):
        return False
    try:
        url, original = urlsplit(reference), urlsplit(pr["url"])
        if any(part.scheme != "https" or not part.hostname or part.username or part.password for part in (url, original)):
            return False
        if (url.hostname.lower(), url.port or 443) != (original.hostname.lower(), original.port or 443):
            return False
        path = f"/{pr['repository']}/pull/{pr['number']}"
        return original.path.rstrip("/") == path and url.path.rstrip("/") in (path, path + "/files") and not url.query and bool(re.fullmatch(r"(?:pullrequestreview-|discussion_r|r)[0-9]+", url.fragment))
    except ValueError:
        return False


def prepare(data, source_dir, output_dir):
    """Copy evidence before constructing reports; retain recorded status separately."""
    validate(data)
    data = copy.deepcopy(data)
    warnings = []
    copied = {}

    def retain(path, kind, label):
        source = Path(path).expanduser()
        if not source.is_absolute():
            source = source_dir / source
        if not source.is_file():
            warnings.append(f"{label}: missing {kind} ({path})")
            return None
        suffix = source.suffix.lower()
        if kind == "screenshot" and suffix not in IMAGE_TYPES:
            warnings.append(f"{label}: unsupported screenshot format ({suffix})")
            return None
        key = (str(source.resolve()), kind)
        if key not in copied:
            digest = hashlib.sha256(source.read_bytes()).hexdigest()[:20]
            relative = Path("evidence") / ("screenshots" if kind == "screenshot" else "exports") / f"{digest}{suffix}"
            target = output_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            copied[key] = relative.as_posix()
        return copied[key]

    for pr in data["prs"]:
        label = f"{pr['repository']}#{pr['number']}"
        pr.setdefault("base_sha", None)
        pr.setdefault("head_sha", None)
        pr["revision_available"] = all(bool(pr.get(f"{part}_sha")) for part in ("base", "head"))
        pr["stale"] = pr["revision_available"] and any(pr.get(f"current_{part}_sha") and pr[f"current_{part}_sha"] != pr[f"{part}_sha"] for part in ("base", "head"))
        pr["revision_refreshed"] = pr["revision_available"] and all(bool(pr.get(f"current_{part}_sha")) for part in ("base", "head"))
        pr["freshness"] = "stale" if pr["stale"] else "current" if pr["revision_refreshed"] else "unknown"
        if not pr["revision_available"]:
            warnings.append(f"{label}: pinned revision unavailable; review is blocked and freshness cannot be checked")
        elif pr["stale"]:
            warnings.append(f"{label}: base or head changed; all outcomes describe the pinned revision")
        elif not pr["revision_refreshed"]:
            warnings.append(f"{label}: final revision refresh not recorded")
        gaps = pr.setdefault("coverage_gaps", [])
        for section in SECTION_STATUSES:
            if section not in pr:
                pr[section] = {"status": "pending"}
                gaps.append(f"{section.capitalize()} result not recorded")
        publication = pr["publication"]
        publication["display_status"] = publication["status"]
        if publication["status"] in ("verified", "reused"):
            refs = [publication.get("review_url")] + publication.get("reused_threads", [])
            has_link = any(publication_link_matches_pr(ref, pr) for ref in refs)
            if not has_link:
                publication["display_status"] = "unverified"
                gap = "Publication marked verified/reused without a saved HTTPS review or thread permalink matching this PR"
                gaps.append(gap)
                warnings.append(f"{label}: {gap}")
        if not pr["scenarios"] and pr["live"]["status"] != "not_required":
            gaps.append("No live scenario results recorded")
        live = pr["live"]
        live["display_status"] = live["status"]
        has_observations = any(s["status"] != "blocked" for s in pr["scenarios"])
        provenance_gaps = live_evidence_gaps(pr) if has_observations or live["status"] == "complete" else []
        live["evidence_gaps"] = provenance_gaps
        if has_observations or live["status"] == "complete" or "cleanup" in live or "runtime_commit" in live:
            live["cleanup_status"] = cleanup_status(live)
        else:
            live.pop("cleanup_status", None)
        for scenario in pr["scenarios"]:
            scenario["display_status"] = scenario["status"]
            scenario["evidence_gaps"] = list(scenario.get("evidence_gaps", []))
            case_label = f"{label}/{scenario['id']}"
            if not pr["revision_available"]:
                scenario["display_status"] = "blocked"
                scenario["evidence_gaps"].append("Pinned revision unavailable; recorded outcome cannot be verified")
            if pr["revision_available"] and scenario["status"] == "pass" and not all(meaningful(scenario[key]) for key in ("expected", "actual", "steps")):
                scenario["display_status"] = "partial"
                scenario["evidence_gaps"].append("Recorded pass lacks expected/actual observations or runnable steps; shown as partial")
                warnings.append(f"{case_label}: incomplete pass evidence; shown as partial")
            if pr["revision_available"] and scenario["status"] in ("pass", "fail") and provenance_gaps:
                scenario["display_status"] = "partial"
                scenario["evidence_gaps"].extend(provenance_gaps)
            screenshots = []
            for path in scenario.get("screenshots", []):
                saved = retain(path, "screenshot", case_label)
                if saved:
                    screenshots.append(saved)
                else:
                    scenario["evidence_gaps"].append(f"Screenshot unavailable: {path}")
            scenario["screenshots"] = screenshots
            if not screenshots and scenario["status"] in ("pass", "fail", "observed", "partial", "spec_gap"):
                note = scenario.get("visual_evidence") or "No retained screenshot; visual outcome has no illustrated evidence"
                scenario["evidence_gaps"].append(note)
            for export in scenario.get("exports", []):
                original = export["path"]
                saved = retain(original, "export", case_label)
                export["path"] = saved or original
                if not saved:
                    export["missing"] = True
                    scenario["evidence_gaps"].append(f"Export unavailable: {export['file_name']}")
                else:
                    export.pop("missing", None)
                if not meaningful(export.get("verification")):
                    scenario["evidence_gaps"].append(f"Export contents not verified: {export['file_name']}")
            scenario["evidence_gaps"] = list({json.dumps(gap, sort_keys=True): gap for gap in scenario["evidence_gaps"]}.values())
        if live["status"] == "complete":
            if not pr["scenarios"]:
                live["evidence_gaps"].append("No acceptance scenarios recorded")
            elif any(s["display_status"] not in ("pass", "fail") for s in pr["scenarios"]):
                live["evidence_gaps"].append("Planned checks include blocked or incomplete acceptance evidence")
            if live["evidence_gaps"]:
                live["display_status"] = "incomplete"
        warnings.extend(f"{label}: {gap}" for gap in live["evidence_gaps"])
        pr["next_actions"] = next_actions(pr)
    data["report"] = {**outcome_summary(data["prs"]), "projects": project_groups(data["prs"]), "warnings": warnings}
    return data


def safe_json(data):
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def html_data(data, output_dir, inline):
    result = copy.deepcopy(data)
    result["image_sources"] = {}
    result["report_links"] = {name: str(output_dir / file_name) if inline else file_name for name, file_name in (("guide", "scenario-guide.html"), ("results", "results.json"))}
    result["local_links"] = list(result["report_links"].values()) if inline else []
    budget = 2 * 1024 * 1024
    chosen = set()
    if inline:
        ordered = sorted(((p, s) for p in result["prs"] for s in p["scenarios"]), key=lambda pair: pair[1]["display_status"] != "fail")
        for _, scenario in ordered:
            for path in scenario["screenshots"][:1]:
                if path in chosen:
                    continue
                size = (output_dir / path).stat().st_size * 4 // 3
                if len(chosen) < 4 and size <= budget:
                    chosen.add(path)
                    budget -= size
    for pr in result["prs"]:
        for scenario in pr["scenarios"]:
            for export in scenario.get("exports", []):
                if export.get("path") and not export.get("missing"):
                    export["report_url"] = str(output_dir / export["path"]) if inline else export["path"]
                    if inline:
                        result["local_links"].append(export["report_url"])
            scenario["images"] = []
            for path in scenario["screenshots"]:
                if inline and path not in chosen:
                    continue
                source = output_dir / path
                if path not in result["image_sources"]:
                    uri = "data:" + IMAGE_TYPES[source.suffix.lower()] + ";base64," + base64.b64encode(source.read_bytes()).decode("ascii")
                    result["image_sources"][path] = uri
                scenario["images"].append({"path": path})
            scenario["images_omitted"] = len(scenario["screenshots"]) - len(scenario["images"])
    result["inline"] = inline
    return result


def render(input_path, output_dir):
    input_path = Path(input_path).resolve()
    output_dir = Path(output_dir).resolve()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    validate(data)
    output_dir.mkdir(parents=True, exist_ok=True)
    data = prepare(data, input_path.parent, output_dir)
    (output_dir / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    template = (Path(__file__).resolve().parent.parent / "assets" / "report.html").read_text(encoding="utf-8")
    for name, inline in (("scenario-guide.html", False), ("inline-report.html", True)):
        (output_dir / name).write_text(template.replace("__REPORT_JSON__", safe_json(html_data(data, output_dir, inline))), encoding="utf-8")
    return {"guide": str(output_dir / "scenario-guide.html"), "inline": str(output_dir / "inline-report.html"), "results": str(output_dir / "results.json"), "pr_count": len(data["prs"]), **data["report"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(render(args.input, args.output)))
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
