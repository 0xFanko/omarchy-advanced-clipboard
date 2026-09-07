#!/usr/bin/env python3
"""Fetch inert page metadata without exposing the shell UI process to the network."""

from __future__ import annotations

import ctypes
import hashlib
import ipaddress
import json
import os
import re
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_BODY_BYTES = 512 * 1024
MAX_REDIRECTS = 3
REQUEST_TIMEOUT_SECONDS = 8.0
MAX_URL_LENGTH = 8192
MEDIA_MAX_BYTES = 5 * 1024 * 1024
MEDIA_TIMEOUT_SECONDS = 5.0
HELPER_TIMEOUT_SECONDS = 14.0
CACHE_MAX_FILES = 100
CACHE_MAX_BYTES = 100 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "image/gif": (".gif", (b"GIF87a", b"GIF89a")),
    "image/webp": (".webp", b"RIFF"),
}


class PreviewError(Exception):
    pass


@dataclass(frozen=True)
class ValidatedUrl:
    url: str
    host: str
    port: int
    address: str


@dataclass(frozen=True)
class Response:
    status: int
    headers: dict[str, str]
    body: bytes


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.properties: dict[str, str] = {}
        self.names: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.in_title = False
        self.ignored_depth = 0
        self.base_href = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        if tag in ("script", "style"):
            self.ignored_depth += 1
        elif tag == "title":
            self.in_title = True
        elif tag == "base" and not self.base_href:
            self.base_href = values.get("href", "").strip()
        elif tag == "meta":
            content = clean_text(values.get("content", ""))
            prop = values.get("property", "").strip().lower()
            name = values.get("name", "").strip().lower()
            if content and prop and prop not in self.properties:
                self.properties[prop] = content
            if content and name and name not in self.names:
                self.names[name] = content

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in ("script", "style") and self.ignored_depth:
            self.ignored_depth -= 1
        elif tag == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title and not self.ignored_depth:
            self.title_parts.append(data)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def parse_metadata(body: bytes, content_type: str, page_url: str) -> dict[str, str]:
    charset_match = re.search(r"charset\s*=\s*['\"]?([^\s;'\"]+)", content_type, re.I)
    charset = charset_match.group(1) if charset_match else "utf-8"
    try:
        html = body.decode(charset, errors="replace")
    except LookupError:
        html = body.decode("utf-8", errors="replace")

    parser = MetadataParser()
    try:
        parser.feed(html)
    except Exception:
        # HTMLParser is intentionally best effort for malformed remote markup.
        pass

    title = clean_text(
        parser.properties.get("og:title", "")
        or parser.names.get("twitter:title", "")
        or " ".join(parser.title_parts)
    )[:300]
    description = clean_text(
        parser.properties.get("og:description", "")
        or parser.names.get("twitter:description", "")
        or parser.names.get("description", "")
    )[:1000]
    image = clean_text(
        parser.properties.get("og:image:secure_url", "")
        or parser.properties.get("og:image", "")
        or parser.names.get("twitter:image", "")
        or parser.names.get("twitter:image:src", "")
    )
    base_url = urljoin(page_url, parser.base_href) if parser.base_href else page_url
    image_url = urljoin(base_url, image) if image else ""
    if image_url and urlsplit(image_url).scheme.lower() not in ("http", "https"):
        image_url = ""
    site = clean_text(parser.properties.get("og:site_name", "") or (urlsplit(page_url).hostname or ""))[:200]
    return {"title": title, "description": description, "image_url": image_url, "site": site}


