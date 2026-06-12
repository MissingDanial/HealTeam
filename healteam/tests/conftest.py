"""Root-level test fixtures for the entire healteam test suite.

Registers `healteam_memory` and `healteam_agents` as importable packages
so all tests can do `from healteam_memory import ...` without `poetry install`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PACKAGES_DIR = REPO_ROOT / "packages"
PROMPTS_DIR = PACKAGES_DIR / "prompts"
SCHOOLS_DIR = REPO_ROOT / "data" / "schools"


def _register_package(alias: str, pkg_dir: Path) -> None:
    """Load `pkg_dir/__init__.py` and register it as `alias` in `sys.modules`."""
    init = pkg_dir / "__init__.py"
    if not init.exists():
        raise FileNotFoundError(f"{init} missing")
    spec = importlib.util.spec_from_file_location(
        alias, init, submodule_search_locations=[str(pkg_dir)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build spec for {alias} at {pkg_dir}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[alias] = module
    spec.loader.exec_module(module)


# Order matters: agents imports from memory, so memory first.
_register_package("healteam_memory", PACKAGES_DIR / "memory")
_register_package("healteam_agents", PACKAGES_DIR / "agents")


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def prompts_dir() -> Path:
    return PROMPTS_DIR


@pytest.fixture(scope="session")
def schools_dir() -> Path:
    return SCHOOLS_DIR
