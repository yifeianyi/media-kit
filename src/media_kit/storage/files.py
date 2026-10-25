"""Private staging, local serialization, and durable atomic filesystem moves."""

from __future__ import annotations

import fcntl
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import BinaryIO

from media_kit.errors import InputValidationError, StorageError


@contextmanager
def store_lock(state_dir: Path) -> Iterator[None]:
    """Serialize recovery and mutations across threads and local processes."""
    with (state_dir / ".lock").open("r+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_move(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source, destination)
    fsync_directory(destination.parent)
    if source.parent.exists() and source.parent != destination.parent:
        fsync_directory(source.parent)


def copy_input(source: str | Path | BinaryIO, target: Path, max_bytes: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with (
        Path(source).open("rb") if isinstance(source, (str, Path)) else nullcontext(source)
    ) as stream:
        try:
            total = 0
            with target.open("xb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not isinstance(chunk, bytes):
                        raise InputValidationError("source must be a binary stream")
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise InputValidationError("image exceeds byte limit")
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            fsync_directory(target.parent)
        except BaseException:
            target.unlink(missing_ok=True)
            raise


def remove_tree(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("refusing to remove a symlink as a managed tree")
    if path.exists():
        shutil.rmtree(path)
