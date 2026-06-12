"""Root-level test fixtures for the entire healteam test suite.

Registers `healteam_memory` and `healteam_agents` as importable packages
so all tests can do `from healteam_memory import ...` without `poetry install`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
from repo_bootstrap import bootstrap_local_packages

PACKAGES_DIR = REPO_ROOT / "packages"
PROMPTS_DIR = PACKAGES_DIR / "prompts"
SCHOOLS_DIR = REPO_ROOT / "data" / "schools"


# Order matters: agents imports from memory, so memory first.
bootstrap_local_packages(REPO_ROOT)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def prompts_dir() -> Path:
    return PROMPTS_DIR


@pytest.fixture(scope="session")
def schools_dir() -> Path:
    return SCHOOLS_DIR
