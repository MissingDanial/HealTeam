"""Bundle source plus verified production web files; never bundle local data."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path

try:
    from . import package_source
    from .prepare_local import ROOT, PreparationError, is_link, verified_runtime
except ImportError:  # Direct script invocation.
    import package_source
    from prepare_local import ROOT, PreparationError, is_link, verified_runtime


def package(root: Path, output: Path) -> dict:
    root = root.resolve()
    source = root.parent
    if is_link(output):
        raise PreparationError("linked_output_file")
    output = output.resolve()
    if output.is_relative_to(source):
        raise PreparationError("archive_output_must_be_outside_source")
    manifest, runtime = verified_runtime(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".healteam-package-", suffix=".zip", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary)
    source_count = 0
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for current, directories, files in os.walk(source, followlinks=False):
                directories[:] = sorted(
                    name
                    for name in directories
                    if name not in package_source.PRUNE | {".local", "backups", "releases"}
                    and not name.endswith(".egg-info")
                )
                if any(is_link(Path(current) / name) for name in directories):
                    raise PreparationError("linked_source_directory")
                for name in sorted(files):
                    path = Path(current) / name
                    relative = path.relative_to(source)
                    if is_link(path) or not path.resolve().is_relative_to(source):
                        raise PreparationError("linked_source_file")
                    if package_source.allowed(package_source.SOURCE / relative):
                        bundle.write(path, Path(source.name, relative).as_posix())
                        source_count += 1
            for name, path in sorted(runtime.items()):
                bundle.write(path, f"{source.name}/{root.name}/.local/runtime/{name}")
        with zipfile.ZipFile(temporary) as bundle:
            if bundle.testzip() is not None:
                raise PreparationError("local_archive_corrupt")
            names = bundle.namelist()
            if len(names) != len(set(names)):
                raise PreparationError("duplicate_archive_entry")
            required = {f"{source.name}/{name}" for name in package_source.REQUIRED}
            if not required <= set(names):
                raise PreparationError("missing_local_source_resources")
            if f"{source.name}/{root.name}/.local/runtime/web/server.js" not in names:
                raise PreparationError("missing_local_web_runtime")
            prefix = f"{source.name}/{root.name}/.local/runtime/"
            if json.loads(bundle.read(prefix + "manifest.json")) != manifest:
                raise PreparationError("runtime_changed_during_packaging")
            if any(
                hashlib.sha256(bundle.read(prefix + name)).hexdigest() != digest
                for name, digest in manifest["files"].items()
            ):
                raise PreparationError("runtime_changed_during_packaging")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(f"{digest}  {output.name}\n", encoding="ascii")
    return {
        "archive": str(output),
        "version": manifest["version"],
        "platform": manifest["platform"],
        "arch": manifest["arch"],
        "source_files": source_count,
        "runtime_files": len(runtime),
        "bytes": output.stat().st_size,
        "sha256": digest,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        manifest, _ = verified_runtime(args.root.resolve())
        output = (
            args.output
            or args.root.resolve().parent.parent
            / "output"
            / f"HealTeam-local-{manifest['version']}-{manifest['platform']}-{manifest['arch']}.zip"
        )
        result = package(args.root, output)
    except (PreparationError, OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
        code = str(exc) if isinstance(exc, PreparationError) else "local_packaging_failed"
        raise SystemExit(f"无法生成本地交付包：{code}") from None
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
