"""Offline, verified SQLite backups. Archives contain sensitive training data.

Only app-owned schema v1/v2 databases are accepted. Restoring never runs model
calls, migrates a schema, or rewrites the saved conversations/reviews.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import zipfile
from collections import defaultdict
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from .contracts import CreateSessionCommand, EventEnvelope, SessionSnapshot, SessionState
from .evaluation_contracts import Assessment, ReviewRecord, ReviewSubmission
from .review_store import _validate_evidence, review_context
from .runtime_lease import RuntimeInUseError, RuntimeLease
from .training_models import TrainingBase
from .training_store import ACTIVE_STATES, StoreError, TrainingRecord, _fingerprint

FORMAT = "healteam-sqlite-backup"
FORMAT_VERSION = 1
DATABASE_ENTRY = "database.sqlite3"
MANIFEST_ENTRY = "manifest.json"
MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_ARCHIVE_BYTES = MAX_DATABASE_BYTES + MAX_MANIFEST_BYTES + 1024 * 1024
MAX_COMPRESSION_RATIO = 200
_TABLES_V1 = {"training_sessions", "training_events", "training_receipts"}
_TABLES_V2 = _TABLES_V1 | {"training_expert_reviews"}


class BackupError(RuntimeError):
    """Stable error code only; never include database contents or credentials."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DatabaseInfo:
    schema_version: int
    table_counts: dict[str, int]


def database_path(database: str | Path) -> Path:
    raw = str(database)
    try:
        if "://" in raw:
            url = make_url(raw)
            if url.get_backend_name() != "sqlite" or url.query:
                raise BackupError("invalid_database_path")
            raw = url.database or ""
        if not raw.strip() or raw == ":memory:":
            raise BackupError("invalid_database_path")
        path = Path(raw).expanduser()
        if path.is_symlink():
            raise BackupError("symlink_database_not_supported")
        return path.resolve()
    except (ArgumentError, ValueError, TypeError, OSError):
        raise BackupError("invalid_database_path") from None


def _readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 8 * 1024 * 1024)
    connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 1024 * 1024)
    deadline = time.monotonic() + 30
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    connection.execute("PRAGMA trusted_schema = OFF")
    connection.execute("PRAGMA query_only = ON")
    return connection


def _json(raw: str) -> Any:
    return json.loads(raw, parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))


def _hash_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, *, missing: str) -> None:
    try:
        if not stat.S_ISREG(path.stat().st_mode):
            raise BackupError("not_a_regular_file")
    except FileNotFoundError:
        raise BackupError(missing) from None


