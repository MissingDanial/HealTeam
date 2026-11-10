"""Resolve the shared content root without depending on source-tree depth."""

from __future__ import annotations

import os
from pathlib import Path


class ResourceConfigurationError(ValueError):
    """The configured HealTeam content directory is unavailable."""


def find_project_root(start: Path | None = None, root: str | Path | None = None) -> Path:
    """Find the content root, respecting an explicit ``HEALTEAM_ROOT`` first.

    An invalid explicit location is an error, never an invitation to silently
    load a different copy of the cases. Installed applications can point this
    setting at a separately deployed content directory.
    """
    configured = root if root is not None else (os.getenv("HEALTEAM_ROOT") or "").strip() or None
    markers = ("data/cases", "data/schools", "packages/prompts")

    def has_resources(candidate: Path) -> bool:
        return all((candidate / marker).is_dir() for marker in markers)

    if configured is not None:
        if not str(configured).strip():
            raise ResourceConfigurationError("HEALTEAM_ROOT must name a content directory")
        candidate = Path(configured).expanduser().resolve()
        if not has_resources(candidate):
            raise ResourceConfigurationError("HEALTEAM_ROOT is missing required content directories")
        return candidate

    origin = (start or Path(__file__)).resolve()
    if origin.is_file() or origin.suffix == ".py":
        origin = origin.parent
    for candidate in (origin, *origin.parents):
        if has_resources(candidate):
            return candidate
    raise ResourceConfigurationError("HealTeam content is unavailable; configure HEALTEAM_ROOT")
