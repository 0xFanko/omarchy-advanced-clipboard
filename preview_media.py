"""Download and cache bounded link-preview images supported by Qt6."""

from __future__ import annotations

import hashlib
import os
import resource
import signal
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from preview_network import NetworkClient, PreviewError, trusted_system_path
from secure_storage import StorageError, atomic_write_at, open_private_dir, unlink_regular_at

MEDIA_MAX_BYTES = 5 * 1024 * 1024
MEDIA_TIMEOUT_SECONDS = 5.0
CACHE_MAX_FILES = 100
CACHE_MAX_BYTES = 100 * 1024 * 1024
CACHE_TTL_SECONDS = 24 * 60 * 60
NORMALIZED_EXTENSION = ".png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAGICK_CANDIDATES = (("/usr/bin/magick", "magick"), ("/bin/magick", "magick"),
                     ("/usr/bin/convert", "convert"), ("/bin/convert", "convert"))
_CHILDREN: set[subprocess.Popen] = set()
_CHILDREN_LOCK = threading.Lock()
SOURCE_IMAGE_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def trusted_decoder() -> tuple[str, str]:
    for candidate, style in MAGICK_CANDIDATES:
        decoder = trusted_system_path(candidate)
        if decoder: return decoder, style
    raise PreviewError("The image decoder is unavailable.")


def terminate_process_tree(process: subprocess.Popen, grace: float = 0.2) -> None:
    try: os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError: pass
    try: process.wait(timeout=grace)
    except subprocess.TimeoutExpired: pass
    try: os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError: pass
    try: process.wait(timeout=grace)
    except subprocess.TimeoutExpired: pass
    with _CHILDREN_LOCK: _CHILDREN.discard(process)


def terminate_all_children() -> None:
    with _CHILDREN_LOCK: children = list(_CHILDREN)
    for process in children: terminate_process_tree(process)


def cache_directory() -> Path:
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    descriptor, path = open_private_dir(cache_root, "clipboard-link-media-v4")
    os.close(descriptor)
    return Path(path)


def cache_descriptor() -> tuple[int, Path]:
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    descriptor, path = open_private_dir(cache_root, "clipboard-link-media-v4")
    return descriptor, Path(path)


def cache_path(url: str) -> Path:
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_directory() / (name + NORMALIZED_EXTENSION)


def cached_image(url: str, now: float | None = None) -> str:
    cutoff = (time.time() if now is None else now) - CACHE_TTL_SECONDS
    descriptor, directory = cache_descriptor()
    name = hashlib.sha256(url.encode("utf-8")).hexdigest() + NORMALIZED_EXTENSION
    try:
        try: fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=descriptor)
        except FileNotFoundError: return ""
        try:
            value = os.fstat(fd)
            if not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid() or value.st_nlink != 1:
                raise StorageError("unsafe preview cache entry")
            if value.st_size > 0 and value.st_size <= MEDIA_MAX_BYTES and value.st_mtime >= cutoff:
                return str(directory / name)
        finally: os.close(fd)
        unlink_regular_at(descriptor, name)
        return ""
    finally: os.close(descriptor)


def prune_cache(preserve: Path | None = None) -> None:
    descriptor, directory = cache_descriptor()
    preserve_name = preserve.name if preserve and preserve.parent == directory else ""
    entries: list[tuple[float, int, str]] = []
    try:
        for name in os.listdir(descriptor):
            if name == preserve_name or not name.endswith(NORMALIZED_EXTENSION): continue
            try: value = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            except OSError: continue
            if stat.S_ISREG(value.st_mode) and value.st_uid == os.getuid() and value.st_nlink == 1:
                entries.append((value.st_mtime, value.st_size, name))
        preserved_size = 0
        if preserve_name:
            try:
                value = os.stat(preserve_name, dir_fd=descriptor, follow_symlinks=False)
                if stat.S_ISREG(value.st_mode) and value.st_uid == os.getuid() and value.st_nlink == 1: preserved_size = value.st_size
            except OSError: pass
        total_files = len(entries) + (1 if preserved_size else 0)
        total_bytes = sum(size for _, size, _ in entries) + preserved_size
        for _, size, name in sorted(entries):
            if total_files <= CACHE_MAX_FILES and total_bytes <= CACHE_MAX_BYTES: break
            try: unlink_regular_at(descriptor, name)
            except OSError: continue
            total_files -= 1; total_bytes -= size
    finally: os.close(descriptor)


