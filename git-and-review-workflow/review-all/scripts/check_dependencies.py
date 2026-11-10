#!/usr/bin/env python3
"""Check review-all companion skills locally before any batch work starts.

Exit 0: available. Exit 2: installation required; no network or runtime work performed.
--skills-root may be repeated to search only explicit installation roots.
REVIEW_SKILLS_DIRS provides the same override to the workflow entrypoints (os.pathsep-separated).
"""

import argparse
import json
import os
from pathlib import Path
import re
import sys


SOURCE = "https://github.com/alchambron/skills/tree/main/"
DEPENDENCIES = (
    ("code-review", ("code-review",), "git-and-review-workflow/code-review", ()),
    ("manual-test-guide", ("manual-test-guide",), "testing-and-quality/manual-test-guide", ()),
    ("github-inline-review", ("github-inline-review",), "git-and-review-workflow/github-inline-review",
     ("references/github-api.md",)),
    ("joulemv-code-review", ("joulemv-code-review",), "git-and-review-workflow/joulemv-code-review", ()),
    ("browser-testing", ("joulemv-browser-testing", "browser-testing"), "testing-and-quality/browser-testing", ()),
)


def installation_roots(roots=None):
    if roots is None:
        configured = os.environ.get("REVIEW_SKILLS_DIRS")
        if configured is not None:
            roots = [value for value in configured.split(os.pathsep) if value.strip()]
        else:
            siblings = Path(__file__).resolve().parents[2]
            roots = [siblings, Path.home() / ".codex/skills", Path.home() / ".claude/skills",
                     Path.home() / ".agents/skills"]
            if os.environ.get("CODEX_HOME"):
                roots.insert(0, Path(os.environ["CODEX_HOME"]) / "skills")
            if siblings.name == "git-and-review-workflow":
                roots.append(siblings.parent / "testing-and-quality")
    return list(dict.fromkeys(Path(root).expanduser().resolve() for root in roots))


def inspect_skill(folder, name, resources):
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return "SKILL.md is missing or unreadable"
    frontmatter = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.S)
    if not frontmatter or not frontmatter[2].strip():
        return "SKILL.md has no valid frontmatter and instruction body"
    declared = re.search(r"^name:\s*(.*?)\s*$", frontmatter[1], re.M)
    if not declared or declared[1].strip("\"'") != name:
        return f"SKILL.md must declare name: {name}"
    for resource in resources:
        try:
            if not (folder / resource).read_text(encoding="utf-8").strip():
                return f"Required resource is empty: {resource}"
        except (OSError, UnicodeError):
            return f"Required resource is missing or unreadable: {resource}"
    return None


def check(roots=None):
    roots = installation_roots(roots)
    dependencies = []
    for name, folders, source, resources in DEPENDENCIES:
        candidates = [root / folder for root in roots for folder in folders]
        errors = []
        selected = None
        for candidate in candidates:
            error = inspect_skill(candidate, name, resources)
            if error is None:
                selected = candidate
                break
            errors.append({"path": str(candidate), "reason": error})
        dependencies.append({"name": name, "status": "available" if selected else "missing_or_invalid",
                             "path": str(selected) if selected else None, "install_folder": folders[0],
                             "url": SOURCE + source, "searched": errors if selected is None else []})
    ready = all(item["status"] == "available" for item in dependencies)
    return {"ok": ready, "status": "ready" if ready else "installation_required", "dependencies": dependencies}


def notice(result):
    lines = ["review-all did not start. Please install or update these complete skill folders:"]
    for item in result["dependencies"]:
        if item["status"] != "available":
            lines.append(f"- [{item['name']}]({item['url']}) → {item['install_folder']}")
    lines.append("Use your configured skills directory (~/.codex/skills or ~/.claude/skills by default), "
                 "including each folder's references and supporting files. Then rerun the dependency check.")
    return "\n".join(lines)


def require_dependencies():
    result = check()
    if not result["ok"]:
        result["message"] = notice(result)
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-root", action="append", help="Directory containing installed skill folders")
    args = parser.parse_args(argv)
    result = check(args.skills_root)
    if not result["ok"]:
        result["message"] = notice(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
