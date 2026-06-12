"""Shared test fixtures for `tests/prompts/`.

Package registration is handled by the root `tests/conftest.py`.
This module only provides directory-scoped fixtures specific to prompt tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "packages" / "prompts"
SCHOOLS_DIR = REPO_ROOT / "data" / "schools"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def prompts_dir() -> Path:
    return PROMPTS_DIR


@pytest.fixture(scope="session")
def schools_dir() -> Path:
    return SCHOOLS_DIR
