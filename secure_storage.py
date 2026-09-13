"""Descriptor-relative, symlink-resistant storage for clipboard state/cache."""
from __future__ import annotations

import errno
import os
import secrets
import stat
import time
from contextlib import contextmanager
from pathlib import Path

PRIVATE_MODE = 0o700
FILE_MODE = 0o600

class StorageError(RuntimeError):
    pass


def _check_dir(fd: int, label: str, *, private: bool) -> None:
    value = os.fstat(fd)
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.getuid():
        raise StorageError(f"{label} must be a directory owned by the current user")
    if private and value.st_mode & 0o077:
        try:
            os.fchmod(fd, PRIVATE_MODE)
        except OSError as error:
            raise StorageError(f"{label} must be private") from error
        if os.fstat(fd).st_mode & 0o077:
            raise StorageError(f"{label} must be private")


def _check_path_ancestor(fd: int, label: str, *, configured_root: bool) -> None:
    value = os.fstat(fd)
    if not stat.S_ISDIR(value.st_mode):
        raise StorageError(f"{label} must be a directory")
    if value.st_uid not in (0, os.getuid()):
        raise StorageError(f"{label} must be owned by root or the current user")

    writable = value.st_mode & 0o022
    sticky_shared = bool(value.st_mode & stat.S_ISVTX) and not configured_root
    if configured_root and value.st_uid == os.getuid() and writable:
        try:
            os.fchmod(fd, stat.S_IMODE(value.st_mode) & ~0o022)
        except OSError as error:
            raise StorageError("configured storage root cannot be hardened") from error
        value = os.fstat(fd)
        writable = value.st_mode & 0o022
    if writable and not sticky_shared:
        raise StorageError(f"{label} must not be group/world-writable without the sticky bit")
    if configured_root and value.st_uid != os.getuid():
        raise StorageError("configured storage root must be owned by the current user")
    if configured_root and writable:
        raise StorageError("configured storage root must not be group/world-writable")


def _open_configured_root(path: str) -> int:
    components = [part for part in path.split("/") if part]
    if not components:
        raise StorageError("storage root is invalid")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        _check_path_ancestor(fd, "storage root ancestor /", configured_root=False)
        for index, part in enumerate(components):
            label = "configured storage root" if index == len(components) - 1 else "storage root ancestor"
            try:
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=fd)
            except FileNotFoundError:
                # Only create below an existing directory owned by this user.
                if os.fstat(fd).st_uid != os.getuid():
                    raise StorageError("storage root parent does not exist")
                os.mkdir(part, PRIVATE_MODE, dir_fd=fd)
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=fd)
            try:
                _check_path_ancestor(child, label, configured_root=index == len(components) - 1)
            except Exception:
                os.close(child)
                raise
            os.close(fd)
            fd = child
        return fd
    except Exception:
        os.close(fd)
        raise


def open_private_dir(root: str, *parts: str) -> tuple[int, str]:
    """Open/create a private subtree without following any path component."""
    path = os.path.abspath(os.path.expanduser(root))
    base_fd = _open_configured_root(path)
    try:
        leaf = "omarchy"
        try:
            os.mkdir(leaf, PRIVATE_MODE, dir_fd=base_fd)
        except FileExistsError:
            pass
        fd = os.open(leaf, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=base_fd)
    finally:
        os.close(base_fd)
    try:
        _check_dir(fd, "storage root", private=True)
        current_path = os.path.join(path, "omarchy")
        for part in parts:
            if not part or part in (".", "..") or "/" in part:
                raise StorageError("invalid storage component")
            try:
                os.mkdir(part, PRIVATE_MODE, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            current_path = os.path.join(current_path, part)
            _check_dir(fd, "storage directory", private=True)
        return fd, current_path
    except Exception:
        os.close(fd)
        raise


def _check_regular(fd: int, label: str, max_bytes: int | None = None) -> os.stat_result:
    value = os.fstat(fd)
    if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or value.st_nlink != 1:
        raise StorageError(f"{label} is not a private user-owned regular file")
    if value.st_mode & 0o077:
        os.fchmod(fd, FILE_MODE)
    if max_bytes is not None and value.st_size > max_bytes:
        raise StorageError(f"{label} exceeds its size limit")
    return value


def _open_regular_at(dir_fd: int, name: str, max_bytes: int | None = None) -> tuple[int, os.stat_result]:
    """Open a regular entry without waiting on FIFOs or special devices."""
    before = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise StorageError(f"{name} is not a private user-owned regular file")
    fd = os.open(
        name,
        os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW,
        dir_fd=dir_fd,
    )
    try:
        value = _check_regular(fd, name, max_bytes)
        if (value.st_dev, value.st_ino) != (before.st_dev, before.st_ino):
            raise StorageError(f"{name} changed while being opened")
        return fd, value
    except Exception:
        os.close(fd)
        raise


def read_file_at(dir_fd: int, name: str, max_bytes: int, default: bytes = b"") -> bytes:
    try:
        fd, value = _open_regular_at(dir_fd, name, max_bytes)
    except FileNotFoundError:
        return default
    try:
        data = bytearray()
        while len(data) < value.st_size:
            chunk = os.read(fd, min(65536, value.st_size - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if os.read(fd, 1):
            raise StorageError(f"{name} changed while being read")
        return bytes(data)
    finally:
        os.close(fd)


def atomic_write_at(dir_fd: int, name: str, data: bytes, max_bytes: int) -> None:
    if len(data) > max_bytes:
        raise StorageError(f"{name} exceeds its size limit")
    temporary = f".{name}.{os.getpid()}.{secrets.token_hex(6)}"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, FILE_MODE, dir_fd=dir_fd)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(fd, data[offset:])
        os.fsync(fd)
        _check_regular(fd, temporary)
    except Exception:
        try: os.unlink(temporary, dir_fd=dir_fd)
        except OSError: pass
        raise
    finally:
        os.close(fd)
    try:
        try:
            existing, _ = _open_regular_at(dir_fd, name)
        except FileNotFoundError:
            existing = -1
        if existing >= 0:
            os.close(existing)
        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    except Exception:
        try: os.unlink(temporary, dir_fd=dir_fd)
        except OSError: pass
        raise


def unlink_regular_at(dir_fd: int, name: str) -> bool:
    try:
        fd, _ = _open_regular_at(dir_fd, name)
    except FileNotFoundError:
        return False
    os.close(fd)
    os.unlink(name, dir_fd=dir_fd)
    return True


def quarantine_entry_at(dir_fd: int, name: str) -> str | None:
    """Rename one unsafe/unreadable directory entry without following it."""
    try:
        os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    quarantine = f"{name}.quarantine-{stamp}-{secrets.token_hex(6)}"
    os.rename(name, quarantine, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    os.fsync(dir_fd)
    return quarantine
