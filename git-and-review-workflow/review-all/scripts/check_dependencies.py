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

from skill_versions import check_updates, parse_version, read_version, requirements, version_status

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


def check(roots=None, skill_dir=None):
    skill_dir = Path(skill_dir) if skill_dir else Path(__file__).resolve().parent.parent
    own = {"name": "review-all", "path": str(skill_dir), "installed_version": None,
           "url": SOURCE + "git-and-review-workflow/review-all", "status": "available"}
    try:
        own["installed_version"] = read_version(skill_dir)
        parse_version(own["installed_version"])
        required = requirements(skill_dir, [item[0] for item in DEPENDENCIES])
    except (OSError, UnicodeError, ValueError, TypeError, KeyError):
        own.update(status="update_required", reason="VERSION or requirements.json is missing or invalid; update the complete review-all folder")
        return {"ok": False, "status": "installation_required", "skill": own, "dependencies": []}
    roots = installation_roots(roots)
    dependencies = []
    for name, folders, source, resources in DEPENDENCIES:
        candidates = [root / folder for root in roots for folder in folders]
        errors = []
        selected = None
        outdated = None
        installed = None
        limits = required[name]
        for candidate in candidates:
            error = inspect_skill(candidate, name, resources)
            if error is None:
                installed, error = version_status(candidate, limits)
                if error is None:
                    selected = candidate
                    break
                if outdated is None:
                    outdated = {"path": str(candidate), "installed_version": installed, "reason": error}
            errors.append({"path": str(candidate), "reason": error})
        item = {"name": name, "status": "available" if selected else ("update_required" if outdated else "missing_or_invalid"),
                             "path": str(selected) if selected else None, "install_folder": folders[0],
                             "installed_version": installed if selected else None,
                             "required_version": f">={limits['minimum']}, <{limits['maximum_exclusive']}",
                             "url": SOURCE + source, "searched": errors if selected is None else []}
        if selected is None and outdated:
            item.update(outdated)
        dependencies.append(item)
    ready = all(item["status"] == "available" for item in dependencies)
    return {"ok": ready, "status": "ready" if ready else "installation_required", "skill": own, "dependencies": dependencies}


def notice(result):
    lines = ["review-all did not start. Please install or update these complete skill folders:"]
    if result.get("skill", {}).get("status") != "available":
        own = result["skill"]
        lines.append(f"- [review-all]({own['url']}): {own['reason']}")
    for item in result["dependencies"]:
        if item["status"] != "available":
            installed = item.get("installed_version") or "unversioned or missing"
            lines.append(f"- [{item['name']}]({item['url']}): installed {installed}; required {item['required_version']} → {item['install_folder']}")
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
    parser.add_argument("--check-updates", action="store_true", help="Also check published versions through read-only gh API access")
    args = parser.parse_args(argv)
    result = check(args.skills_root)
    if not result["ok"]:
        result["message"] = notice(result)
    if args.check_updates and result["ok"]:
        check_updates(result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
