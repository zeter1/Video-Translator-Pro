"""Process-wide lock for one queue-owning application instance.

The translation queue is durable but intentionally single-consumer. Two GUI
processes using the same runtime directory would otherwise be able to dispatch
the same queued task concurrently and race while replacing translation_tasks.json.
"""
from __future__ import annotations

from pathlib import Path
import os

from videotranslator.core.paths import get_translation_checkpoints_dir


class ApplicationInstanceAlreadyRunning(RuntimeError):
    """Raised when another process owns the queue/runtime instance lock."""


def get_application_instance_lock_path() -> Path:
    return get_translation_checkpoints_dir() / ".application.lock"


class ApplicationInstanceLock:
    """OS-backed non-blocking lock held for the lifetime of the GUI process."""

    def __init__(self, path: str | os.PathLike[str] | None = None):
        self.path = Path(path) if path is not None else get_application_instance_lock_path()
        self._handle = None
        self._locked = False

    @property
    def acquired(self) -> bool:
        return bool(self._locked and self._handle is not None)

    def acquire(self) -> "ApplicationInstanceLock":
        if self.acquired:
            return self

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)

            if os.name == "nt":
                import msvcrt

                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise ApplicationInstanceAlreadyRunning(
                        "Другая копия программы уже использует эту очередь переводов."
                    ) from exc
            else:
                import fcntl

                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise ApplicationInstanceAlreadyRunning(
                        "Другая копия программы уже использует эту очередь переводов."
                    ) from exc
        except Exception:
            handle.close()
            raise

        self._handle = handle
        self._locked = True
        return self

    def release(self) -> None:
        handle = self._handle
        locked = self._locked
        self._handle = None
        self._locked = False
        if handle is None:
            return
        try:
            if locked:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "ApplicationInstanceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
