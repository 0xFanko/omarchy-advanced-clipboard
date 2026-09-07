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
SCREENSHOT_WIDTH = 1280
SCREENSHOT_HEIGHT = 720
SCREENSHOT_TIMEOUT_SECONDS = 12.0
HELPER_TIMEOUT_SECONDS = REQUEST_TIMEOUT_SECONDS + SCREENSHOT_TIMEOUT_SECONDS + 1.0


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

    title = clean_text(parser.properties.get("og:title", "") or " ".join(parser.title_parts))[:300]
    description = clean_text(parser.properties.get("og:description", "") or parser.names.get("description", ""))[:1000]
    return {"title": title, "description": description}


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

    def request_once(self, target: ValidatedUrl, timeout: float) -> Response:
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
            "Accept: text/html, application/xhtml+xml",
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
                chunk = process.stdout.read(min(64 * 1024, self.max_body_bytes - len(body) + 1))
                if not chunk:
                    break
                remaining = self.max_body_bytes - len(body)
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

    def fetch(self, initial_url: str) -> dict[str, str]:
        deadline = time.monotonic() + self.timeout
        current_url = initial_url

        for redirect_count in range(self.max_redirects + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PreviewError("The preview request timed out.")

            target = self.validate_url(current_url)
            response = self.request_once(target, remaining)
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

            content_type = response.headers.get("content-type", "").lower()
            if content_type and "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                raise PreviewError("The address does not return an HTML page.")
            metadata = parse_metadata(response.body, content_type, target.url)
            metadata["resolved_url"] = target.url
            metadata["resolved_host"] = target.host
            metadata["resolved_address"] = target.address
            return metadata

        raise PreviewError("The page redirected too many times.")


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


def screenshot_cache_path(url: str) -> str:
    cache_root = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    cache_dir = os.path.join(cache_root, "omarchy", "clipboard-link-previews")
    os.makedirs(cache_dir, mode=0o700, exist_ok=True)
    return os.path.join(cache_dir, hashlib.sha256(url.encode("utf-8")).hexdigest() + ".png")


def capture_screenshot(url: str, host: str, address: str) -> str:
    """Render one inert viewport while keeping Chromium on the validated host.

    The catch-all resolver rule denies third-party hosts. Besides limiting remote
    content, this prevents a page from using the preview browser to probe local
    services. The selected host remains pinned to the address validated above.
    """
    output_path = screenshot_cache_path(url)
    if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
        return output_path

    chromium = next((path for path in ("/usr/bin/chromium", "/usr/bin/chromium-browser") if os.path.isfile(path)), "")
    if not chromium:
        raise PreviewError("Chromium is required for the visual page preview.")

    # Chromium infers the encoder from the final suffix, so the staging file
    # must keep a .png extension.
    temporary_output = output_path + f".{os.getpid()}.tmp.png"
    resolver_rules = f"MAP {host} {address}, MAP * ~NOTFOUND"
    with tempfile.TemporaryDirectory(prefix="omarchy-link-preview-") as profile_dir:
        command = [
            chromium,
            "--headless=new",
            "--disable-background-networking",
            "--disable-breakpad",
            "--disable-component-update",
            "--disable-default-apps",
            "--disable-extensions",
            "--disable-features=OptimizationHints,MediaRouter",
            "--disable-sync",
            "--hide-scrollbars",
            "--metrics-recording-only",
            "--mute-audio",
            "--no-first-run",
            "--no-proxy-server",
            f"--host-resolver-rules={resolver_rules}",
            f"--user-data-dir={profile_dir}",
            f"--window-size={SCREENSHOT_WIDTH},{SCREENSHOT_HEIGHT}",
            f"--screenshot={temporary_output}",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=SCREENSHOT_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise PreviewError("The visual page preview timed out.") from error

    if completed.returncode != 0 or not os.path.isfile(temporary_output) or os.path.getsize(temporary_output) == 0:
        try:
            os.unlink(temporary_output)
        except FileNotFoundError:
            pass
        raise PreviewError("The visual page preview could not be rendered.")
    os.replace(temporary_output, output_path)
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
        screenshot = capture_screenshot(
            metadata.pop("resolved_url"),
            metadata.pop("resolved_host"),
            metadata.pop("resolved_address"),
        )
        state = "ready" if screenshot or metadata["title"] or metadata["description"] else "empty"
        print(result(serial, state, screenshot=screenshot, **metadata))
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