def validate_database(database: str | Path) -> DatabaseInfo:
    """Read only; reject unfamiliar objects and broken protocol references."""
    path = database_path(database)
    _regular_file(path, missing="database_not_found")
    if path.stat().st_size > MAX_DATABASE_BYTES:
        raise BackupError("database_too_large")
    try:
        with closing(_readonly(path)) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in {1, 2}:
                raise BackupError("unsupported_database_version")
            objects = connection.execute(
                "SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if any(row["type"] in {"view", "trigger"} for row in objects):
                raise BackupError("unexpected_database_objects")
            tables = {row["name"] for row in objects if row["type"] == "table"}
            expected = _TABLES_V1 if version == 1 else _TABLES_V2
            if tables != expected:
                raise BackupError("unexpected_database_schema")
            if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
                raise BackupError("database_integrity_failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise BackupError("database_reference_failed")
            for name in expected:
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')}
                if columns != set(TrainingBase.metadata.tables[name].columns.keys()):
                    raise BackupError("unexpected_database_schema")
            counts = {
                name: connection.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]
                for name in sorted(expected)
            }
            _validate_records(connection, version)
            return DatabaseInfo(version, counts)
    except BackupError:
        raise
    except (
        sqlite3.Error,
        StoreError,
        ValueError,
        KeyError,
        TypeError,
        AttributeError,
        OverflowError,
        RecursionError,
    ):
        raise BackupError("invalid_database_records") from None


def _validate_records(connection: sqlite3.Connection, version: int) -> None:
    events: dict[str, list[EventEnvelope]] = defaultdict(list)
    for row in connection.execute("SELECT * FROM training_events ORDER BY session_id,seq"):
        event = EventEnvelope.model_validate_json(row["envelope_json"])
        if (
            str(event.session_id) != row["session_id"]
            or event.seq != row["seq"]
            or str(event.event_id) != row["event_id"]
        ):
            raise BackupError("database_reference_failed")
        events[row["session_id"]].append(event)
    records: dict[str, TrainingRecord] = {}
    active = 0
    for row in connection.execute("SELECT * FROM training_sessions"):
        snapshot = SessionSnapshot.model_validate_json(row["snapshot_json"])
        command = CreateSessionCommand.model_validate_json(row["request_json"])
        config, case = _json(row["config_json"]), _json(row["case_json"])
        report = _json(row["report_json"]) if row["report_json"] is not None else None
        known_events = events.pop(row["session_id"], [])
        if (
            str(snapshot.session_id) != row["session_id"]
            or str(command.client_request_id) != row["client_request_id"]
            or _fingerprint(command.model_dump(mode="json")) != row["request_fingerprint"]
            or command.case_id != snapshot.case_id
            or command.school != snapshot.school
            or command.mode != snapshot.mode
            or command.max_rounds != snapshot.max_rounds
            or not isinstance(config, dict)
            or not isinstance(case, dict)
            or case.get("case_id") != snapshot.case_id
            or case.get("version") != snapshot.case_version
            or config.get("model_mode", "mock") != snapshot.model_mode.value
            or row["owner"] != "local"
            or row["revision"] < 0
            or not 0 <= row["completed_rounds"] <= snapshot.current_round
            or bool(row["active"]) != (snapshot.state in ACTIVE_STATES)
            or [event.seq for event in known_events] != list(range(1, snapshot.last_event_seq + 1))
        ):
            raise BackupError("database_reference_failed")
        if report is not None and (
            not isinstance(report, dict)
            or snapshot.state != SessionState.COMPLETED
            or report.get("session_id", row["session_id"]) != row["session_id"]
        ):
            raise BackupError("database_reference_failed")
        if snapshot.state == SessionState.COMPLETED and report is None:
            raise BackupError("database_reference_failed")
        if report is not None and report.get("assessment") is not None:
            assessment = Assessment.model_validate(report["assessment"])
            public = {
                event.seq
                for event in known_events
                if event.type.value == "utterance" and event.visibility.value == "public"
            }
            if not set(assessment.reviewed_event_seq) <= public:
                raise BackupError("database_reference_failed")
        active += bool(row["active"])
        records[row["session_id"]] = TrainingRecord(
            snapshot,
            config,
            case,
            tuple(known_events),
            report,
            row["completed_rounds"],
            row["created_at"],
            row["updated_at"],
            row["error_code"],
            row["revision"],
        )
    if events or active > 1:
        raise BackupError("database_reference_failed")
    for row in connection.execute("SELECT * FROM training_receipts"):
        record = records.get(row["session_id"])
        response = _json(row["response_json"])
        if (
            record is None
            or not isinstance(response, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", row["fingerprint"])
        ):
            raise BackupError("database_reference_failed")
        if response.get("session_id", row["session_id"]) != row["session_id"]:
            raise BackupError("database_reference_failed")
        if "client_message_id" in response:
            identifier = str(UUID(response["client_message_id"]))
            matching = [
                event
                for event in record.events
                if event.type.value == "utterance"
                and event.source.value == "human"
                and event.payload.get("client_message_id") == identifier
            ]
            if (
                identifier != row["receipt_key"]
                or len(matching) != 1
                or response.get("accepted_turn") != matching[0].round
            ):
                raise BackupError("database_reference_failed")
    if version == 2:
        revisions: dict[str, int] = defaultdict(int)
        for row in connection.execute("SELECT * FROM training_expert_reviews ORDER BY session_id,revision"):
            record = records.get(row["session_id"])
            submission = ReviewSubmission.model_validate_json(row["submission_json"])
            review = ReviewRecord.model_validate_json(row["review_json"])
            if record is None or record.report is None:
                raise BackupError("database_reference_failed")
            binding = review_context(record)
            _validate_evidence(record, submission, record.report["rubric"])
            revisions[row["session_id"]] += 1
            if (
                row["revision"] != revisions[row["session_id"]]
                or submission.expected_revision != row["revision"] - 1
                or str(review.review_id) != row["review_id"]
                or str(review.session_id) != row["session_id"]
                or review.revision != row["revision"]
                or review.created_at.isoformat() != row["created_at"]
                or str(submission.client_review_id) != row["client_review_id"]
                or review.client_review_id != submission.client_review_id
                or _fingerprint(submission.model_dump(mode="json")) != row["request_fingerprint"]
                or row["report_hash"] != binding["report_hash"]
                or row["rubric_hash"] != binding["rubric_hash"]
                or review.report_hash != binding["report_hash"]
                or review.rubric_hash != binding["rubric_hash"]
            ):
                raise BackupError("database_reference_failed")
            expected = {
                key: value
                for key, value in submission.model_dump(mode="json").items()
                if key != "expected_revision"
            }
            actual = review.model_dump(mode="json")
            if any(actual[key] != value for key, value in expected.items()):
                raise BackupError("database_reference_failed")


def _manifest(database: Path, info: DatabaseInfo) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "database_entry": DATABASE_ENTRY,
        "database_size": database.stat().st_size,
        "database_sha256": _hash_file(database),
        **asdict(info),
    }


def _extract_verified(archive: Path, destination: Path) -> dict[str, Any]:
    _regular_file(archive, missing="backup_not_found")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise BackupError("backup_too_large")
    try:
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if len(names) != 2 or set(names) != {MANIFEST_ENTRY, DATABASE_ENTRY}:
                raise BackupError("invalid_backup_entries")
            for item in bundle.infolist():
                mode = item.external_attr >> 16
                if (
                    item.flag_bits & 1
                    or stat.S_ISLNK(mode)
                    or item.is_dir()
                    or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
                ):
                    raise BackupError("invalid_backup_entries")
            document = bundle.getinfo(MANIFEST_ENTRY)
            data = bundle.getinfo(DATABASE_ENTRY)
            if (
                document.file_size > MAX_MANIFEST_BYTES
                or not 0 < data.file_size <= MAX_DATABASE_BYTES
                or data.file_size > max(1, data.compress_size) * MAX_COMPRESSION_RATIO
            ):
                raise BackupError("backup_too_large")
            manifest = _json(bundle.read(MANIFEST_ENTRY).decode("utf-8"))
            required = {
                "format",
                "format_version",
                "created_at",
                "database_entry",
                "database_size",
                "database_sha256",
                "schema_version",
                "table_counts",
            }
            if not isinstance(manifest, dict) or set(manifest) != required:
                raise BackupError("invalid_backup_manifest")
            if (
                manifest["format"] != FORMAT
                or type(manifest["format_version"]) is not int
                or manifest["format_version"] != FORMAT_VERSION
                or manifest["database_entry"] != DATABASE_ENTRY
            ):
                raise BackupError("invalid_backup_manifest")
            if type(manifest["schema_version"]) is not int or manifest["schema_version"] not in {1, 2}:
                raise BackupError("unsupported_database_version")
            if (
                type(manifest["database_size"]) is not int
                or manifest["database_size"] != data.file_size
                or not isinstance(manifest["database_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", manifest["database_sha256"])
            ):
                raise BackupError("invalid_backup_manifest")
            datetime.fromisoformat(manifest["created_at"])
            expected_tables = _TABLES_V1 if manifest["schema_version"] == 1 else _TABLES_V2
            if (
                not isinstance(manifest["table_counts"], dict)
                or set(manifest["table_counts"]) != expected_tables
                or any(type(count) is not int or count < 0 for count in manifest["table_counts"].values())
            ):
                raise BackupError("invalid_backup_manifest")
            digest = sha256()
            copied = 0
            with bundle.open(DATABASE_ENTRY) as source, destination.open("xb") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    copied += len(chunk)
                    if copied > data.file_size or copied > MAX_DATABASE_BYTES:
                        raise BackupError("backup_too_large")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if copied != data.file_size or digest.hexdigest() != manifest["database_sha256"]:
                raise BackupError("backup_checksum_mismatch")
        info = validate_database(destination)
        if info.schema_version != manifest["schema_version"] or info.table_counts != manifest["table_counts"]:
            raise BackupError("backup_manifest_mismatch")
        return manifest
    except BackupError:
        raise
    except (
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        UnicodeError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
    ):
        raise BackupError("invalid_backup") from None


def verify_backup(archive: str | Path) -> dict[str, Any]:
    """Inspect only fixed ZIP entries; never extract names supplied by a file."""
    try:
        with tempfile.TemporaryDirectory(prefix="healteam-backup-verify-") as temporary:
            return _extract_verified(Path(archive).expanduser().resolve(), Path(temporary) / DATABASE_ENTRY)
    except BackupError:
        raise
    except (OSError, sqlite3.Error):
        raise BackupError("backup_verification_failed") from None


def _copy_snapshot(database: Path, copied: Path) -> None:
    with closing(_readonly(database)) as source, closing(sqlite3.connect(copied)) as target:
        source.backup(target, pages=256, sleep=0.01)
        target.execute("PRAGMA journal_mode = DELETE")


def _create_locked(database: Path, archive: Path, *, lock_source: bool = True) -> dict[str, Any]:
    _regular_file(database, missing="database_not_found")
    if archive.exists():
        raise BackupError("backup_already_exists")
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".healteam-backup-", dir=archive.parent) as temporary:
        folder = Path(temporary)
        copied = folder / DATABASE_ENTRY
        if lock_source:
            with closing(sqlite3.connect(database, timeout=2)) as writer:
                writer.execute("PRAGMA trusted_schema = OFF")
                writer.execute("BEGIN IMMEDIATE")
                _copy_snapshot(database, copied)
                writer.rollback()
        else:
            # Restore already holds a platform write guard across both this
            # safety snapshot and the final atomic replacement.
            _copy_snapshot(database, copied)
        info = validate_database(copied)
        manifest = _manifest(copied, info)
        staged = folder / "backup.zip"
        with zipfile.ZipFile(staged, "w") as bundle:
            bundle.writestr(
                MANIFEST_ENTRY,
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
                compress_type=zipfile.ZIP_DEFLATED,
            )
            bundle.write(copied, DATABASE_ENTRY, compress_type=zipfile.ZIP_STORED)
        verify_backup(staged)
        # Windows rename and POSIX hard-link publication both refuse a raced
        # destination, without exposing a partly copied archive.
        if os.name == "nt":
            os.rename(staged, archive)
        else:
            os.link(staged, archive)
        return manifest


def _signature(path: Path) -> tuple[int, int, int, int, str]:
    attributes = path.stat()
    return attributes.st_dev, attributes.st_ino, attributes.st_size, attributes.st_mtime_ns, _hash_file(path)


@contextmanager
def _replacement_guard(path: Path):
    """Prevent SQLite writes after the safety snapshot through replacement.

    Windows denies write-sharing until native POSIX-style atomic rename. On
    POSIX a dedicated child holds SQLite's write reservation: closing another
    descriptor in this process must not accidentally release its fcntl locks.
    """
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel.CreateFileW(str(path), 0x80000000, 0x1 | 0x4, None, 3, 0x80, None)
        if handle == ctypes.c_void_p(-1).value:
            raise BackupError("database_in_use")
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except BaseException:
            kernel.CloseHandle(handle)
            raise
        with os.fdopen(descriptor, "rb") as held:
            attributes = os.fstat(held.fileno())
            digest = sha256()
            for chunk in iter(lambda: held.read(1024 * 1024), b""):
                digest.update(chunk)
            identity = (
                attributes.st_dev,
                attributes.st_ino,
                attributes.st_size,
                attributes.st_mtime_ns,
                digest.hexdigest(),
            )
            if _signature(path) != identity:
                raise BackupError("restore_target_changed")
            yield identity
    else:
        import select

        code = """
import json,os,sqlite3,sys
connection=sqlite3.connect(sys.argv[1],timeout=2)
try:
 connection.execute('PRAGMA trusted_schema=OFF')
 connection.execute('BEGIN IMMEDIATE')
 info=os.stat(sys.argv[1])
 print(json.dumps({'device':info.st_dev,'inode':info.st_ino}),flush=True)
 sys.stdin.readline()
finally:
 connection.rollback()
 connection.close()
"""
        environment = {
            key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TMPDIR") if key in os.environ
        }
        process = subprocess.Popen(
            [sys.executable, "-I", "-c", code, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env=environment,
        )
        try:
            if not select.select([process.stdout], [], [], 10)[0]:
                raise BackupError("database_in_use")
            try:
                owned = json.loads(process.stdout.readline())
            except (ValueError, TypeError):
                raise BackupError("database_in_use") from None
            identity = _signature(path)
            if (owned["device"], owned["inode"]) != identity[:2]:
                raise BackupError("restore_target_changed")
            yield identity
        finally:
            try:
                process.communicate("\n", timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _replace_existing(candidate: Path, target: Path) -> None:
    if os.name != "nt":
        os.replace(candidate, target)
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(candidate), 0x10000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise BackupError("backup_restore_failed")
    try:
        encoded = str(target).encode("utf-16-le")

        class RenameInfo(ctypes.Structure):
            _fields_ = [
                ("flags", wintypes.DWORD),
                ("directory", wintypes.HANDLE),
                ("length", wintypes.DWORD),
                ("name", wintypes.WCHAR * (len(encoded) // 2 + 1)),
            ]

        information = RenameInfo()
        information.flags = 0x1 | 0x2  # REPLACE_IF_EXISTS | POSIX_SEMANTICS.
        information.length = len(encoded)
        ctypes.memmove(ctypes.addressof(information) + RenameInfo.name.offset, encoded, len(encoded))
        # FileRenameInfoEx=22. No release-and-replace fallback on unsupported
        # Windows/filesystems: retain the old DB and restore to a new path.
        if not kernel.SetFileInformationByHandle(
            handle, 22, ctypes.byref(information), ctypes.sizeof(information)
        ):
            raise BackupError("restore_atomic_replace_unavailable")
    finally:
        kernel.CloseHandle(handle)


def _publish_new(candidate: Path, target: Path) -> None:
    try:
        if os.name == "nt":
            os.rename(candidate, target)
        else:
            os.link(candidate, target)
    except FileExistsError:
        raise BackupError("restore_target_exists") from None


def create_backup(database: str | Path, archive: str | Path) -> dict[str, Any]:
    path, output = database_path(database), Path(archive).expanduser().resolve()
    if path == output:
        raise BackupError("source_target_same")
    try:
        with RuntimeLease(path):
            return _create_locked(path, output)
    except BackupError:
        raise
    except RuntimeInUseError:
        raise BackupError("runtime_in_use") from None
    except (OSError, sqlite3.Error):
        raise BackupError("backup_creation_failed") from None


def restore_backup(archive: str | Path, database: str | Path, *, replace: bool = False) -> dict[str, Any]:
    source, target = Path(archive).expanduser().resolve(), database_path(database)
    if source == target:
        raise BackupError("source_target_same")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with RuntimeLease(target):
            existed = target.exists()
            if existed and not replace:
                raise BackupError("restore_target_exists")
            with tempfile.TemporaryDirectory(prefix=".healteam-restore-", dir=target.parent) as temporary:
                candidate = Path(temporary) / DATABASE_ENTRY
                manifest = _extract_verified(source, candidate)
                safety = None
                if existed:
                    safety = (
                        target.parent
                        / "backups"
                        / f"before-restore-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.healteam-backup.zip"
                    )
                    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                        raise BackupError("database_sidecars_present")
                    with closing(_readonly(target)) as connection:
                        if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal":
                            raise BackupError("restore_target_uses_wal")
                    with _replacement_guard(target) as identity:
                        _create_locked(target, safety, lock_source=False)
                        verify_backup(safety)
                        if any(
                            Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
                        ):
                            raise BackupError("database_sidecars_present")
                        if _signature(target) != identity:
                            raise BackupError("restore_target_changed")
                        _replace_existing(candidate, target)
                else:
                    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
                        raise BackupError("database_sidecars_present")
                    _publish_new(candidate, target)
                return {
                    "status": "restored",
                    "database": str(target),
                    "schema_version": manifest["schema_version"],
                    "table_counts": manifest["table_counts"],
                    "safety_backup": str(safety) if safety else None,
                }
    except BackupError:
        raise
    except RuntimeInUseError:
        raise BackupError("runtime_in_use") from None
    except (OSError, sqlite3.Error):
        raise BackupError("backup_restore_failed") from None


__all__ = [
    "BackupError",
    "DatabaseInfo",
    "create_backup",
    "verify_backup",
    "restore_backup",
    "validate_database",
    "database_path",
]
