"""Verified local frontend artifacts and owned Windows child-process handles."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
import subprocess
import tomllib
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit


class LocalRuntimeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def load_manifest(root: Path, *, api_port: int | None = None) -> dict:
    runtime = root / ".local" / "runtime"
    path = runtime / "manifest.json"
    try:
        for directory in (root / ".local", runtime):
            if _linked(directory):
                raise ValueError
        if (
            not runtime.resolve().is_relative_to(root.resolve())
            or _linked(path)
            or path.stat().st_size > 4_000_000
        ):
            raise ValueError
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            data.get("format") != "healteam-local-runtime-v1"
            or data.get("entry") != "web/server.js"
            or not isinstance(data.get("files"), dict)
            or not 1 <= len(data["files"]) <= 50000
        ):
            raise ValueError
        version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
        if data.get("version") != version:
            raise LocalRuntimeError("runtime_version_requires_rebuild")
        endpoint = urlsplit(data["api_internal_url"])
        if (
            endpoint.scheme != "http"
            or endpoint.hostname != "127.0.0.1"
            or endpoint.path not in {"", "/"}
            or endpoint.query
            or endpoint.fragment
            or endpoint.username
            or endpoint.password
            or endpoint.port is None
            or not 1 <= endpoint.port <= 65535
        ):
            raise ValueError
        if api_port is not None and api_port != endpoint.port:
            raise LocalRuntimeError("api_port_build_mismatch")
        if data.get("platform") != "win32" or data.get("arch") != "x64":
            raise LocalRuntimeError("runtime_platform_mismatch")
        if type(data.get("node_major")) is not int or data["node_major"] not in {22, 24}:
            raise ValueError
        if "web/server.js" not in data["files"] or not any(
            name.startswith("web/.next/static/") for name in data["files"]
        ):
            raise ValueError
        resolved = runtime.resolve()
        for name, digest in data["files"].items():
            relative = PurePosixPath(name)
            if (
                not isinstance(name, str)
                or "\\" in name
                or ":" in name
                or relative.is_absolute()
                or not relative.parts
                or relative.parts[0] != "web"
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError
            if (
                relative.name.lower().startswith(".env") and relative.name.lower() != ".env.example"
            ) or relative.name.lower().endswith((".sqlite3", ".sqlite", ".db", ".pem", ".key")):
                raise LocalRuntimeError("private_runtime_file")
            target = runtime.joinpath(*relative.parts)
            current = runtime
            for part in relative.parts:
                current = current / part
                if _linked(current):
                    raise ValueError
            if not target.resolve().is_relative_to(resolved) or not target.is_file():
                raise ValueError
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise LocalRuntimeError("runtime_hash_mismatch")
        actual = set()
        for current, directories, files in os.walk(runtime, followlinks=False):
            for name in directories:
                if _linked(Path(current) / name):
                    raise ValueError
            for name in files:
                target = Path(current) / name
                if _linked(target):
                    raise ValueError
                relative_name = target.relative_to(runtime).as_posix()
                if relative_name != "manifest.json":
                    actual.add(relative_name)
        if actual != set(data["files"]):
            raise LocalRuntimeError("runtime_inventory_mismatch")
        required = json.loads((runtime / "web/.next/required-server-files.json").read_text(encoding="utf-8"))
        if required.get("config", {}).get("experimental", {}).get("isrFlushToDisk") is not False:
            raise LocalRuntimeError("runtime_cache_requires_rebuild")
        data["api_port"] = endpoint.port
        return data
    except LocalRuntimeError:
        raise
    except (OSError, UnicodeError, ValueError, KeyError, TypeError, AttributeError):
        raise LocalRuntimeError("runtime_not_prepared") from None


def _linked(path: Path) -> bool:
    return path.is_symlink() or bool(
        getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def node_environment(web_port: int) -> dict[str, str]:
    """The web child receives no provider, credential, proxy or Python settings."""
    allowed = (
        "PATH",
        "SystemRoot",
        "WINDIR",
        "TEMP",
        "TMP",
        "COMSPEC",
        "PATHEXT",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    )
    result = {name: os.environ[name] for name in allowed if name in os.environ}
    result.update(
        HOSTNAME="127.0.0.1", PORT=str(web_port), NODE_ENV="production", NEXT_TELEMETRY_DISABLED="1"
    )
    return result


class WindowsChild:
    """Create Node already inside a kill-on-close Job, with an exact handle list.

    Job assignment is atomic at process creation (Windows 10+); there is no
    spawned-but-unowned window. Only this object holds the non-inherited Job.
    See Microsoft's PROC_THREAD_ATTRIBUTE_JOB_LIST and Job Objects references.
    """

    def __init__(self, command: list[str], *, cwd: Path, environment: dict[str, str], log_path: Path):
        if os.name != "nt":
            raise LocalRuntimeError("windows_launcher_required")
        import msvcrt
        from ctypes import wintypes as w

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        size_t = ctypes.c_size_t

        class BasicLimits(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_longlong),
                ("job_time", ctypes.c_longlong),
                ("flags", w.DWORD),
                ("min_ws", size_t),
                ("max_ws", size_t),
                ("active", w.DWORD),
                ("affinity", size_t),
                ("priority", w.DWORD),
                ("scheduling", w.DWORD),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                (name, ctypes.c_ulonglong)
                for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")
            ]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [
                ("basic", BasicLimits),
                ("io", IoCounters),
                ("process_memory", size_t),
                ("job_memory", size_t),
                ("peak_process", size_t),
                ("peak_job", size_t),
            ]

        class StartupInfo(ctypes.Structure):
            _fields_ = [
                ("cb", w.DWORD),
                ("reserved", w.LPWSTR),
                ("desktop", w.LPWSTR),
                ("title", w.LPWSTR),
                *[
                    (name, w.DWORD)
                    for name in ("x", "y", "xsize", "ysize", "xcount", "ycount", "fill", "flags")
                ],
                ("show", w.WORD),
                ("reserved_size", w.WORD),
                ("reserved_bytes", ctypes.c_void_p),
                ("stdin", w.HANDLE),
                ("stdout", w.HANDLE),
                ("stderr", w.HANDLE),
            ]

        class StartupEx(ctypes.Structure):
            _fields_ = [("startup", StartupInfo), ("attributes", ctypes.c_void_p)]

        class ProcessInfo(ctypes.Structure):
            _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]

        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        kernel.CreateJobObjectW.restype = w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        kernel.SetInformationJobObject.restype = w.BOOL
        kernel.InitializeProcThreadAttributeList.argtypes = [
            ctypes.c_void_p,
            w.DWORD,
            w.DWORD,
            ctypes.POINTER(size_t),
        ]
        kernel.InitializeProcThreadAttributeList.restype = w.BOOL
        kernel.UpdateProcThreadAttribute.argtypes = [
            ctypes.c_void_p,
            w.DWORD,
            size_t,
            ctypes.c_void_p,
            size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
        ]
        kernel.UpdateProcThreadAttribute.restype = w.BOOL
        kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        kernel.CreateProcessW.argtypes = [
            w.LPCWSTR,
            w.LPWSTR,
            ctypes.c_void_p,
            ctypes.c_void_p,
            w.BOOL,
            w.DWORD,
            ctypes.c_void_p,
            w.LPCWSTR,
            ctypes.c_void_p,
            ctypes.POINTER(ProcessInfo),
        ]
        kernel.CreateProcessW.restype = w.BOOL
        kernel.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        kernel.WaitForSingleObject.restype = w.DWORD
        kernel.GetExitCodeProcess.argtypes = [w.HANDLE, ctypes.POINTER(w.DWORD)]
        kernel.GetExitCodeProcess.restype = w.BOOL
        kernel.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
        kernel.TerminateJobObject.restype = w.BOOL
        self._kernel = kernel
        self._job = kernel.CreateJobObjectW(None, None)
        self._process = None
        self.pid = 0
        if not self._job:
            raise LocalRuntimeError("process_ownership_unavailable")
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(self._job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            self.close()
            raise LocalRuntimeError("process_ownership_unavailable")
        attributes = None
        attributes_initialized = False
        log_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with log_path.open("ab", buffering=0) as log, open(os.devnull, "rb") as null:
                log_handle, null_handle = (
                    msvcrt.get_osfhandle(log.fileno()),
                    msvcrt.get_osfhandle(null.fileno()),
                )
                inherited = (w.HANDLE * 2)(log_handle, null_handle)
                jobs = (w.HANDLE * 1)(self._job)
                os.set_handle_inheritable(log_handle, True)
                os.set_handle_inheritable(null_handle, True)
                try:
                    size = size_t()
                    kernel.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(size))
                    attributes = ctypes.create_string_buffer(size.value)
                    if not kernel.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(size)):
                        raise LocalRuntimeError("process_ownership_unavailable")
                    attributes_initialized = True
                    if not kernel.UpdateProcThreadAttribute(
                        attributes, 0, 0x2000D, jobs, ctypes.sizeof(jobs), None, None
                    ):
                        raise LocalRuntimeError("process_ownership_unavailable")
                    if not kernel.UpdateProcThreadAttribute(
                        attributes, 0, 0x20002, inherited, ctypes.sizeof(inherited), None, None
                    ):
                        raise LocalRuntimeError("process_ownership_unavailable")
                    startup = StartupEx()
                    startup.startup.cb = ctypes.sizeof(startup)
                    startup.startup.flags = 0x100  # STARTF_USESTDHANDLES
                    startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = (
                        null_handle,
                        log_handle,
                        log_handle,
                    )
                    startup.attributes = ctypes.cast(attributes, ctypes.c_void_p)
                    process = ProcessInfo()
                    line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
                    block = ctypes.create_unicode_buffer(
                        "\0".join(
                            f"{key}={value}"
                            for key, value in sorted(environment.items(), key=lambda item: item[0].upper())
                        )
                        + "\0\0"
                    )
                    flags = 0x08000000 | 0x400 | 0x80000
                    if not kernel.CreateProcessW(
                        command[0],
                        line,
                        None,
                        None,
                        True,
                        flags,
                        block,
                        str(cwd),
                        ctypes.byref(startup),
                        ctypes.byref(process),
                    ):
                        raise LocalRuntimeError("web_process_start_failed")
                    self._process, self.pid = process.process, process.pid
                    kernel.CloseHandle(process.thread)
                finally:
                    os.set_handle_inheritable(log_handle, False)
                    os.set_handle_inheritable(null_handle, False)
        except BaseException:
            self.close()
            raise
        finally:
            if attributes_initialized:
                kernel.DeleteProcThreadAttributeList(attributes)

    def poll(self) -> int | None:
        if self._process is None:
            return None
        if self._kernel.WaitForSingleObject(self._process, 0) == 258:
            return None
        result = ctypes.c_ulong()
        if not self._kernel.GetExitCodeProcess(self._process, ctypes.byref(result)):
            raise LocalRuntimeError("web_process_status_failed")
        return result.value

    def close(self) -> None:
        if getattr(self, "_job", None):
            self._kernel.CloseHandle(self._job)
            self._job = None
        if getattr(self, "_process", None):
            self._kernel.WaitForSingleObject(self._process, 5000)
            self._kernel.CloseHandle(self._process)
            self._process = None
