"""Sensitive backups remain complete; untrusted restores never replace early."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sqlite3
import stat
import subprocess
import sys
import zipfile
from contextlib import closing, contextmanager
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from healteam_memory import backup
from healteam_memory.backup import (
    BackupError,
    create_backup,
    restore_backup,
    validate_database,
    verify_backup,
)
from healteam_memory.contracts import CreateSessionCommand, transition_snapshot
from healteam_memory.runtime_lease import RuntimeLease
from healteam_memory.training_store import Mutation, TrainingRepository

from tests.memory.test_review_store import stored_report, submission, trial_rubric

ROOT = Path(__file__).resolve().parents[2]


def seed(path, *, reviews=True, waiting=True):
    async def scenario():
        repository = TrainingRepository(path)
        report_id = await stored_report(repository, rubric=trial_rubric())
        command = None
        if reviews:
            command = await submission(repository, report_id)
            await repository.submit_review(report_id, command)
        waiting_id = None
        if waiting:
            created = await repository.create(
                CreateSessionCommand(client_request_id=uuid4(), case_id="teen-truancy-01", school="satir"),
                config_snapshot={"model_mode": "mock"},
                case_snapshot={"case_id": "teen-truancy-01", "version": "1.0"},
            )
            waiting_id = created.record.snapshot.session_id
            await repository.transact(
                waiting_id,
                lambda record: Mutation(snapshot=transition_snapshot(record.snapshot, "awaiting_input")),
            )
        await repository.dispose()
        return report_id, command, waiting_id

    return asyncio.run(scenario())


def table_data(path):
    with closing(sqlite3.connect(path)) as connection:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return {name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall()) for (name,) in tables}


def rewrite_archive(original, output, *, manifest_change=None, database_change=None, extras=None):
    with zipfile.ZipFile(original) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        database = bundle.read("database.sqlite3")
    if database_change:
        database = database_change(database)
    if manifest_change:
        manifest_change(manifest)
    with zipfile.ZipFile(output, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("database.sqlite3", database)
        for name, content in (extras or {}).items():
            bundle.writestr(name, content)


def test_backup_restore_preserves_waiting_report_receipts_and_review_idempotency(tmp_path):
    source, archive, target = (
        tmp_path / "original.sqlite3",
        tmp_path / "backup.zip",
        tmp_path / "restored.sqlite3",
    )
    report_id, command, waiting_id = seed(source)
    expected = table_data(source)
    manifest = create_backup(source, archive)
    assert manifest == verify_backup(archive)
    assert manifest["schema_version"] == 2
    assert manifest["table_counts"]["training_expert_reviews"] == 1
    assert set(zipfile.ZipFile(archive).namelist()) == {"manifest.json", "database.sqlite3"}
    result = restore_backup(archive, target)
    assert result["safety_backup"] is None and table_data(target) == expected

    async def reopen():
        repository = TrainingRepository(target)
        await repository.initialize()
        assert (await repository.get(waiting_id)).snapshot.state.value == "awaiting_input"
        assert (await repository.submit_review(report_id, command)).replayed

        def must_not_run(_record):
            raise AssertionError("Receipt replays must not execute a mutation")

        receipt = await repository.transact(
            report_id, must_not_run, receipt_key="learner-1", receipt_payload={"content": "请说说你的感受。"}
        )
        assert receipt.replayed and receipt.receipt == {"accepted_turn": 1}
        await repository.dispose()

    asyncio.run(reopen())


def test_replacement_requires_explicit_flag_and_creates_verified_safety_backup(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    seed(source)
    seed(target, reviews=False, waiting=False)
    before = table_data(target)
    archive = tmp_path / "incoming.zip"
    create_backup(source, archive)
    with pytest.raises(BackupError, match="restore_target_exists"):
        restore_backup(archive, target)
    assert table_data(target) == before
    result = restore_backup(archive, target, replace=True)
    safety = Path(result["safety_backup"])
    assert safety.is_relative_to(tmp_path) and safety.exists()
    verify_backup(safety)
    recovered_old = tmp_path / "old-recovered.sqlite3"
    restore_backup(safety, recovered_old)
    assert table_data(recovered_old) == before
    assert table_data(target) == table_data(source)


def test_schema_one_is_restored_without_automatic_migration(tmp_path):
    source = tmp_path / "v1.sqlite3"
    seed(source, reviews=False)
    with sqlite3.connect(source) as connection:
        connection.execute("DROP TABLE training_expert_reviews")
        connection.execute("PRAGMA user_version = 1")
    archive, target = tmp_path / "v1.zip", tmp_path / "v1-restored.sqlite3"
    create_backup(source, archive)
    assert restore_backup(archive, target)["schema_version"] == 1
    with sqlite3.connect(target) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='training_expert_reviews'"
            ).fetchone()
            is None
        )
    assert table_data(target) == table_data(source)


@pytest.mark.parametrize(
    "object_sql",
    [
        "CREATE TABLE unrelated_secret(value TEXT)",
        "CREATE VIEW unrelated_view AS SELECT session_id FROM training_sessions",
        "CREATE TRIGGER unrelated_trigger AFTER INSERT ON training_sessions BEGIN SELECT 1; END",
    ],
)
def test_unknown_database_objects_are_rejected(tmp_path, object_sql):
    source = tmp_path / "source.sqlite3"
    seed(source)
    with sqlite3.connect(source) as connection:
        connection.execute(object_sql)
    with pytest.raises(BackupError, match="unexpected_database"):
        create_backup(source, tmp_path / "refused.zip")
    assert not (tmp_path / "refused.zip").exists()


@pytest.mark.parametrize("corruption", ["event", "snapshot", "review", "receipt", "foreign_key"])
def test_protocol_reference_corruption_is_rejected(tmp_path, corruption):
    source = tmp_path / "source.sqlite3"
    seed(source)
    with sqlite3.connect(source) as connection:
        if corruption == "event":
            rowid, text = connection.execute(
                "SELECT rowid,envelope_json FROM training_events LIMIT 1"
            ).fetchone()
            envelope = json.loads(text)
            envelope["session_id"] = str(uuid4())
            connection.execute(
                "UPDATE training_events SET envelope_json=? WHERE rowid=?", (json.dumps(envelope), rowid)
            )
        elif corruption == "snapshot":
            session_id, text = connection.execute(
                "SELECT session_id,snapshot_json FROM training_sessions LIMIT 1"
            ).fetchone()
            snapshot = json.loads(text)
            snapshot["last_event_seq"] += 1
            connection.execute(
                "UPDATE training_sessions SET snapshot_json=? WHERE session_id=?",
                (json.dumps(snapshot), session_id),
            )
        elif corruption == "review":
            connection.execute("UPDATE training_expert_reviews SET report_hash=?", ("0" * 64,))
        elif corruption == "receipt":
            connection.execute(
                "UPDATE training_receipts SET response_json=?", (json.dumps({"session_id": str(uuid4())}),)
            )
        else:
            connection.execute(
                "INSERT INTO training_receipts VALUES(?,?,?,?)", (str(uuid4()), "orphan", "0" * 64, "{}")
            )
    with pytest.raises(BackupError):
        create_backup(source, tmp_path / "invalid.zip")


def test_unknown_schema_and_source_target_alias_are_rejected(tmp_path):
    source = tmp_path / "source.sqlite3"
    seed(source)
    with pytest.raises(BackupError, match="source_target_same"):
        create_backup(source, source)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 99")
    with pytest.raises(BackupError, match="unsupported_database_version"):
        create_backup(source, tmp_path / "unknown.zip")


@pytest.mark.parametrize(
    "attack", ["checksum", "traversal", "extra", "manifest_schema", "manifest_counts", "oversize"]
)
def test_invalid_archive_never_replaces_existing_database(tmp_path, monkeypatch, attack):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    seed(source)
    seed(target, reviews=False)
    original, malicious = tmp_path / "valid.zip", tmp_path / "invalid.zip"
    create_backup(source, original)
    before = target.read_bytes()
    if attack == "checksum":
        rewrite_archive(
            original, malicious, manifest_change=lambda manifest: manifest.update(database_sha256="0" * 64)
        )
    elif attack == "traversal":
        rewrite_archive(original, malicious, extras={"../escape.txt": "must not extract"})
    elif attack == "extra":
        rewrite_archive(original, malicious, extras={".env": "must not extract"})
    elif attack == "manifest_schema":
        rewrite_archive(
            original, malicious, manifest_change=lambda manifest: manifest.update(schema_version=99)
        )
    elif attack == "manifest_counts":
        rewrite_archive(
            original,
            malicious,
            manifest_change=lambda manifest: manifest["table_counts"].update(training_sessions=999),
        )
    else:
        malicious.write_bytes(original.read_bytes())
        monkeypatch.setattr(backup, "MAX_DATABASE_BYTES", 1)
    with pytest.raises(BackupError):
        restore_backup(malicious, target, replace=True)
    assert target.read_bytes() == before
    assert not (tmp_path / "escape.txt").exists()


def test_duplicate_or_symlink_entries_are_not_extracted(tmp_path):
    source, original = tmp_path / "source.sqlite3", tmp_path / "valid.zip"
    seed(source)
    create_backup(source, original)
    with zipfile.ZipFile(original) as bundle:
        manifest, database = bundle.read("manifest.json"), bundle.read("database.sqlite3")
    duplicate = tmp_path / "duplicate.zip"
    with pytest.warns(UserWarning), zipfile.ZipFile(duplicate, "w") as bundle:
        bundle.writestr("manifest.json", manifest)
        bundle.writestr("database.sqlite3", database)
        bundle.writestr("database.sqlite3", database)
    with pytest.raises(BackupError, match="invalid_backup_entries"):
        verify_backup(duplicate)
    symlink = tmp_path / "symlink.zip"
    with zipfile.ZipFile(symlink, "w") as bundle:
        bundle.writestr("manifest.json", manifest)
        info = zipfile.ZipInfo("database.sqlite3")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        bundle.writestr(info, database)
    with pytest.raises(BackupError, match="invalid_backup_entries"):
        verify_backup(symlink)


def test_failed_safety_backup_or_atomic_replace_keeps_old_database(tmp_path, monkeypatch):
    source, target, archive = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3", tmp_path / "valid.zip"
    seed(source)
    seed(target, reviews=False)
    create_backup(source, archive)
    before = target.read_bytes()

    def fail_backup(_database, _archive, **_kwargs):
        raise BackupError("safety_backup_failed")

    original = backup._create_locked
    monkeypatch.setattr(backup, "_create_locked", fail_backup)
    with pytest.raises(BackupError, match="safety_backup_failed"):
        restore_backup(archive, target, replace=True)
    assert target.read_bytes() == before
    monkeypatch.setattr(backup, "_create_locked", original)

    def fail_replace(_source, _target):
        raise PermissionError

    monkeypatch.setattr(backup, "_replace_existing", fail_replace)
    with pytest.raises(BackupError, match="backup_restore_failed"):
        restore_backup(archive, target, replace=True)
    assert target.read_bytes() == before
    assert len(list((tmp_path / "backups").glob("*.zip"))) == 1


@contextmanager
def child_runtime_owner(path):
    code = "from healteam_memory.runtime_lease import RuntimeLease; import sys,os; lease=RuntimeLease(sys.argv[1]); lease.acquire(); print(os.getpid(),flush=True); sys.stdin.readline(); lease.release()"
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    pid = int(process.stdout.readline().strip())
    try:
        yield
    finally:
        try:
            process.communicate("\n", timeout=10)
        except subprocess.TimeoutExpired:
            os.kill(pid, signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
            process.kill()
            process.wait(timeout=10)


def test_independent_running_owner_blocks_backup_and_restore(tmp_path):
    source, target = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3"
    seed(source)
    seed(target)
    incoming = tmp_path / "incoming.zip"
    create_backup(source, incoming)
    before = target.read_bytes()
    with child_runtime_owner(target):
        with pytest.raises(BackupError, match="runtime_in_use"):
            create_backup(target, tmp_path / "blocked.zip")
        with pytest.raises(BackupError, match="runtime_in_use"):
            restore_backup(incoming, target, replace=True)
    assert target.read_bytes() == before


def test_cli_backup_verify_restore_are_safe_and_do_not_load_database_contents_to_stdout(tmp_path):
    source, archive, target = (
        tmp_path / "source.sqlite3",
        tmp_path / "copy.zip",
        tmp_path / "restored.sqlite3",
    )
    seed(source)
    environment = {**os.environ, "PYTHONUTF8": "1"}
    for arguments, status in [
        (["backup", "--database", str(source), "--output", str(archive)], "backed_up"),
        (["verify", str(archive)], "verified"),
        (["restore", str(archive), "--database", str(target)], "restored"),
    ]:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/manage_data.py"), *arguments],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["status"] == status
        assert (
            "请说说你的感受" not in result.stdout
            and "本地复核者" not in result.stdout
            and "sk-" not in result.stdout
        )


@pytest.mark.parametrize("configuration_source", ["default", "dotenv", "environment"])
def test_relocated_cli_ignores_old_root_from_an_external_working_directory(tmp_path, configuration_source):
    current, previous, external = tmp_path / "搬移 副本", tmp_path / "旧 副本", tmp_path / "调用目录"
    external.mkdir()
    for root in (current, previous):
        for marker in ("data/cases", "data/schools", "packages/prompts", "scripts"):
            (root / marker).mkdir(parents=True, exist_ok=True)
    script = current / "scripts/manage_data.py"
    script.write_text((ROOT / "scripts/manage_data.py").read_text(encoding="utf-8"), encoding="utf-8")
    relative_database = (
        "data/healteam.sqlite3" if configuration_source == "default" else "data/records.sqlite3"
    )
    database = current / relative_database
    previous_database = previous / "data/healteam.sqlite3"
    seed(database)
    seed(previous_database, reviews=False, waiting=False)
    expected, previous_records = table_data(database), table_data(previous_database)
    (previous / ".env").write_text(
        f"HEALTEAM_DATABASE_URL={previous_database.as_posix()}\n", encoding="utf-8"
    )
    environment = {**os.environ, "HEALTEAM_ROOT": str(previous), "PYTHONUTF8": "1"}
    environment.pop("HEALTEAM_DATABASE_URL", None)
    if configuration_source == "dotenv":
        (current / ".env").write_text(f"HEALTEAM_DATABASE_URL={relative_database}\n", encoding="utf-8")
    elif configuration_source == "environment":
        environment["HEALTEAM_DATABASE_URL"] = f"sqlite+aiosqlite:///{relative_database}"

    def run(*arguments):
        result = subprocess.run(
            [sys.executable, str(script), *arguments],
            cwd=external,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    result = run("backup")
    archive = Path(result["archive"])
    assert archive.parent == current / "backups"
    assert run("verify", str(archive))["status"] == "verified"
    database.rename(database.with_suffix(".saved"))
    assert run("restore", str(archive))["database"] == str(database)
    assert table_data(database) == expected
    assert table_data(previous_database) == previous_records
    assert not (previous / "backups").exists()
    assert not (external / "data").exists()


def test_archive_does_not_contain_private_environment_or_paths(tmp_path):
    source, archive = tmp_path / "source.sqlite3", tmp_path / "copy.zip"
    seed(source)
    (tmp_path / ".env").write_text("API_KEY=private-do-not-copy", encoding="utf-8")
    create_backup(source, archive)
    with zipfile.ZipFile(archive) as bundle:
        manifest = bundle.read("manifest.json")
        assert b"private-do-not-copy" not in manifest and str(source).encode() not in manifest
        assert set(bundle.namelist()) == {"manifest.json", "database.sqlite3"}
    with RuntimeLease(source):
        assert validate_database(source).schema_version == 2


@pytest.mark.parametrize("action", ["backup", "verify", "restore"])
def test_human_cli_shows_chinese_summary_without_sensitive_records(tmp_path, action):
    source, archive = tmp_path / "source.sqlite3", tmp_path / "valid.zip"
    seed(source)
    if action != "backup":
        create_backup(source, archive)
    arguments = {
        "backup": ["backup", "--database", str(source), "--output", str(archive)],
        "verify": ["verify", str(archive)],
        "restore": ["restore", str(archive), "--database", str(tmp_path / "restored.sqlite3")],
    }[action]
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts/manage_data.py"), *arguments, "--human"],
        cwd=ROOT,
        env={**os.environ, "PYTHONUTF8": "1"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "数据库版本：2" in result.stdout and "人工复核：1" in result.stdout
    assert not result.stdout.startswith("{")
    assert all(value not in result.stdout for value in ("本地复核者", "请说说你的感受", "sk-"))


def test_forged_checksum_cannot_authorize_an_unexpected_trigger(tmp_path):
    source, archive = tmp_path / "source.sqlite3", tmp_path / "valid.zip"
    seed(source)
    create_backup(source, archive)
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TRIGGER forbidden AFTER INSERT ON training_sessions BEGIN SELECT 1; END")
    data = source.read_bytes()
    forged = tmp_path / "forged.zip"
    rewrite_archive(
        archive,
        forged,
        database_change=lambda _old: data,
        manifest_change=lambda manifest: manifest.update(
            database_size=len(data), database_sha256=sha256(data).hexdigest()
        ),
    )
    with pytest.raises(BackupError, match="unexpected_database_objects"):
        verify_backup(forged)


def test_high_expansion_database_entry_is_rejected_before_decompression(tmp_path):
    source, archive = tmp_path / "source.sqlite3", tmp_path / "valid.zip"
    seed(source)
    create_backup(source, archive)
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    data = b"\0" * (8 * 1024 * 1024)
    manifest.update(database_size=len(data), database_sha256=sha256(data).hexdigest())
    bomb = tmp_path / "expansion.zip"
    with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("database.sqlite3", data)
    with pytest.raises(BackupError, match="backup_too_large"):
        verify_backup(bomb)


def test_default_restore_cannot_overwrite_a_target_created_during_validation(tmp_path, monkeypatch):
    source, archive, target = tmp_path / "source.sqlite3", tmp_path / "valid.zip", tmp_path / "new.sqlite3"
    seed(source)
    create_backup(source, archive)
    original = backup._extract_verified

    def raced_extract(archive, destination):
        manifest = original(archive, destination)
        if destination.parent.name.startswith(".healteam-restore-"):
            target.write_bytes(b"new external file must survive")
        return manifest

    monkeypatch.setattr(backup, "_extract_verified", raced_extract)
    with pytest.raises(BackupError, match="restore_target_exists"):
        restore_backup(archive, target)
    assert target.read_bytes() == b"new external file must survive"


def test_backup_archive_publication_never_overwrites_a_raced_output(tmp_path, monkeypatch):
    source, archive = tmp_path / "source.sqlite3", tmp_path / "output.zip"
    seed(source)
    operation = "rename" if os.name == "nt" else "link"
    original = getattr(backup.os, operation)

    def publish(candidate, destination):
        if Path(destination) == archive:
            archive.write_bytes(b"another user's new archive")
        return original(candidate, destination)

    monkeypatch.setattr(backup.os, operation, publish)
    with pytest.raises(BackupError):
        create_backup(source, archive)
    assert archive.read_bytes() == b"another user's new archive"


def test_replace_holds_write_protection_after_safety_snapshot(tmp_path, monkeypatch):
    source, target, archive = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3", tmp_path / "valid.zip"
    seed(source)
    seed(target, reviews=False)
    create_backup(source, archive)
    original = backup._create_locked
    blocked = []

    def snapshot_then_attempt_write(database, archive, **kwargs):
        manifest = original(database, archive, **kwargs)
        if archive.parent.name == "backups":
            connection = None
            try:
                connection = sqlite3.connect(target, timeout=0)
                connection.execute("UPDATE training_sessions SET error_code='late-race'")
                connection.commit()
            except sqlite3.Error:
                blocked.append(True)
            finally:
                if connection is not None:
                    connection.close()
        return manifest

    monkeypatch.setattr(backup, "_create_locked", snapshot_then_attempt_write)
    restore_backup(archive, target, replace=True)
    assert blocked == [True]
    assert table_data(target) == table_data(source)


def test_changed_target_inode_after_safety_snapshot_is_preserved(tmp_path, monkeypatch):
    source, target, external = (
        tmp_path / "source.sqlite3",
        tmp_path / "target.sqlite3",
        tmp_path / "external.sqlite3",
    )
    archive = tmp_path / "valid.zip"
    seed(source)
    seed(target, reviews=False)
    seed(external, waiting=False)
    external_data = table_data(external)
    create_backup(source, archive)
    original = backup._create_locked

    def snapshot_then_swap(database, archive, **kwargs):
        result = original(database, archive, **kwargs)
        if archive.parent.name == "backups":
            backup._replace_existing(external, target)
        return result

    monkeypatch.setattr(backup, "_create_locked", snapshot_then_swap)
    with pytest.raises(BackupError, match="restore_target_changed"):
        restore_backup(archive, target, replace=True)
    assert table_data(target) == external_data


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_restore_to_new_path_rejects_stale_sqlite_sidecars(tmp_path, suffix):
    source, archive, target = tmp_path / "source.sqlite3", tmp_path / "valid.zip", tmp_path / "new.sqlite3"
    seed(source)
    create_backup(source, archive)
    sidecar = Path(str(target) + suffix)
    sidecar.write_bytes(b"unrelated sidecar")
    with pytest.raises(BackupError, match="database_sidecars_present"):
        restore_backup(archive, target)
    assert not target.exists() and sidecar.read_bytes() == b"unrelated sidecar"


def test_wal_commits_are_included_in_backup_and_restore_needs_no_sidecars(tmp_path):
    source, archive, target = tmp_path / "wal.sqlite3", tmp_path / "valid.zip", tmp_path / "new.sqlite3"
    seed(source)
    with closing(sqlite3.connect(source)) as writer:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("UPDATE training_sessions SET error_code='wal-committed'")
        writer.commit()
        assert Path(str(source) + "-wal").exists()
        create_backup(source, archive)
        restore_backup(archive, target)
        assert table_data(target) == table_data(source)
    with closing(sqlite3.connect(target)) as restored:
        assert restored.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert not any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal"))


def test_invalid_database_url_returns_a_safe_error_code():
    with pytest.raises(BackupError, match="invalid_database_path"):
        backup.database_path("not a valid :// URL")


def test_existing_sqlite_writer_handle_blocks_replacement_without_data_loss(tmp_path):
    source, target, archive = tmp_path / "source.sqlite3", tmp_path / "target.sqlite3", tmp_path / "valid.zip"
    seed(source)
    seed(target, reviews=False)
    create_backup(source, archive)
    before = table_data(target)
    with closing(sqlite3.connect(target, timeout=0)) as external:
        external.execute("BEGIN IMMEDIATE")
        with pytest.raises(BackupError):
            restore_backup(archive, target, replace=True)
        external.rollback()
    assert table_data(target) == before


def test_atomic_replacement_supports_spaces_chinese_and_surrogate_pair_paths(tmp_path):
    folder = tmp_path / "训练 备份📚"
    folder.mkdir()
    source, target, archive = folder / "源库.sqlite3", folder / "目标📂.sqlite3", folder / "完整备份.zip"
    seed(source)
    seed(target, reviews=False)
    create_backup(source, archive)
    result = restore_backup(archive, target, replace=True)
    assert result["status"] == "restored" and table_data(target) == table_data(source)
