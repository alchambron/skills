#!/usr/bin/env python3
"""Render analyzer JSON reports as the respond plan or outcome Markdown.

Usage:
  render_report.py REPORT.json [...] [--out FILE]             plan: summary + per-comment tables
  render_report.py REPORT.json [...] --outcomes [--out FILE]  outcomes, joined with the
                                                              PR's publication journals
  render_report.py --run-dir RUN_DIR [--outcomes]             use recorded report paths and status
Stdout carries only the summary table and the written file path; the file has everything.
"""

import argparse
import importlib.util
import json
from pathlib import Path
import sys

from publish import PublishError, publication_journal_path

LEGACY_PRIORITY = {"high", "medium", "low", "urgent ticket"}
DISPOSITIONS = ("Fix", "Answer", "Defer", "No change")


def cell(value, limit=None):
    if isinstance(value, list):
        value = "; ".join(str(v) for v in value if v)
    text = " ".join(str(value or "").split()).replace("|", "\\|")
    if limit and len(text) > limit:
        text = text[:limit - 1] + "…"
    return text or "—"


def split_labels(report):
    labels = report.get("labels") or []
    names = [l if isinstance(l, str) else l.get("name", "") for l in labels]
    risk = [n for n in names if n.casefold().startswith("risk:")]
    priority = [n for n in names if n.casefold().startswith("priority:")]
    legacy = [n for n in names if n.casefold() in LEGACY_PRIORITY]
    other = [n for n in names if n not in risk + priority + legacy]

    def show(found, legacy_found=()):
        values = list(found) + [f"{n} (legacy)" for n in legacy_found]
        if not values:
            return "Not labeled"
        return values[0] if len(values) == 1 else "Conflicting labels: " + ", ".join(values)

    return (report.get("risk") or show(risk), report.get("priority") or show(priority, legacy),
            ", ".join(other) or "None")


def pr_cell(report, title=True):
    number = report.get("number") or report["url"].rstrip("/").rsplit("/", 1)[-1]
    name = f"{report.get('repository', '')}#{number}"
    if title and report.get("title"):
        name += " — " + cell(report["title"], 70)
    return f"[{name}]({report['url']})"


def disposition(comment):
    value = str(comment.get("disposition") or "")
    for known in DISPOSITIONS:
        if value.casefold().startswith(known.casefold()):
            return known
    return value or "Unset"


def counts(report):
    tally = {}
    for comment in report.get("comments", []):
        key = disposition(comment)
        tally[key] = tally.get(key, 0) + 1
    return ", ".join(f"{k} {v}" for k, v in tally.items()) or "no rows"


def comment_cell(comment):
    status = comment.get("status") or "open"
    summary = comment.get("summary") or comment.get("solution") or ""
    who = comment.get("reviewer") or "?"
    return f"[{cell(who)}: {cell(summary, 120)}]({comment.get('url', '')}) ({cell(status, 80)})"