def normalize_image(body: bytes, input_format: str) -> bytes:
    decoder, decoder_style = trusted_decoder()

    command = [
        decoder,
        "-limit", "memory", "64MiB",
        "-limit", "map", "64MiB",
        "-limit", "disk", "64MiB",
        "-limit", "width", "8192",
        "-limit", "height", "8192",
        "-limit", "area", "64MP",
        "-limit", "thread", "1",
        "-limit", "time", "5",
        f"{input_format}:-", "-auto-orient", "-thumbnail", "1280x720>",
        "-strip", "-depth", "8", "png:-",
    ]
    def setup() -> None:
        os.setsid()
        resource.setrlimit(resource.RLIMIT_FSIZE, (MEDIA_MAX_BYTES, MEDIA_MAX_BYTES))
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=subprocess.DEVNULL,
                                       env={"PATH": "/usr/bin:/bin", "LANG": os.environ.get("LANG", "C.UTF-8")}, preexec_fn=setup)
            with _CHILDREN_LOCK: _CHILDREN.add(process)
            try: process.communicate(body, timeout=7)
            except subprocess.TimeoutExpired as error:
                terminate_process_tree(process)
                raise PreviewError("The preview image normalization timed out.") from error
            finally:
                # ImageMagick may invoke delegates. Reap the complete session
                # even when the main decoder has already exited successfully.
                terminate_process_tree(process)
            size = os.fstat(output.fileno()).st_size
            if size > MEDIA_MAX_BYTES: raise PreviewError("The normalized preview image is too large.")
            output.seek(0); normalized = output.read(MEDIA_MAX_BYTES + 1)
            return_code = process.returncode
    except OSError as error:
        raise PreviewError("The image decoder is unavailable.") from error

    if return_code != 0 or not normalized.startswith(PNG_SIGNATURE):
        raise PreviewError("The preview image could not be normalized safely.")
    if len(normalized) > MEDIA_MAX_BYTES:
        raise PreviewError("The normalized preview image is too large.")
    return normalized


def store_image(url: str, body: bytes) -> str:
    descriptor, directory = cache_descriptor()
    name = hashlib.sha256(url.encode("utf-8")).hexdigest() + NORMALIZED_EXTENSION
    try: atomic_write_at(descriptor, name, body, MEDIA_MAX_BYTES)
    finally: os.close(descriptor)
    path = directory / name
    prune_cache(path)
    return str(path)


def fetch_preview_image(url: str, client: NetworkClient | None = None) -> str:
    try:
        existing = cached_image(url)
    except OSError as error:
        raise PreviewError("The preview image cache is unavailable.") from error
    if existing:
        return existing

    response, _ = (client or NetworkClient(timeout=MEDIA_TIMEOUT_SECONDS)).fetch(
        url,
        accept=", ".join(SOURCE_IMAGE_TYPES),
        max_body_bytes=MEDIA_MAX_BYTES,
    )
    if len(response.body) > MEDIA_MAX_BYTES:
        raise PreviewError("The remote preview image is too large.")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in SOURCE_IMAGE_TYPES:
        raise PreviewError("The preview image type is not supported.")
    normalized = normalize_image(response.body, SOURCE_IMAGE_TYPES[content_type])
    try:
        return store_image(url, normalized)
    except OSError as error:
        raise PreviewError("The preview image cache is unavailable.") from error
