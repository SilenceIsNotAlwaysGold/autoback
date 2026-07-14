"""跨进程单实例锁与 PID 探测。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import BinaryIO


def pid_is_running(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return ctypes.windll.kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _try_lock(file_obj: BinaryIO) -> bool:
    file_obj.seek(0)
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(file_obj.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    import fcntl

    try:
        fcntl.flock(file_obj.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(file_obj: BinaryIO) -> None:
    file_obj.seek(0)
    if sys.platform == "win32":
        import msvcrt

        msvcrt.locking(file_obj.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(file_obj.fileno(), fcntl.LOCK_UN)


def lock_is_held(lock_path: Path) -> bool:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a+b", buffering=0) as f:
        if lock_path.stat().st_size == 0:
            f.write(b"\0")
            f.flush()
        if not _try_lock(f):
            return True
        _unlock(f)
        return False


def read_live_pid(pid_path: Path, lock_path: Path) -> int | None:
    try:
        pid = int(pid_path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    if pid_is_running(pid) and lock_is_held(lock_path):
        return pid
    if not lock_is_held(lock_path):
        try:
            pid_path.unlink()
        except OSError:
            pass
    return None


class ProcessLock:
    def __init__(self, lock_path: Path, pid_path: Path):
        self.lock_path = lock_path
        self.pid_path = pid_path
        self._file: BinaryIO | None = None
        self.pid = os.getpid()

    def acquire(self) -> bool:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(self.lock_path, "a+b", buffering=0)
        if self.lock_path.stat().st_size == 0:
            f.write(b"\0")
            f.flush()
        if not _try_lock(f):
            f.close()
            return False
        self._file = f
        self.pid_path.write_text(str(self.pid), encoding="ascii")
        return True

    def release(self) -> None:
        if not self._file:
            return
        try:
            try:
                owner = int(self.pid_path.read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                owner = None
            if owner == self.pid:
                try:
                    self.pid_path.unlink()
                except OSError:
                    pass
            _unlock(self._file)
        finally:
            self._file.close()
            self._file = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("主引擎已经在运行")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.release()
