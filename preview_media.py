"""Download and cache bounded link-preview images supported by Qt6."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from preview_network import NetworkClient, PreviewError

MEDIA_MAX_BYTES = 5 * 1024 * 1024
MEDIA_TIMEOUT_SECONDS = 5.0
CACHE_MAX_FILES = 100
CACHE_MAX_BYTES = 100 * 1024 * 1024
CACHE_TTL_SECONDS = 24 * 60 * 60
NORMALIZED_EXTENSION = ".png"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SOURCE_IMAGE_TYPES = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def cache_directory() -> Path:
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    path = Path(cache_root) / "omarchy" / "clipboard-link-media-v4"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def cache_path(url: str) -> Path:
    name = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return cache_directory() / (name + NORMALIZED_EXTENSION)


def cached_image(url: str, now: float | None = None) -> str:
    cutoff = (time.time() if now is None else now) - CACHE_TTL_SECONDS
    path = cache_path(url)
    try:
        stat = path.stat()
        if stat.st_size > 0 and stat.st_mtime >= cutoff:
            return str(path)
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return ""


def prune_cache(preserve: Path | None = None) -> None:
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
        except OSError:
            continue
        total_files -= 1
        total_bytes -= size


def normalize_image(body: bytes, input_format: str) -> bytes:
    magick = shutil.which("magick")
    if not magick:
        raise PreviewError("The image decoder is unavailable.")

    command = [
        os.path.realpath(magick),
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
    try:
        completed = subprocess.run(
            command,
            input=body,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=7,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PreviewError("The preview image normalization timed out.") from error
    except OSError as error:
        raise PreviewError("The image decoder is unavailable.") from error

    normalized = completed.stdout
    if completed.returncode != 0 or not normalized.startswith(PNG_SIGNATURE):
        raise PreviewError("The preview image could not be normalized safely.")
    if len(normalized) > MEDIA_MAX_BYTES:
        raise PreviewError("The normalized preview image is too large.")
    return normalized


def store_image(url: str, body: bytes) -> str:
    path = cache_path(url)
    descriptor, temporary = tempfile.mkstemp(prefix=".preview-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
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
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in SOURCE_IMAGE_TYPES:
        raise PreviewError("The preview image type is not supported.")
    normalized = normalize_image(response.body, SOURCE_IMAGE_TYPES[content_type])
    try:
        return store_image(url, normalized)
    except OSError as error:
        raise PreviewError("The preview image cache is unavailable.") from error
