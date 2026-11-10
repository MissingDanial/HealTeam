"""Exclusive local runtime ownership, released by the OS after a process exits.

This deliberately uses a held file lock rather than an expiring timestamp:
sleep, a paused event loop, or slow generation must not let another runtime
recover the live owner's sessions. The sidecar is never unlinked, since that
would allow two processes to lock different inodes for the same database.
"""

from __future__ import annotations

import errno
import os
import threading
import weakref
from pathlib import Path
from typing import BinaryIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl

_GUARD = threading.Lock()
_OWNERS: weakref.WeakValueDictionary[tuple[int, str], RuntimeLease] = weakref.WeakValueDictionary()


def _after_fork() -> None:
    # A lock held by another thread at fork cannot be released in the child.
    global _GUARD, _OWNERS
    _GUARD = threading.Lock()
    _OWNERS = weakref.WeakValueDictionary()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


class RuntimeInUseError(RuntimeError):
    """A live runtime already owns the local database; no recovery is safe."""

    code = "runtime_in_use"
    status_code = 503

    def __init__(self):
        super().__init__("Another local runtime already owns this database")


def _lock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class RuntimeLease:
    """Keep one runtime per canonical database path, across local processes.

    Construct with the repository's resolved file path. Acquire before schema
    initialization or interrupted-session recovery, and release only after the
    manager and repository have shut down. No thread or heartbeat is required.
    Repeated acquire/release on this object is idempotent. Separate objects in
    the same process compete just like separate processes.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()
        self.path = self.database_path.with_name(f"{self.database_path.name}.lock")
        self._key = os.path.normcase(str(self.path))
        self._handle: BinaryIO | None = None
        self._pid: int | None = None

    @property
    def acquired(self) -> bool:
        return self._pid == os.getpid() and self._handle is not None and not self._handle.closed

    def acquire(self) -> None:
        with _GUARD:
            if self.acquired:
                return
            # A forked child may inherit this object and its file descriptor.
            # Close its copy without unlocking the parent's shared lock.
            if self._handle is not None:
                self._handle.close()
                self._handle = None
                self._pid = None
            process_key = (os.getpid(), self._key)
            owner = _OWNERS.get(process_key)
            if owner is not None and owner.acquired:
                raise RuntimeInUseError()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.path.open("a+b")
            try:
                # Windows locks a byte range. Keep one real byte on disk, but
                # never use its content or modification time as ownership.
                if os.fstat(handle.fileno()).st_size == 0:
                    handle.write(b"\0")
                    handle.flush()
                _lock(handle)
            except OSError as exc:
                handle.close()
                if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}:
                    raise RuntimeInUseError() from None
                raise
            except BaseException:
                handle.close()
                raise
            self._handle = handle
            self._pid = os.getpid()
            _OWNERS[process_key] = self

    def release(self) -> None:
        with _GUARD:
            handle = self._handle
            if handle is None:
                return
            process_key = (os.getpid(), self._key)
            if _OWNERS.get(process_key) is self:
                _OWNERS.pop(process_key, None)
            try:
                if self._pid == os.getpid() and not handle.closed:
                    _unlock(handle)
            finally:
                handle.close()
                self._handle = None
                self._pid = None

    def __enter__(self) -> RuntimeLease:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


__all__ = ["RuntimeLease", "RuntimeInUseError"]
