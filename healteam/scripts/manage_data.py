"""Offline training backup, verification and restore; no model calls."""

from __future__ import annotations

import argparse
import json
import os
from contextlib import chdir
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from dotenv import dotenv_values
from healteam_memory.backup import BackupError, create_backup, database_path, restore_backup, verify_backup
from healteam_memory.resources import find_project_root

MESSAGES = {
    "runtime_in_use": "请先停止使用该数据库的 HealTeam 服务。",
    "database_in_use": "数据库仍被使用，请关闭相关程序后重试。",
    "restore_target_exists": "目标数据库已存在。恢复到新文件，或显式使用 --replace；覆盖前会自动保存安全备份。",
    "backup_already_exists": "备份文件已存在，请选择新的输出文件名。",
    "database_not_found": "找不到数据库文件。尚未训练时不需要备份。",
    "backup_not_found": "找不到备份文件。",
    "unsupported_database_version": "数据库版本不受当前工具支持，未作修改。",
    "source_target_same": "源文件与目标文件必须不同。",
    "restore_target_changed": "目标文件在恢复期间发生变化，已停止替换。请关闭相关程序后重试。",
    "database_sidecars_present": "目标旁仍有数据库日志文件，请关闭相关程序，或恢复到一个新文件。",
    "restore_target_uses_wal": "现有目标采用WAL模式，请恢复到一个新的数据库文件，原库保持不变。",
    "restore_atomic_replace_unavailable": "当前系统或文件系统不支持安全覆盖，请恢复到一个新的数据库文件。",
    "backup_too_large": "备份超过当前512 MiB或压缩比例限制，未作恢复。",
    "database_too_large": "数据库超过当前512 MiB备份上限，未作修改。",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    backup = subcommands.add_parser("backup", help="服务停止后备份完整训练记录")
    backup.add_argument("--database", help="SQLite文件路径或URL，默认读取本机配置")
    backup.add_argument("--output", type=Path, help="输出ZIP，默认项目backups目录")
    backup.add_argument("--human", action="store_true", help="显示简明中文结果")
    verify = subcommands.add_parser("verify", help="只验证备份，不修改数据库")
    verify.add_argument("archive", type=Path)
    verify.add_argument("--human", action="store_true", help="显示简明中文结果")
    restore = subcommands.add_parser("restore", help="验证后恢复到新数据库；不自动迁移")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--database", help="目标SQLite文件路径或URL，默认读取本机配置")
    restore.add_argument("--replace", action="store_true", help="显式替换现有库，先自动保存可验证安全备份")
    restore.add_argument("--human", action="store_true", help="显示简明中文结果")
    args = parser.parse_args()
    # Maintenance belongs to this copy, even when another copy's root was inherited.
    root = find_project_root(root=Path(__file__).resolve().parents[1])
    configuration = dotenv_values(root / ".env")
    database = (
        getattr(args, "database", None)
        or os.getenv("HEALTEAM_DATABASE_URL")
        or configuration.get("HEALTEAM_DATABASE_URL")
        or str(root / "data" / "healteam.sqlite3")
    )
    try:
        if args.command != "verify" and not args.database:
            # Configuration paths have the same project-relative meaning as startup;
            # explicit CLI paths still resolve from the caller's working directory.
            with chdir(root):
                database = database_path(database)
        if args.command == "backup":
            output = (
                args.output
                or root
                / "backups"
                / f"healteam-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}.healteam-backup.zip"
            )
            result = {
                "status": "backed_up",
                "archive": str(output.resolve()),
                **create_backup(database, output),
            }
        elif args.command == "verify":
            result = {"status": "verified", **verify_backup(args.archive)}
        else:
            result = restore_backup(args.archive, database, replace=args.replace)
    except BackupError as exc:
        message = MESSAGES.get(exc.code, "备份或恢复未完成，现有数据库未被删除。请检查文件与可用空间。")
        if args.human:
            print(f"未完成：{message}（{exc.code}）")
        else:
            print(json.dumps({"status": "failed", "code": exc.code, "message": message}, ensure_ascii=False))
        return 1
    if args.human:
        label = {"backup": "备份已完成", "verify": "备份验证通过", "restore": "恢复已完成"}[args.command]
        print(label)
        location = result.get("archive") or result.get("database") or str(args.archive.resolve())
        print(f"文件：{location}")
        print(f"数据库版本：{result['schema_version']}")
        counts = result["table_counts"]
        print(
            f"会话：{counts['training_sessions']}，事件：{counts['training_events']}，输入收据：{counts['training_receipts']}，人工复核：{counts.get('training_expert_reviews', 0)}"
        )
        if result.get("created_at"):
            print(
                f"备份时间：{datetime.fromisoformat(result['created_at']).astimezone().isoformat(timespec='seconds')}"
            )
        if result.get("safety_backup"):
            print(f"替换前安全备份：{result['safety_backup']}")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
