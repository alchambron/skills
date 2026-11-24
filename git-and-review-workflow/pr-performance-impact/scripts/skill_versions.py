"""Local VERSION reading and optional, read-only published-version checks."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple


VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
CATALOG_ENDPOINT = "repos/alchambron/skills/contents/skill-versions.json?ref=main"
SKILL_NAME = "pr-performance-impact"
SKILL_URL = "https://github.com/alchambron/skills/tree/main/git-and-review-workflow/pr-performance-impact"


def parse_version(value: Any) -> Tuple[int, int, int]:
    match = VERSION_PATTERN.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Version must be a stable MAJOR.MINOR.PATCH value")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def read_version(skill_dir: Path) -> str:
    version = (Path(skill_dir) / "VERSION").read_text(encoding="utf-8").strip()
    parse_version(version)
    return version


def check_update(
    skill_dir: Path,
    runner: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Check local VERSION and optionally compare against published skill-versions.json."""
    installed: Optional[str] = None
    try:
        installed = read_version(skill_dir)
    except (OSError, UnicodeError, ValueError) as exc:
        return {
            "name": SKILL_NAME,
            "installed_version": installed,
            "status": "invalid_local_version",
            "error": str(exc),
            "url": SKILL_URL,
        }

    run = runner or subprocess.run
    try:
        response = run(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                CATALOG_ENDPOINT,
                "-H",
                "Accept: application/vnd.github.raw+json",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if response.returncode:
            raise ValueError("Published version catalog unavailable")
        catalog = json.loads(response.stdout)
        if (
            not isinstance(catalog, dict)
            or catalog.get("schema_version") != 1
            or not isinstance(catalog.get("skills"), dict)
        ):
            raise ValueError("Invalid published version catalog")
        latest = catalog["skills"][SKILL_NAME]["version"]
        newer = parse_version(installed) < parse_version(latest)
        out: Dict[str, Any] = {
            "name": SKILL_NAME,
            "installed_version": installed,
            "latest_version": latest,
            "update_available": newer,
            "status": "checked",
            "url": SKILL_URL,
        }
        if newer:
            out["update_message"] = (
                f"New release available for [{SKILL_NAME}]({SKILL_URL}): "
                f"installed {installed}; latest {latest} (no files were changed)."
            )
        return out
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
        return {
            "name": SKILL_NAME,
            "installed_version": installed,
            "latest_version": None,
            "update_available": False,
            "status": "unavailable",
            "url": SKILL_URL,
            "update_message": (
                "Could not check published skill versions. Latest version is unknown; "
                "using installed copy without changing files."
            ),
        }