class NetworkPolicy:
    """Production rejects every address that is not globally routable.

    allow_non_global exists only as an in-process test seam. It is deliberately
    not exposed by the command-line entry point.
    """

    def __init__(self, allow_non_global: bool = False) -> None:
        self.allow_non_global = allow_non_global

    def validate_address(self, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise PreviewError("The address has an invalid IP resolution.") from error

        mapped = getattr(address, "ipv4_mapped", None)
        checked = mapped or address
        unsafe = (
            not checked.is_global
            or checked.is_private
            or checked.is_loopback
            or checked.is_link_local
            or checked.is_multicast
            or checked.is_reserved
            or checked.is_unspecified
        )
        if not self.allow_non_global and unsafe:
            raise PreviewError("Preview blocked for a local or private network address.")
        return str(address)


class PreviewClient:
    def __init__(
        self,
        *,
        policy: NetworkPolicy | None = None,
        resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        max_body_bytes: int = MAX_BODY_BYTES,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self.policy = policy or NetworkPolicy()
        self.resolver = resolver
        self.timeout = timeout
        self.max_body_bytes = max_body_bytes
        self.max_redirects = max_redirects

    def validate_url(self, value: str) -> ValidatedUrl:
        raw = str(value).strip()
        if not raw or len(raw) > MAX_URL_LENGTH or any(ord(char) < 32 for char in raw):
            raise PreviewError("The URL is invalid or too long.")

        parsed = urlsplit(raw)
        if parsed.scheme.lower() not in ("http", "https"):
            raise PreviewError("Only HTTP(S) pages can be previewed.")
        if parsed.username is not None or parsed.password is not None:
            raise PreviewError("URLs containing credentials cannot be previewed.")
        if not parsed.hostname:
            raise PreviewError("The URL has no host.")

        try:
            host = parsed.hostname.encode("idna").decode("ascii").lower()
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except (UnicodeError, ValueError) as error:
            raise PreviewError("The URL host or port is invalid.") from error

        try:
            literal = ipaddress.ip_address(host)
            addresses = [str(literal)]
        except ValueError:
            try:
                answers = self.resolver(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except OSError as error:
                raise PreviewError("The page host could not be resolved.") from error
            addresses = sorted({answer[4][0] for answer in answers})

        if not addresses:
            raise PreviewError("The page host has no usable address.")

        # Reject the host if any DNS answer is unsafe; selecting only a public
        # answer from a mixed response would make rebinding protection ambiguous.
        validated = [self.policy.validate_address(address) for address in addresses]
        selected = sorted(validated, key=lambda address: (":" in address, address))[0]

        netloc = f"[{host}]" if ":" in host else host
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        if port != default_port:
            netloc += f":{port}"
        normalized = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
        return ValidatedUrl(normalized, host, port, selected)

    def request_once(self, target: ValidatedUrl, timeout: float, accept: str, max_body_bytes: int) -> Response:
        command = [
            "curl",
            "--disable",
            "--silent",
            "--show-error",
            "--noproxy",
            "*",
            "--max-redirs",
            "0",
            "--proto",
            "=http,https",
            "--connect-timeout",
            str(max(0.1, timeout)),
            "--max-time",
            str(max(0.1, timeout)),
            "--header",
            f"Accept: {accept}",
            "--output",
            "-",
        ]
        if target.host != target.address:
            resolve_address = f"[{target.address}]" if ":" in target.address else target.address
            command.extend(("--resolve", f"{target.host}:{target.port}:{resolve_address}"))

        environment = os.environ.copy()
        for name in list(environment):
            if name.lower() in ("http_proxy", "https_proxy", "all_proxy", "no_proxy"):
                environment.pop(name, None)

        with tempfile.NamedTemporaryFile() as header_file, tempfile.TemporaryFile() as error_file:
            command.extend(("--dump-header", header_file.name, target.url))
            parent_pid = os.getpid()

            def terminate_with_parent() -> None:
                # The QML Process is intentionally cancellable. Ensure its curl
                # child cannot survive if the helper is stopped on selection or
                # window changes.
                libc = ctypes.CDLL(None)
                libc.prctl(1, signal.SIGTERM)
                if os.getppid() != parent_pid:
                    os.kill(os.getpid(), signal.SIGTERM)

            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=error_file,
                env=environment,
                preexec_fn=terminate_with_parent,
            )
            body = bytearray()
            too_large = False
            assert process.stdout is not None
            while True:
                # Read at most one byte beyond the remaining allowance so an
                # oversized response is detected before it is accumulated.
                chunk = process.stdout.read(min(64 * 1024, max_body_bytes - len(body) + 1))
                if not chunk:
                    break
                remaining = max_body_bytes - len(body)
                if len(chunk) > remaining:
                    if remaining > 0:
                        body.extend(chunk[:remaining])
                    too_large = True
                    process.kill()
                    break
                body.extend(chunk)
            process.stdout.close()
            return_code = process.wait()

            if too_large:
                raise PreviewError("The page is too large to preview safely.")
            if return_code != 0:
                if return_code == 28:
                    raise PreviewError("The preview request timed out.")
                error_file.seek(0)
                detail = clean_text(error_file.read(512).decode("utf-8", errors="replace"))
                raise PreviewError(detail or "The page could not be reached.")

            header_file.seek(0)
            status, headers = parse_headers(header_file.read())
            return Response(status, headers, bytes(body))

    def fetch_response(self, initial_url: str, accept: str, max_body_bytes: int) -> tuple[Response, ValidatedUrl]:
        deadline = time.monotonic() + self.timeout
        current_url = initial_url

        for redirect_count in range(self.max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PreviewError("The preview request timed out.")

            target = self.validate_url(current_url)
            response = self.request_once(target, remaining, accept, max_body_bytes)
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get("location", "").strip()
                if not location:
                    raise PreviewError("The page returned a redirect without a destination.")
                if redirect_count >= self.max_redirects:
                    raise PreviewError("The page redirected too many times.")
                current_url = urljoin(target.url, location)
                # Validation happens at the start of the next iteration, before
                # curl is spawned for the redirect destination.
                continue

            if response.status < 200 or response.status >= 300:
                raise PreviewError(f"The page returned HTTP {response.status}.")

            return response, target

        raise PreviewError("The page redirected too many times.")

    def fetch(self, initial_url: str) -> dict[str, str]:
        response, target = self.fetch_response(
            initial_url, "text/html, application/xhtml+xml", self.max_body_bytes
        )
        content_type = response.headers.get("content-type", "").lower()
        if content_type and "text/html" not in content_type and "application/xhtml+xml" not in content_type:
            raise PreviewError("The address does not return an HTML page.")
        return parse_metadata(response.body, content_type, target.url)

    def fetch_image(self, initial_url: str) -> tuple[bytes, str, str]:
        response, target = self.fetch_response(initial_url, ", ".join(IMAGE_TYPES), MEDIA_MAX_BYTES)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in IMAGE_TYPES:
            raise PreviewError("The preview image type is not supported.")
        extension, signature = IMAGE_TYPES[content_type]
        signatures = signature if isinstance(signature, tuple) else (signature,)
        if not any(response.body.startswith(value) for value in signatures):
            raise PreviewError("The preview image content is invalid.")
        if content_type == "image/webp" and response.body[8:12] != b"WEBP":
            raise PreviewError("The preview image content is invalid.")
        return response.body, extension, target.url


def parse_headers(raw: bytes) -> tuple[int, dict[str, str]]:
    blocks = re.split(br"\r?\n\r?\n", raw.strip())
    for block in reversed(blocks):
        lines = block.splitlines()
        if not lines or not lines[0].startswith(b"HTTP/"):
            continue
        match = re.match(br"HTTP/\S+\s+(\d{3})", lines[0])
        if not match:
            continue
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if b":" not in line:
                continue
            name, value = line.split(b":", 1)
            headers[name.decode("ascii", errors="ignore").strip().lower()] = value.decode("latin-1").strip()
        return int(match.group(1)), headers
    raise PreviewError("The page returned an invalid HTTP response.")


def result(serial: int, state: str, **values: str) -> str:
    payload = {"serial": serial, "state": state}
    payload.update(values)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def media_cache_dir() -> str:
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    cache_dir = os.path.join(cache_root, "omarchy", "clipboard-link-media")
    os.makedirs(cache_dir, mode=0o700, exist_ok=True)
    return cache_dir


def media_cache_path(url: str, extension: str) -> str:
    return os.path.join(media_cache_dir(), hashlib.sha256(url.encode("utf-8")).hexdigest() + extension)


def cached_media_path(url: str) -> str:
    for extension, _ in IMAGE_TYPES.values():
        path = media_cache_path(url, extension)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                os.utime(path)
                prune_media_cache(os.path.dirname(path), path)
                return path
        except OSError:
            continue
    return ""


def prune_media_cache(cache_dir: str, preserve: str = "") -> None:
    entries: list[tuple[float, int, str]] = []
    try:
        names = os.listdir(cache_dir)
    except OSError:
        return

    for name in names:
        path = os.path.join(cache_dir, name)
        if not any(name.endswith(extension) for extension, _ in IMAGE_TYPES.values()) or path == preserve:
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue
        if os.path.isfile(path):
            entries.append((stat.st_mtime, stat.st_size, path))

    preserved_size = 0
    if preserve:
        try:
            preserved_size = os.path.getsize(preserve)
        except OSError:
            pass
    total_files = len(entries) + (1 if preserved_size else 0)
    total_bytes = sum(entry[1] for entry in entries) + preserved_size

    for _, size, path in sorted(entries):
        if total_files <= CACHE_MAX_FILES and total_bytes <= CACHE_MAX_BYTES:
            break
        try:
            os.unlink(path)
        except OSError:
            continue
        total_files -= 1
        total_bytes -= size


def cache_image(url: str, body: bytes, extension: str) -> str:
    output_path = media_cache_path(url, extension)
    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        os.utime(output_path)
        prune_media_cache(os.path.dirname(output_path), output_path)
        return output_path

    descriptor, temporary_path = tempfile.mkstemp(prefix=".preview-", dir=os.path.dirname(output_path))
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(body)
        os.replace(temporary_path, output_path)
    finally:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
    prune_media_cache(os.path.dirname(output_path), output_path)
    return output_path


def main(argv: list[str]) -> int:
    serial = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 0
    if len(argv) < 2:
        print(result(serial, "error", error="A page URL is required."))
        return 0

    def deadline_expired(signum, frame) -> None:
        raise PreviewError("The preview request timed out.")

    previous_handler = signal.signal(signal.SIGALRM, deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, HELPER_TIMEOUT_SECONDS)
    try:
        metadata = PreviewClient().fetch(argv[1])
        image_path = ""
        image_url = metadata.pop("image_url", "")
        if image_url:
            image_path = cached_media_path(image_url)
            if not image_path:
                try:
                    body, extension, _ = PreviewClient(timeout=MEDIA_TIMEOUT_SECONDS).fetch_image(image_url)
                    image_path = cache_image(image_url, body, extension)
                except PreviewError:
                    # Metadata remains useful when a publisher's image is missing,
                    # invalid, oversized, or hosted somewhere we deliberately block.
                    pass
        state = "ready" if image_path or metadata["title"] or metadata["description"] else "empty"
        print(result(serial, state, image=image_path, **metadata))
    except PreviewError as error:
        print(result(serial, "error", error=str(error)))
    except Exception:
        print(result(serial, "error", error="The page preview failed unexpectedly."))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