def render_plan(reports, paths=None):
    head = ["| PR / other labels | Risk | Priority | Feedback and suggested plan | Verification / blockers |",
            "| --- | --- | --- | --- | --- |"]
    for report in reports:
        risk, priority, other = split_labels(report)
        head.append(f"| {pr_cell(report)}; {cell(other)} | {cell(risk)} | {cell(priority)} | "
                    f"{cell(report.get('summary'), 300)} ({counts(report)}) | "
                    f"{cell((report.get('verification') or []) + (report.get('blockers') or []), 300)} |")
    body = list(head)
    for report, path in zip(reports, paths or [None] * len(reports)):
        risk, priority, other = split_labels(report)
        prefix = f"| {pr_cell(report, title=False)}; {cell(other)} | {cell(risk)} | {cell(priority)} |"
        body += ["", f"## {pr_cell(report)}", "",
                 f"Head `{report.get('headSha', '?')}`, base `{report.get('baseSha', '?')}`. "
                 f"Coverage: {cell(report.get('coverage'))}", "",
                 "| PR / other labels | Risk | Priority | Comment | Important? | Valuable? | Solution for this comment |",
                 "| --- | --- | --- | --- | --- | --- | --- |"]
        for comment in report.get("comments", []):
            body.append(f"{prefix} {comment_cell(comment)} | {cell(comment.get('importance'))} | "
                        f"{cell(comment.get('value'))} | {disposition(comment)}: "
                        f"{cell(comment.get('solution'))} {cell(comment.get('verification'))} |")
        if report.get("plan"):
            body += ["", "Plan:"] + [f"{i}. {cell(step)}" for i, step in enumerate(report["plan"], 1)]
        if report.get("verification"):
            body += ["", "Verification:"] + [f"- {cell(step)}" for step in report["verification"]]
        if report.get("blockers"):
            body += ["", "Blockers:"] + [f"- {cell(b)}" for b in report["blockers"]]
        drafts = [c for c in report.get("comments", []) if c.get("draftReply")]
        if drafts:
            body += ["", "Draft replies:"]
            for comment in drafts:
                body += ["", f"- [{cell(comment.get('reviewer'))}]({comment.get('url', '')}) "
                             f"({comment.get('replyKind') or ('inline' if comment.get('inReplyTo') else 'conversation')}):",
                         "", "  > " + "\n  > ".join(str(comment["draftReply"]).splitlines())]
        if report.get("includeCoverageAppendix"):
            reference = report.get("coverageInventory")
            if not reference or path is None:
                raise ValueError("A full coverage appendix requires an inventory and report path")
            inventory = json.loads((Path(path).parent / reference).read_text())
            if not isinstance(inventory, list):
                raise ValueError("Coverage inventory must be a list of original items")
            body += ["", "### Coverage appendix", "",
                     "Historical coverage; these entries do not request new publication.", "",
                     "| Original item | Category | Disposition / reason |", "| --- | --- | --- |"]
            body += [f"| [{cell(item.get('id'))}]({item.get('url', '')}) | "
                     f"{cell(item.get('category'))} | {cell(item.get('reason'))} |"
                     for item in inventory]
    return "\n".join(head), "\n".join(body) + "\n"


def journal_for(path, report, recorded_path=None):
    candidate = Path(recorded_path) if recorded_path else publication_journal_path(path, report["url"])
    if not candidate.exists():
        return {}
    journal = json.loads(candidate.read_text())
    if journal.get("url") != report["url"]:
        raise PublishError(f"Journal identity mismatch: {candidate}")
    return journal


