"""Local release compatibility and optional, read-only published-version checks."""
import json
from pathlib import Path
import re
import subprocess


VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
CATALOG_ENDPOINT = "repos/alchambron/skills/contents/skill-versions.json?ref=main"


def parse_version(value):
    match = VERSION_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Version must be a stable MAJOR.MINOR.PATCH value")
    return tuple(int(part) for part in match.groups())


def read_version(folder):
    return (Path(folder) / "VERSION").read_text(encoding="utf-8").strip()


def requirements(skill_dir, names):
    data = json.loads((Path(skill_dir) / "requirements.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported requirements.json schema")
    required = data.get("requires")
    if not isinstance(required, dict) or set(required) != set(names):
        raise ValueError("requirements.json must declare every companion skill")
    for limits in required.values():
        if not isinstance(limits, dict) or parse_version(limits.get("minimum")) >= parse_version(limits.get("maximum_exclusive")):
            raise ValueError("Invalid compatibility range in requirements.json")
    return required


def version_status(folder, limits):
    installed = None
    try:
        installed = read_version(folder)
        parsed = parse_version(installed)
    except (OSError, UnicodeError, ValueError):
        return installed, "VERSION is missing, unreadable or invalid; update the complete skill folder"
    if not parse_version(limits["minimum"]) <= parsed < parse_version(limits["maximum_exclusive"]):
        return installed, "Installed version is outside the supported range; update review-all or install a compatible companion"
    return installed, None


def check_updates(result, runner=None):
    """Remote failure never means current; compatibility failures remain blocked."""
    runner = runner or subprocess.run
    try:
        response = runner(["gh", "api", "--hostname", "github.com", CATALOG_ENDPOINT,
                           "-H", "Accept: application/vnd.github.raw+json"],
                          capture_output=True, text=True, timeout=10, check=False)
        if response.returncode:
            raise ValueError("Published version catalog unavailable")
        catalog = json.loads(response.stdout)
        if not isinstance(catalog, dict) or catalog.get("schema_version") != 1 or not isinstance(catalog.get("skills"), dict):
            raise ValueError("Invalid published version catalog")
        entries = [result["skill"], *result["dependencies"]]
        # Validate the entire relevant catalog before claiming any freshness.
        versions = {entry["name"]: catalog["skills"][entry["name"]]["version"] for entry in entries}
        for version in versions.values():
            parse_version(version)
        updates = []
        for entry in entries:
            installed = entry.get("installed_version")
            latest = versions[entry["name"]]
            try:
                newer = parse_version(installed) < parse_version(latest)
            except ValueError:
                newer = False  # Missing/invalid versions already block the local guard.
            if newer:
                updates.append({"name": entry["name"], "installed_version": installed,
                                "latest_version": latest, "url": entry["url"]})
        result["update_check"] = {"status": "checked", "updates": updates}
        if updates:
            result["update_message"] = "New skill releases are available (no files were changed):\n" + "\n".join(
                f"- [{entry['name']}]({entry['url']}): installed {entry['installed_version']}; latest {entry['latest_version']}"
                for entry in updates)
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        result["update_check"] = {"status": "unavailable", "updates": []}
        result["update_message"] = ("Could not check published skill versions. Latest versions are unknown; "
                                    "only local compatibility was checked. No files were changed.")
    return result
