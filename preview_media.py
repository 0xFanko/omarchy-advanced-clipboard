"""Download, sandbox-normalize, and cache untrusted preview images."""

from __future__ import annotations

import fcntl
import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from preview_network import NetworkClient, PreviewError

MEDIA_MAX_BYTES = 5 * 1024 * 1024
MEDIA_TIMEOUT_SECONDS = 5.0
CACHE_MAX_FILES = 100
CACHE_MAX_BYTES = 100 * 1024 * 1024
CACHE_TTL_SECONDS = 24 * 60 * 60
NORMALIZED_EXTENSION = ".png"
SUPPORTED_IMAGE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
IMAGE_SIGNATURES = {
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
}


def cache_directory() -> Path:
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    # Versioned so files cached before sandbox normalization are never trusted.
    path = Path(cache_root) / "omarchy" / "clipboard-link-media-v3"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def cache_path(url: str) -> Path:
    return cache_directory() / (hashlib.sha256(url.encode("utf-8")).hexdigest() + NORMALIZED_EXTENSION)


def creation_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".created")


@contextmanager
def cache_lock() -> Iterator[None]:
    descriptor = os.open(cache_directory() / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def production_lock(url: str) -> Iterator[None]:
    root = Path(tempfile.gettempdir()) / ("omarchy-preview-locks-" + str(os.getuid()))
    root.mkdir(mode=0o700, exist_ok=True)
    descriptor = os.open(root / hashlib.sha256(url.encode("utf-8")).hexdigest(), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def cached_image(url: str, now: float | None = None) -> str:
    path = cache_path(url)
    cutoff = (time.time() if now is None else now) - CACHE_TTL_SECONDS
    with cache_lock():
        try:
            stat = path.stat()
            created = float(creation_path(path).read_text(encoding="ascii"))
            if stat.st_size <= 0 or created < cutoff:
                path.unlink(missing_ok=True)
                creation_path(path).unlink(missing_ok=True)
                return ""
            path.touch()
            prune_cache_locked(path)
            return str(path)
        except (OSError, ValueError):
            return ""


def prune_cache_locked(preserve: Path | None = None) -> None:
    entries: list[tuple[float, int, Path]] = []
    for path in cache_directory().glob("*" + NORMALIZED_EXTENSION):
        if path == preserve:
            continue
        try:
            stat = path.stat()
            if path.is_file():
                entries.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            continue

    preserved_size = 0
    if preserve:
        try:
            preserved_size = preserve.stat().st_size
        except OSError:
            pass
    total_files = len(entries) + (1 if preserved_size else 0)
    total_bytes = sum(size for _, size, _ in entries) + preserved_size

    for _, size, path in sorted(entries):
        if total_files <= CACHE_MAX_FILES and total_bytes <= CACHE_MAX_BYTES:
            break
        try:
            path.unlink()
            creation_path(path).unlink(missing_ok=True)
        except OSError:
            continue
        total_files -= 1
        total_bytes -= size


def normalize_image(body: bytes) -> bytes:
    bwrap = shutil.which("bwrap")
    magick = shutil.which("magick")
    if not bwrap or not magick:
        raise PreviewError("Image sandbox tools are unavailable.")
    magick_binary = os.path.realpath(magick)

    with tempfile.TemporaryDirectory(prefix="omarchy-preview-media-") as directory:
        work = Path(directory)
        (work / "input").write_bytes(body)
        command = [
            bwrap,
            "--die-with-parent", "--unshare-all", "--new-session",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp",
            "--bind", directory, "/work", "--chdir", "/work", "--clearenv",
            "--setenv", "PATH", "/usr/bin:/bin",
            magick_binary,
            "-limit", "memory", "64MiB",
            "-limit", "map", "64MiB",
            "-limit", "disk", "64MiB",
            "-limit", "width", "8192",
            "-limit", "height", "8192",
            "-limit", "area", "64MP",
            "-limit", "thread", "1",
            "-limit", "time", "5",
            "input[0]", "-auto-orient", "-thumbnail", "1280x720>",
            "-strip", "png:output.png",
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=7,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise PreviewError("The preview image normalization timed out.") from error

        output = work / "output.png"
        if completed.returncode != 0 or not output.is_file():
            raise PreviewError("The preview image could not be normalized safely.")
        normalized = output.read_bytes()
        if not normalized.startswith(b"\x89PNG\r\n\x1a\n") or len(normalized) > MEDIA_MAX_BYTES:
            raise PreviewError("The normalized preview image is invalid.")
        return normalized


def store_image(url: str, body: bytes, now: float | None = None) -> str:
    path = cache_path(url)
    with cache_lock():
        descriptor, temporary = tempfile.mkstemp(prefix=".preview-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(body)
            os.replace(temporary, path)
            creation_path(path).write_text(str(time.time() if now is None else now), encoding="ascii")
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        prune_cache_locked(path)
    return str(path)


def fetch_preview_image(url: str, client: NetworkClient | None = None) -> str:
    with production_lock(url):
        existing = cached_image(url)
        if existing:
            return existing

        response, _ = (client or NetworkClient(timeout=MEDIA_TIMEOUT_SECONDS)).fetch(
            url,
            accept=", ".join(SUPPORTED_IMAGE_TYPES),
            max_body_bytes=MEDIA_MAX_BYTES,
        )
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in SUPPORTED_IMAGE_TYPES:
            raise PreviewError("The preview image type is not supported.")
        signatures = IMAGE_SIGNATURES[content_type]
        if not any(response.body.startswith(signature) for signature in signatures):
            raise PreviewError("The preview image signature is invalid.")
        if content_type == "image/webp" and response.body[8:12] != b"WEBP":
            raise PreviewError("The preview image signature is invalid.")
        return store_image(url, normalize_image(response.body))