def ledger_helpers():
    path = Path(__file__).resolve().parents[2] / "respond-all/scripts/ledger.py"
    spec = importlib.util.spec_from_file_location("respond_run_ledger", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render_outcomes(reports, paths, ledger=None):
    head = ["| PR / other labels | Risk | Priority | Outcome | Reviewer requests | Publication |",
            "| --- | --- | --- | --- | --- | --- |"]
    body = []
    helpers = ledger_helpers() if ledger is not None else None
    progress = ({r["url"]: r for r in helpers.progress(ledger)["entries"]}
                if helpers else {})
    for report, path in zip(reports, paths):
        entry = (ledger or {}).get("prs", {}).get(report["url"], {})
        state = progress.get(report["url"], {})
        actions, drafts = helpers.outstanding(entry) if helpers else ([], [])
        draft_ids = {str(c.get("id")) for c in drafts}
        recorded = {str(c.get("id")): c for c in entry.get("comments", [])}
        risk, priority, other = split_labels(report)
        reference = (entry.get("reports") or {}).get("publication")
        recorded_path = (Path(ledger.get("_runDir", Path(path).parent)) / reference
                         if reference else None)
        journal = journal_for(path, report, recorded_path)
        replies = journal.get("replies", {})
        reviewers = journal.get("reviewers", {})
        posted = sum(1 for r in replies.values() if r.get("status") == "verified")
        requests = [f"{login}: {state.get('outcome') or state.get('status')}"
                    for login, state in reviewers.items()]
        workflow = (f"{state.get('state', 'unknown')}; "
                    f"recorded {entry.get('phase', '?')}/{entry.get('status', '?')}; "
                    f"outstanding actions={len(actions)}; drafts={len(drafts)}" if entry
                    else "Workflow status not recorded")
        head.append(f"| {pr_cell(report)}; {cell(other)} | {cell(risk)} | {cell(priority)} | {cell(workflow)}; {counts(report)} | "
                    f"{cell(requests) if requests else 'None'} | "
                    f"{journal.get('status', 'not published')}; replies {posted}/{len(replies)}; "
                    f"head {cell(journal.get('expectedHeadSha'), 12)} |")
        prefix = f"| {pr_cell(report, title=False)}; {cell(other)} | {cell(risk)} | {cell(priority)} |"
        body += ["", f"## {pr_cell(report)}", "",
                 f"Recorded workflow: {cell(workflow)}. Current head: "
                 f"`{entry.get('currentHeadSha') or report.get('headSha', '?')}`. "
                 "Publication does not establish passing CI or human approval.", "",
                 "| PR / other labels | Risk | Priority | Comment | Outcome and evidence | Reviewer reply and thread status |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for comment in report.get("comments", []):
            comment = {**comment, **recorded.get(str(comment.get("id")), {})}
            reply = replies.get(str(comment.get("id")), {})
            reply_cell = (f"[{reply.get('outcome', reply.get('status'))}]({reply['url']})" if reply.get("url")
                          else ("Draft" if comment.get("draftReply") else "No reply needed"))
            if str(comment.get("id")) in draft_ids:
                reply_cell += "; outstanding draft remains"
            outcome = comment.get("outcome") or (
                f"Implementation outcome not recorded. Planned {disposition(comment)}: "
                f"{comment.get('solution', '')}")
            body.append(f"{prefix} [{cell(comment.get('reviewer'))}]({comment.get('url', '')}) | "
                        f"{cell(outcome, 300)} | "
                        f"{reply_cell} {cell(comment.get('threadStatus'))} |")
        if actions or drafts:
            body += ["", "Outstanding work:", ""]
            body += [f"- Action {cell(action.get('key'))}: {cell(action.get('status'))}; "
                     f"{cell(action.get('condition') or action.get('outcome'))}" for action in actions]
            body += [f"- Draft [{cell(draft.get('id'))}]({draft.get('url', '')}): "
                     f"{cell(draft.get('draftReply') or draft.get('replyDraft'))}" for draft in drafts]
        if state.get("nextAction"):
            body += ["", "Next action: " + cell(state["nextAction"])]
        evidence = {key: entry[key] for key in
                    ("verification", "ci", "checks", "reviewDecision", "humanApproval",
                     "approvalHeadSha", "mergeability", "deployment", "watcher") if key in entry}
        if evidence:
            body += ["", "Recorded evidence:", ""] + [
                f"- {key}: {cell(value)}" for key, value in evidence.items()]
        if entry.get("blockers"):
            body += ["", "Outstanding blockers:", ""] + [
                f"- {cell(value)}" for value in entry["blockers"]]
        if entry.get("reports"):
            body += ["", "Evidence artifacts:", ""] + [
                f"- {cell(key)}: [{cell(value)}]({(Path(ledger.get('_runDir', Path(path).parent)) / value).resolve()})"
                for key, value in entry["reports"].items() if isinstance(value, str)]
    return "\n".join(head), "\n".join(head + body) + "\n"


def load_run_reports(run_dir):
    """Read the ledger's report references instead of globbing duplicate rounds."""
    root = Path(run_dir).resolve()
    ledger = json.loads((root / "ledger.json").read_text())
    ledger["_runDir"] = str(root)
    reports, paths = [], []
    for url, entry in ledger.get("prs", {}).items():
        reference = (entry.get("reports") or {}).get("analysis")
        if not reference:
            if entry.get("status") == "skipped":
                continue
            raise ValueError(f"Missing analysis report for {url}; finish analysis before rendering")
        path = root / reference
        report = json.loads(path.read_text())
        if report.get("url") != url:
            raise ValueError(f"Analysis report identity mismatch for {url}: {path}")
        reports.append(report)
        paths.append(path)
    if not reports:
        raise ValueError("No analyzed PRs in this run")
    return reports, paths, ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="*")
    parser.add_argument("--run-dir", help="Read analysis paths and outcome evidence from this ledger")
    parser.add_argument("--outcomes", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    if bool(args.reports) == bool(args.run_dir):
        parser.error("Pass report paths or --run-dir RUN_DIR")
    ledger = None
    if args.run_dir:
        reports, paths, ledger = load_run_reports(args.run_dir)
    else:
        paths = args.reports
        reports = [json.loads(Path(path).read_text()) for path in paths]
    if args.outcomes:
        summary, full = render_outcomes(reports, paths, ledger)
        default = "combined-outcomes.md"
    else:
        summary, full = render_plan(reports, paths)
        default = "combined-plan.md"
    out = Path(args.out or (Path(args.run_dir) if args.run_dir else Path(paths[0]).parent) / default)
    out.write_text(full)
    print(summary)
    print(f"\nFull report: {out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, PublishError) as error:
        print(f"render error: {error}", file=sys.stderr)
        sys.exit(2)
