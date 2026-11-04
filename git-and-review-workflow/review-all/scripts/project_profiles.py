"""Repository routing shared by review collection and presentation.

Profiles select guidance, not runtime permissions or proof of compatibility.
Unknown repositories retain their own conventions and startup procedure.
"""

PROFILES = {
    "enerzam/joulemv": ("joulemv", "JouleMV"),
    "enerzam/websynco_react_front": ("websynco-react-front", "WebSynco React frontend"),
    "enerzam/websynco": ("websynco-backend", "WebSynco backend"),
}


def project_profile(repository):
    """Return fresh presentation metadata for an exact owner/repository identity."""
    project_type, label = PROFILES.get(
        repository.casefold() if isinstance(repository, str) else "",
        ("other", "Repository-specific"),
    )
    return {"project_type": project_type, "project_label": label}
