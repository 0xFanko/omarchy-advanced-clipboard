"""Bounded HTTP client with DNS pinning and SSRF protection."""

from __future__ import annotations

import ctypes
import ipaddress
import os
import re
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

MAX_REDIRECTS = 3
MAX_URL_LENGTH = 8192
REQUEST_TIMEOUT_SECONDS = 8.0


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


class NetworkPolicy:
    """Reject every address that is not globally routable in production."""

    def __init__(self, allow_non_global: bool = False) -> None:
        self.allow_non_global = allow_non_global

    def validate_address(self, value: str) -> str:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as error:
            raise PreviewError("The address has an invalid IP resolution.") from error

        checked = getattr(address, "ipv4_mapped", None) or address
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


class NetworkClient:
    def __init__(
        self,
        *,
        policy: NetworkPolicy | None = None,
        resolver: Callable[..., list[tuple]] = socket.getaddrinfo,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        max_redirects: int = MAX_REDIRECTS,
    ) -> None:
        self.policy = policy or NetworkPolicy()
        self.resolver = resolver
        self.timeout = timeout
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
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                answers = self.resolver(host, port, socket.AF_UNSPEC, socket.SOCK_STREAM)
            except OSError as error:
                raise PreviewError("The page host could not be resolved.") from error
            addresses = sorted({answer[4][0] for answer in answers})

        if not addresses:
            raise PreviewError("The page host has no usable address.")
        validated = [self.policy.validate_address(address) for address in addresses]
        selected = sorted(validated, key=lambda address: (":" in address, address))[0]

        netloc = f"[{host}]" if ":" in host else host
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        if port != default_port:
            netloc += f":{port}"
        normalized = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))
        return ValidatedUrl(normalized, host, port, selected)

    def request_once(
        self,
        target: ValidatedUrl,
        timeout: float,
        accept: str,
        max_body_bytes: int,
    ) -> Response:
        command = [
            "curl", "--disable", "--silent", "--show-error", "--noproxy", "*",
            "--max-redirs", "0", "--proto", "=http,https",
            "--connect-timeout", str(max(0.1, timeout)),
            "--max-time", str(max(0.1, timeout)),
            "--header", f"Accept: {accept}", "--output", "-",
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
                chunk = process.stdout.read(min(64 * 1024, max_body_bytes - len(body) + 1))
                if not chunk:
                    break
                remaining = max_body_bytes - len(body)
                if len(chunk) > remaining:
                    body.extend(chunk[:remaining])
                    too_large = True
                    process.kill()
                    break
                body.extend(chunk)
            process.stdout.close()
            return_code = process.wait()

            if too_large:
                raise PreviewError("The remote content is too large to preview safely.")
            if return_code != 0:
                if return_code == 28:
                    raise PreviewError("The preview request timed out.")
                error_file.seek(0)
                detail = clean_error(error_file.read(512).decode("utf-8", errors="replace"))
                raise PreviewError(detail or "The remote content could not be reached.")

            header_file.seek(0)
            status, headers = parse_headers(header_file.read())
            return Response(status, headers, bytes(body))

    def fetch(self, initial_url: str, *, accept: str, max_body_bytes: int) -> tuple[Response, ValidatedUrl]:
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
                    raise PreviewError("The remote content returned a redirect without a destination.")
                if redirect_count >= self.max_redirects:
                    raise PreviewError("The remote content redirected too many times.")
                current_url = urljoin(target.url, location)
                continue

            if response.status < 200 or response.status >= 300:
                raise PreviewError(f"The remote content returned HTTP {response.status}.")
            return response, target

        raise PreviewError("The remote content redirected too many times.")


def clean_error(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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
    raise PreviewError("The remote content returned an invalid HTTP response.")
