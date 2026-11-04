#!/usr/bin/env python3
"""Report the tokens a review-all run used, in total and per PR, from Codex rollout transcripts.

Usage: token_usage.py --run-dir RUN_DIR [--sessions-root DIR] [--output FILE] [--markdown]

The run's sessions are the coordinator (the earliest top-level session that names RUN_DIR), every T3-delegated
session that names RUN_DIR while the coordinator runs, and all native sub-agents spawned below them. Each session's
tokens come from its last cumulative `token_count` event. A child's tokens go to the PRs its own task names: the
plaintext brief of a T3-delegated session, or a native sub-agent's task name such as `scenario_JouleMV_1847_a`.
Children whose task names no PR (static review of the whole batch) are split in proportion to how often their
transcript references each PR. The coordinator and children that reference no PR are shared overhead, also shown
split evenly across PRs.
Writes RUN_DIR/token-usage.json by default and prints a compact JSON summary, or with --markdown the table for the
final response. Reads local files only.
"""

import argparse
import datetime as dt
import json
from pathlib import Path
import re
import sys

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens", "total_tokens")


def parse_time(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def empty():
    return {field: 0 for field in FIELDS}


def add(target, usage, weight=1.0):
    for field in FIELDS:
        target[field] += (usage.get(field) or 0) * weight


def rounded(usage):
    return {field: int(round(value)) for field, value in usage.items()}


def read_session(path):
    """Session identity, time span, cumulative usage, weekly-limit readings and raw text of one rollout."""
    text = path.read_text(encoding="utf-8", errors="replace")
    session = {"path": str(path), "id": None, "parent": None, "name": None, "start": None, "end": None,
               "usage": empty(), "limits": [], "text": text}
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            continue
        payload = record.get("payload") or {}
        stamp = record.get("timestamp")
        if stamp:
            moment = parse_time(stamp)
            session["start"] = session["start"] or moment
            session["end"] = moment
        if record.get("type") == "session_meta":
            session["id"] = payload.get("id")
            spawn = ((payload.get("source") or {}).get("subagent") or {}).get("thread_spawn") if isinstance(payload.get("source"), dict) else None
            if spawn:
                session["parent"] = spawn.get("parent_thread_id")
                session["name"] = spawn.get("agent_path")
        elif record.get("type") == "event_msg" and payload.get("type") == "token_count":
            info = payload.get("info") or {}
            if info.get("total_token_usage"):
                session["usage"] = {field: info["total_token_usage"].get(field) or 0 for field in FIELDS}
            primary = (payload.get("rate_limits") or {}).get("primary") or {}
            if primary.get("used_percent") is not None:
                session["limits"].append(primary["used_percent"])
    return session


def spawn_parent(path):
    """Parent thread of a native sub-agent rollout, read from its first line only."""
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            payload = json.loads(handle.readline()).get("payload") or {}
    except (OSError, ValueError):
        return None
    source = payload.get("source")
    spawn = (source.get("subagent") or {}).get("thread_spawn") if isinstance(source, dict) else None
    return spawn.get("parent_thread_id") if spawn else None


def first_brief(text):
    """The plaintext task of a T3-delegated session; native sub-agent briefs are encrypted."""
    for line in text.splitlines():
        if '"role":"user"' not in line.replace(" ", ""):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        for part in (record.get("payload") or {}).get("content") or []:
            value = part.get("text") or ""
            if value and not value.startswith(("# AGENTS", "<")):
                return value
    return None


def task_patterns(pr):
    """PR identity inside a native task name, e.g. runtime_JouleMV_1847 or scenario-WebSynco-79-b."""
    repository = re.escape(pr["repository"].split("/", 1)[1])
    return re.compile(rf"(?<![A-Za-z0-9]){repository}[_-]+{pr['number']}(?!\d)", re.IGNORECASE)


def candidate_files(root, start, end):
    day = start.date() - dt.timedelta(days=1)
    while day <= end.date() + dt.timedelta(days=1):
        directory = root / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
        if directory.is_dir():
            yield from sorted(directory.glob("rollout-*.jsonl"))
        day += dt.timedelta(days=1)


def pr_patterns(pr):
    owner, repository = pr["repository"].split("/", 1)
    number = pr["number"]
    escaped = re.escape(owner) + r"[/-]{1,2}" + re.escape(repository)
    return re.compile(
        rf"{re.escape(pr['repository'])}/pull/{number}(?!\d)|{re.escape(pr['repository'])}#{number}(?!\d)"
        rf"|{escaped}[/-]{number}(?![\d])", re.IGNORECASE)


def collect(run_dir, sessions_root):
    given = Path(run_dir).expanduser()
    run_dir = given.resolve()
    ledger = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    prs = [{"key": f"{pr['repository']}#{pr['number']}", "repository": pr["repository"], "number": pr["number"],
            "url": pr.get("url")} for pr in ledger.get("prs", [])]
    collected = parse_time(ledger["collected_at"])
    # Transcripts name the run as typed: resolved, unresolved (/var vs /private/var) or under ~.
    spellings = {str(given.absolute()), str(run_dir)}
    for spelling in list(spellings):
        if spelling.startswith(str(Path.home()) + "/"):
            spellings.add("~" + spelling[len(str(Path.home())):])
    markers = [spelling.encode() for spelling in spellings]
    mentioned, unmentioned = [], []
    for path in candidate_files(Path(sessions_root), collected, dt.datetime.now(dt.timezone.utc)):
        try:
            data = path.read_bytes()
            (mentioned if any(marker in data for marker in markers) else unmentioned).append(path)
        except OSError:
            continue
    named = [read_session(path) for path in mentioned]
    top = sorted((s for s in named if not s["parent"] and s["start"]), key=lambda s: s["start"])
    if not top:
        raise ValueError(f"No Codex session names {run_dir}")
    coordinator = top[0]
    included = {coordinator["id"]: coordinator}
    for session in top[1:]:
        if coordinator["start"] <= session["start"] <= coordinator["end"]:
            included[session["id"]] = session
    # Native sub-agents may never print RUN_DIR; follow parent links from every included session.
    children = {s["id"]: s for s in named if s["parent"]}
    parents = {path: spawn_parent(path) for path in unmentioned}
    grew = True
    while grew:
        grew = False
        for identifier, session in children.items():
            if identifier not in included and session["parent"] in included:
                included[identifier] = session
                grew = True
        for path, parent in list(parents.items()):
            if parent in included:
                session = read_session(path)
                children[session["id"]] = session
                del parents[path]
                grew = True
    return run_dir, prs, coordinator, list(included.values())


def attribute(prs, coordinator, sessions):
    patterns = {pr["key"]: pr_patterns(pr) for pr in prs}
    names = {pr["key"]: task_patterns(pr) for pr in prs}
    per_pr = {pr["key"]: empty() for pr in prs}
    shared, total, rows = empty(), empty(), []
    for session in sorted(sessions, key=lambda s: s["start"]):
        add(total, session["usage"])
        brief = None if session["parent"] else first_brief(session["text"])
        owned = [key for key in patterns if (brief and patterns[key].search(brief))
                 or (session["name"] and names[key].search(session["name"]))]
        if owned:
            counts = {key: 1 for key in owned}
            basis = "task"
        else:
            counts = {key: len(pattern.findall(session["text"])) for key, pattern in patterns.items()}
            basis = "references"
        mentioned = sum(counts.values())
        label = session["name"] or ("delegated " + ", ".join(owned) if owned else "delegated")
        role = "coordinator" if session is coordinator else label
        if session is coordinator or not mentioned:
            add(shared, session["usage"])
            split = {}
        else:
            split = {key: count / mentioned for key, count in counts.items() if count}
            for key, weight in split.items():
                add(per_pr[key], session["usage"], weight)
        minutes = (session["end"] - session["start"]).total_seconds() / 60 if session["start"] and session["end"] else None
        rows.append({"session": session["id"], "role": role, "minutes": round(minutes, 1) if minutes is not None else None,
                     "total_tokens": session["usage"]["total_tokens"], "input_tokens": session["usage"]["input_tokens"],
                     "cached_input_tokens": session["usage"]["cached_input_tokens"],
                     "output_tokens": session["usage"]["output_tokens"],
                     "prs": {key: round(weight, 2) for key, weight in split.items()},
                     "attribution": "shared" if not split else basis})
    count = len(prs) or 1
    report_prs = []
    for pr in prs:
        with_shared = dict(per_pr[pr["key"]])
        add(with_shared, shared, 1 / count)
        report_prs.append({"pr": pr["key"], "url": pr["url"], "direct": rounded(per_pr[pr["key"]]),
                           "withSharedSplit": rounded(with_shared)})
    limits = coordinator["limits"]
    return {"total": rounded(total), "shared": rounded(shared), "prs": report_prs, "sessions": rows,
            "weeklyLimitPercent": {"start": limits[0], "end": limits[-1], "delta": round(limits[-1] - limits[0], 1),
                                   "note": "account-wide; includes other sessions running at the same time"} if limits else None}


def millions(value):
    return f"{value / 1_000_000:.1f}M"


def markdown(report):
    total = report["total"]["total_tokens"] or 1
    lines = ["| PR | Tokens, own work | Tokens with shared split | Share of total |", "|---|---:|---:|---:|"]
    for row in sorted(report["prs"], key=lambda r: -r["withSharedSplit"]["total_tokens"]):
        lines.append(f"| {row['pr']} | {millions(row['direct']['total_tokens'])} | "
                     f"{millions(row['withSharedSplit']['total_tokens'])} | {row['withSharedSplit']['total_tokens'] / total:.0%} |")
    lines.append(f"| Shared (coordinator, children without a PR) | {millions(report['shared']['total_tokens'])} | | "
                 f"{report['shared']['total_tokens'] / total:.0%} |")
    usage = report["total"]
    cached = usage["cached_input_tokens"] / (usage["input_tokens"] or 1)
    lines.append(f"| **Total** ({len(report['sessions'])} sessions) | **{millions(usage['total_tokens'])}** | | 100% |")
    lines.append("")
    lines.append(f"Input {millions(usage['input_tokens'])} ({cached:.0%} cached), output {millions(usage['output_tokens'])} "
                 f"(reasoning {millions(usage['reasoning_output_tokens'])}).")
    limit = report.get("weeklyLimitPercent")
    if limit:
        lines.append(f"Weekly limit {limit['start']:g}% → {limit['end']:g}% during the run (account-wide, includes other sessions).")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--sessions-root", default=str(Path.home() / ".codex" / "sessions"))
    parser.add_argument("--output")
    parser.add_argument("--markdown", action="store_true", help="Print the final-response table instead of JSON")
    args = parser.parse_args(argv)
    try:
        run_dir, prs, coordinator, sessions = collect(args.run_dir, args.sessions_root)
        report = {"runDir": str(run_dir), "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                  "coordinatorSession": coordinator["id"], **attribute(prs, coordinator, sessions)}
    except (OSError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    output = Path(args.output) if args.output else run_dir / "token-usage.json"
    output.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    if args.markdown:
        print(markdown(report))
        return 0
    print(json.dumps({"output": str(output), "sessions": len(report["sessions"]),
                      "totalTokens": report["total"]["total_tokens"], "sharedTokens": report["shared"]["total_tokens"],
                      "perPr": {row["pr"]: {"direct": row["direct"]["total_tokens"],
                                            "withSharedSplit": row["withSharedSplit"]["total_tokens"]} for row in report["prs"]},
                      "weeklyLimitPercent": report["weeklyLimitPercent"]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
