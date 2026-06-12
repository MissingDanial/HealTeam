"""Helpers for loading monorepo-local packages without installation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def ensure_repo_root_on_path(repo_root: Path) -> None:
    """Make the repo root importable for scripts executed from subdirectories."""

    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def register_package(alias: str, pkg_dir: Path, *, strict: bool = True) -> None:
    """Register `pkg_dir` as an importable module alias in `sys.modules`."""

    if alias in sys.modules:
        return

    init = pkg_dir / "__init__.py"
    if not init.exists():
        if strict:
            raise FileNotFoundError(f"{init} missing")
        return

    spec = importlib.util.spec_from_file_location(
        alias,
        init,
        submodule_search_locations=[str(pkg_dir)],
    )
    if spec is None or spec.loader is None:
        if strict:
            raise ImportError(f"could not build spec for {alias} at {pkg_dir}")
        return

    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)


def bootstrap_local_packages(repo_root: Path, *, strict: bool = True) -> None:
    """Register the local `memory` and `agents` packages in dependency order."""

    packages_dir = repo_root / "packages"
    register_package("healteam_memory", packages_dir / "memory", strict=strict)
    register_package("healteam_agents", packages_dir / "agents", strict=strict)
