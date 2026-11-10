"""Package runnable source while excluding local training data and secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from datetime import date
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2]
PRUNE = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    ".cache",
    ".local",
    "backups",
    "releases",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".tmp",
    "build",
    "dist",
    "reports",
    "logs",
    ".aws",
    ".opencode",
    ".mavis",
}
REQUIRED = (
    "healteam/README.md",
    "healteam/pyproject.toml",
    "healteam/requirements.lock",
    "healteam/data/cases/teen-truancy-01.json",
    "healteam/data/schools/structural.md",
    "healteam/data/schools/bowen.md",
    "healteam/data/schools/satir.md",
    "healteam/data/rubrics/trial-family-training-v1.json",
    "healteam/packages/prompts/doctor.md",
    "healteam/packages/memory/runtime_lease.py",
    "healteam/packages/memory/model_usage.py",
    "healteam/packages/memory/evaluation_contracts.py",
    "healteam/packages/memory/review_store.py",
    "healteam/apps/api/app/services/live_training.py",
    "healteam/apps/api/app/services/calibration.py",
    "healteam/apps/api/app/routers/reviews.py",
    "healteam/apps/api/app/main.py",
    "healteam/apps/web/package-lock.json",
    "healteam/scripts/smoke.py",
    "healteam/packages/memory/backup.py",
    "healteam/scripts/local_app.py",
    "healteam/scripts/local_runtime.py",
    "healteam/scripts/prepare_local.py",
    "healteam/scripts/manage_data.py",
    "healteam/scripts/package_local.py",
    "healteam/安装与构建.cmd",
    "healteam/开始使用.cmd",
    "healteam/停止服务.cmd",
    "healteam/运行检查.cmd",
    "healteam/备份数据.cmd",
    "healteam/验证备份.cmd",
    "healteam/恢复数据.cmd",
    "healteam/docs/s5-runbook.md",
    "healteam/docs/s5-delivery.md",
    "healteam/docs/s5-verification.json",
)


def allowed(path: Path) -> bool:
    relative = path.relative_to(SOURCE)
    name = path.name.lower()
    if name.startswith(".env") and name != ".env.example":
        return False
    if name.endswith(
        (
            ".pyc",
            ".pyo",
            ".log",
            ".sqlite3",
            ".sqlite",
            ".db",
            ".lock",
            ".pem",
            ".key",
            ".tsbuildinfo",
            ".zip",
            ".whl",
            ".bak",
            ".backup",
            ".7z",
        )
    ):
        # requirements.lock and dependency lockfiles are source, not DB locks.
        if path.name != "requirements.lock":
            return False
    if any(marker in name for marker in (".sqlite3-", ".sqlite-", ".db-")):
        return False
    if len(relative.parts) >= 3 and relative.parts[:2] == ("healteam", "data"):
        return relative.parts[2] in {"cases", "schools", "rubrics", "case-school-matrix.md"}
    return True


def verify_archive(archive: Path) -> int:
    with zipfile.ZipFile(archive) as bundle:
        assert bundle.testzip() is None, "Source archive is corrupt"
        names = bundle.namelist()
        required = {f"{SOURCE.name}/{name}" for name in REQUIRED}
        missing = required - set(names)
        if missing:
            raise RuntimeError(f"Missing runtime source/resources: {sorted(missing)}")
        if len(names) != len(set(names)):
            raise RuntimeError("Duplicate archive entries")
        for name in names:
            relative = Path(name).relative_to(SOURCE.name)
            if any(part in PRUNE or part.endswith(".egg-info") for part in relative.parts):
                raise RuntimeError("Excluded directory entered source archive")
            if not allowed(SOURCE / relative):
                raise RuntimeError("Local data or configuration entered source archive")
    return len(names)


def verify_extracted(archive: Path) -> None:
    cache = SOURCE / "healteam" / ".cache"
    cache.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="source-acceptance-", dir=cache) as temporary:
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temporary)
        extracted = Path(temporary) / SOURCE.name / "healteam"
        code = """
import os, runpy, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
os.environ['HEALTEAM_ROOT'] = str(root)
os.environ['HEALTEAM_MODEL_MODE'] = 'mock'
os.environ.pop('HEALTEAM_DATABASE_URL', None)
sys.path[:0] = [str(root), str(root / 'apps' / 'api')]
from repo_bootstrap import bootstrap_local_packages
bootstrap_local_packages(root)
import app.main, healteam_memory.training_store
assert Path(app.main.__file__).is_relative_to(root)
assert Path(healteam_memory.training_store.__file__).is_relative_to(root)
runpy.run_path(str(root / 'scripts' / 'smoke.py'), run_name='__main__')
"""
        result = subprocess.run(
            [sys.executable, "-I", "-c", code, str(extracted)],
            cwd=extracted,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=90,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"Extracted source smoke failed:\n{result.stdout}\n{result.stderr}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-run", action="store_true", help="Run offline smoke using extracted source")
    args = parser.parse_args()
    archive = args.output or SOURCE.parent / "output" / f"HealTeam-S5-{date.today().isoformat()}.zip"
    archive = archive.resolve()
    if archive.is_relative_to(SOURCE.resolve()):
        parser.error("Write the source archive outside the project to exclude local output")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for current, directories, files in os.walk(SOURCE):
            directories[:] = sorted(
                name for name in directories if name not in PRUNE and not name.endswith(".egg-info")
            )
            for name in sorted(files):
                path = Path(current) / name
                if allowed(path):
                    bundle.write(path, path.relative_to(SOURCE.parent).as_posix())
    count = verify_archive(archive)
    if args.verify_run:
        verify_extracted(archive)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    print(
        json.dumps(
            {
                "archive": str(archive.resolve()),
                "files": count,
                "bytes": archive.stat().st_size,
                "sha256": digest,
                "extracted_smoke": "passed" if args.verify_run else "not_run",
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
