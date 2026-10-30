#!/usr/bin/env python3
"""Render analyzer JSON reports as the respond plan or outcome Markdown.

Usage:
  render_report.py REPORT.json [...] [--out FILE]             plan: summary + per-comment tables
  render_report.py REPORT.json [...] --outcomes [--out FILE]  outcomes, joined with the
                                                              pr-NUMBER-publication.json journals
Stdout carries only the summary table and the written file path; the file has everything.
"""

import argparse
import json
from pathlib import Path
import sys

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


def render_plan(reports):
    head = ["| PR / other labels | Risk | Priority | Feedback and suggested plan | Verification / blockers |",
            "| --- | --- | --- | --- | --- |"]
    for report in reports:
        risk, priority, other = split_labels(report)
        head.append(f"| {pr_cell(report)}; {cell(other)} | {cell(risk)} | {cell(priority)} | "
                    f"{cell(report.get('summary'), 300)} ({counts(report)}) | "
                    f"{cell((report.get('verification') or []) + (report.get('blockers') or []), 300)} |")
    body = list(head)
    for report in reports:
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
    return "\n".join(head), "\n".join(body) + "\n"


def journal_for(path, report):
    number = report.get("number") or report["url"].rstrip("/").rsplit("/", 1)[-1]
    candidate = Path(path).parent / f"pr-{number}-publication.json"
    return json.loads(candidate.read_text()) if candidate.exists() else {}


def render_outcomes(reports, paths):
    head = ["| PR / other labels | Risk | Priority | Outcome | Reviewer requests | Publication |",
            "| --- | --- | --- | --- | --- | --- |"]
    body = []
    for report, path in zip(reports, paths):
        risk, priority, other = split_labels(report)
        journal = journal_for(path, report)
        replies = journal.get("replies", {})
        reviewers = journal.get("reviewers", {})
        posted = sum(1 for r in replies.values() if r.get("status") == "verified")
        requests = [f"{login}: {state.get('outcome') or state.get('status')}"
                    for login, state in reviewers.items()]
        head.append(f"| {pr_cell(report)}; {cell(other)} | {cell(risk)} | {cell(priority)} | {counts(report)} | "
                    f"{cell(requests) if requests else 'None'} | "
                    f"{journal.get('status', 'not published')}; replies {posted}/{len(replies)} |")
        prefix = f"| {pr_cell(report, title=False)}; {cell(other)} | {cell(risk)} | {cell(priority)} |"
        body += ["", f"## {pr_cell(report)}", "",
                 "| PR / other labels | Risk | Priority | Comment | Outcome and evidence | Reviewer reply and thread status |",
                 "| --- | --- | --- | --- | --- | --- |"]
        for comment in report.get("comments", []):
            reply = replies.get(str(comment.get("id")), {})
            reply_cell = (f"[{reply.get('outcome', reply.get('status'))}]({reply['url']})" if reply.get("url")
                          else ("Draft" if comment.get("draftReply") else "No reply needed"))
            body.append(f"{prefix} [{cell(comment.get('reviewer'))}]({comment.get('url', '')}) | "
                        f"{disposition(comment)}: {cell(comment.get('outcome') or comment.get('solution'), 300)} | "
                        f"{reply_cell} {cell(comment.get('threadStatus'))} |")
    return "\n".join(head), "\n".join(head + body) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("reports", nargs="+")
    parser.add_argument("--outcomes", action="store_true")
    parser.add_argument("--out")
    args = parser.parse_args()
    reports = [json.loads(Path(path).read_text()) for path in args.reports]
    if args.outcomes:
        summary, full = render_outcomes(reports, args.reports)
        default = "combined-outcomes.md"
    else:
        summary, full = render_plan(reports)
        default = "combined-plan.md"
    out = Path(args.out or Path(args.reports[0]).parent / default)
    out.write_text(full)
    print(summary)
    print(f"\nFull report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
