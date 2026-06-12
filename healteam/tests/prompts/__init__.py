"""healteam.tests.prompts package — re-export test helpers from conftest.py."""
from __future__ import annotations

# Re-export so individual test files can do:
#   from tests.prompts.conftest import PROMPTS_DIR, REPO_ROOT, ...
# Actually conftest isn't importable as a regular module under pytest; we
# keep this __init__ to make `tests/prompts` a discoverable package.
