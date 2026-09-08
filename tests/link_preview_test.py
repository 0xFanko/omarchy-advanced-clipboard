#!/usr/bin/env python3

import base64
import importlib.util
import os
import pathlib
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import preview_media
import preview_metadata
import preview_network

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "link_preview.py"
SPEC = importlib.util.spec_from_file_location("link_preview", MODULE_PATH)
link_preview = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = link_preview
SPEC.loader.exec_module(link_preview)


def public_resolver(host, port, family, socktype):
    return [(family, socktype, 6, "", ("93.184.216.34", port))]


class PreviewTests(unittest.TestCase):
    def test_preview_streams_metadata_before_rejected_image_completes(self):
        metadata = preview_metadata.PreviewMetadata(
            title="Safe title",
            description="Safe description",
            image_url="https://images.example.test/card.png",
            site="Example",
        )
        with mock.patch.object(link_preview, "fetch_metadata", return_value=metadata), mock.patch.object(
            link_preview,
            "fetch_preview_image",
            side_effect=preview_network.PreviewError("blocked"),
        ):
            payloads = link_preview.preview_payloads("https://example.test/")
            metadata_payload = next(payloads)
            complete_payload = next(payloads)

        self.assertEqual(metadata_payload["phase"], "metadata")
        self.assertEqual(metadata_payload["title"], "Safe title")
        self.assertEqual(complete_payload, {"phase": "complete", "image": ""})

    def test_accepts_public_ipv4_and_ipv6_dns_answers(self):
        def resolver(host, port, family, socktype):
            return [
                (family, socktype, 6, "", ("93.184.216.34", port)),
                (family, socktype, 6, "", ("2001:4860:4860::8888", port, 0, 0)),
            ]

        target = preview_network.NetworkClient(resolver=resolver).validate_url("https://example.test/page")
        self.assertEqual(target.address, "93.184.216.34")
        self.assertEqual(preview_network.NetworkClient().validate_url("https://8.8.8.8/").address, "8.8.8.8")
        self.assertEqual(
            preview_network.NetworkClient().validate_url("https://[2001:4860:4860::8888]/").address,
            "2001:4860:4860::8888",
        )

    def test_blocks_private_loopback_link_local_multicast_and_metadata_addresses(self):
        blocked = [
            "127.0.0.1",
            "10.0.0.1",
            "169.254.169.254",
            "224.0.0.1",
            "::1",
            "fc00::1",
            "fe80::1",
            "ff02::1",
            "::ffff:127.0.0.1",
        ]
        client = preview_network.NetworkClient()
        for address in blocked:
            with self.subTest(address=address), self.assertRaises(preview_network.PreviewError):
                client.validate_url(f"http://[{address}]/" if ":" in address else f"http://{address}/")

    def test_rejects_mixed_public_and_private_dns_answers(self):
        def resolver(host, port, family, socktype):
            return [
                (family, socktype, 6, "", ("93.184.216.34", port)),
                (family, socktype, 6, "", ("127.0.0.1", port)),
            ]

        with self.assertRaises(preview_network.PreviewError):
            preview_network.NetworkClient(resolver=resolver).validate_url("https://example.test/")

    def test_blocks_local_redirect_before_second_request(self):
        client = preview_network.NetworkClient(resolver=public_resolver)
        calls = []

        def request_once(target, timeout, accept, max_body_bytes):
            calls.append(target.url)
            return preview_network.Response(302, {"location": "http://127.0.0.1/admin"}, b"")

        client.request_once = request_once
        with self.assertRaises(preview_network.PreviewError):
            client.fetch("https://public.example/start", accept="text/html", max_body_bytes=1024)
        self.assertEqual(calls, ["https://public.example/start"])

    def test_parses_plain_metadata_without_returning_remote_markup(self):
        metadata = preview_metadata.parse_metadata(
            b'<title><script>alert(1)</script>Safe &amp; plain</title>'
            b'<meta property="og:description" content="A useful description">',
            "text/html; charset=utf-8",
            "https://example.test/",
        )
        self.assertEqual(metadata.title, "Safe & plain")
        self.assertEqual(metadata.description, "A useful description")
        self.assertEqual(metadata.image_url, "")
        self.assertEqual(metadata.site, "example.test")

    def test_parses_open_graph_image_against_page_base(self):
        metadata = preview_metadata.parse_metadata(
            b'<base href="https://cdn.example.test/assets/">'
            b'<meta property="og:image" content="card.png">'
            b'<meta property="og:site_name" content="Example News">',
            "text/html",
            "https://example.test/article",
        )
        self.assertEqual(metadata.image_url, "https://cdn.example.test/assets/card.png")
        self.assertEqual(metadata.site, "Example News")

    def test_media_cache_uses_a_stable_opaque_name(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            previous = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = cache_dir
            try:
                first = str(preview_media.cache_path("https://example.test/card"))
                second = str(preview_media.cache_path("https://example.test/card"))
            finally:
                if previous is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = previous

        self.assertEqual(first, second)
        self.assertTrue(first.endswith(".png"))
        self.assertNotIn("example.test", pathlib.Path(first).name)

    def test_cached_media_path_reuses_existing_image(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            previous = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = cache_dir
            try:
                expected = preview_media.cache_path("https://example.test/card")
                expected.write_bytes(b"\x89PNG\r\n\x1a\nnormalized")
                preview_media.creation_path(expected).write_text(str(time.time()), encoding="ascii")
                actual = preview_media.cached_image("https://example.test/card")
            finally:
                if previous is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = previous

        self.assertEqual(actual, str(expected))

    def test_cached_media_path_expires_after_ttl(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            previous = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = cache_dir
            try:
                expected = preview_media.cache_path("https://example.test/old-card")
                expected.write_bytes(b"\x89PNG\r\n\x1a\nnormalized")
                old = 1_000_000
                preview_media.creation_path(expected).write_text(str(old), encoding="ascii")
                actual = preview_media.cached_image(
                    "https://example.test/old-card",
                    now=old + preview_media.CACHE_TTL_SECONDS + 1,
                )
                self.assertEqual(actual, "")
                self.assertFalse(expected.exists())
                self.assertFalse(preview_media.creation_path(expected).exists())
            finally:
                if previous is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = previous

    def test_cache_hits_do_not_extend_absolute_ttl(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            previous = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = cache_dir
            try:
                url = "https://example.test/frequently-read-card"
                path = preview_media.cache_path(url)
                path.write_bytes(b"\x89PNG\r\n\x1a\nnormalized")
                created = 1_000_000
                preview_media.creation_path(path).write_text(str(created), encoding="ascii")
                self.assertEqual(preview_media.cached_image(url, now=created + 10), str(path))
                self.assertEqual(
                    preview_media.cached_image(url, now=created + preview_media.CACHE_TTL_SECONDS + 1),
                    "",
                )
            finally:
                if previous is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = previous
    def test_normalizes_image_in_sandbox_to_bounded_png(self):
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        )
        normalized = preview_media.normalize_image(png)
        self.assertTrue(normalized.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertLessEqual(len(normalized), preview_media.MEDIA_MAX_BYTES)

    def test_concurrent_cache_misses_produce_image_once(self):
        class CountingClient:
            def __init__(self):
                self.calls = 0
                self.guard = threading.Lock()

            def fetch(self, url, *, accept, max_body_bytes):
                with self.guard:
                    self.calls += 1
                time.sleep(0.05)
                return preview_network.Response(200, {"content-type": "image/png"}, b"\x89PNG\r\n\x1a\nremote"), None

        with tempfile.TemporaryDirectory() as cache_dir:
            previous = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = cache_dir
            client = CountingClient()
            results = []
            try:
                with mock.patch.object(
                    preview_media,
                    "normalize_image",
                    return_value=b"\x89PNG\r\n\x1a\nnormalized",
                ):
                    threads = [
                        threading.Thread(
                            target=lambda: results.append(
                                preview_media.fetch_preview_image("https://example.test/shared.png", client)
                            )
                        )
                        for _ in range(2)
                    ]
                    for thread in threads:
                        thread.start()
                    for thread in threads:
                        thread.join()
            finally:
                if previous is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = previous

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])

    def test_media_cache_prunes_oldest_files_by_count(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            previous = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = cache_dir
            paths = []
            cache = pathlib.Path(cache_dir) / "omarchy" / "clipboard-link-media-v3"
            cache.mkdir(parents=True)
            for index in range(preview_media.CACHE_MAX_FILES + 2):
                path = cache / f"{index:03}.png"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
                paths.append(path)

            preserved = str(paths[-1])
            try:
                with preview_media.cache_lock():
                    preview_media.prune_cache_locked(pathlib.Path(preserved))
            finally:
                if previous is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = previous

            remaining = sorted(cache.glob("*.png"))
            self.assertEqual(len(remaining), preview_media.CACHE_MAX_FILES)
            self.assertFalse(paths[0].exists())
            self.assertFalse(paths[1].exists())
            self.assertTrue(pathlib.Path(preserved).exists())

    def test_media_cache_prunes_oldest_files_by_size(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            previous_cache = os.environ.get("XDG_CACHE_HOME")
            os.environ["XDG_CACHE_HOME"] = cache_dir
            cache = pathlib.Path(cache_dir) / "omarchy" / "clipboard-link-media-v3"
            cache.mkdir(parents=True)
            first = cache / "first.png"
            second = cache / "second.png"
            first.write_bytes(b"a" * 60)
            second.write_bytes(b"b" * 60)
            os.utime(first, (1, 1))
            os.utime(second, (2, 2))

            previous_limit = preview_media.CACHE_MAX_BYTES
            preview_media.CACHE_MAX_BYTES = 100
            try:
                with preview_media.cache_lock():
                    preview_media.prune_cache_locked()
            finally:
                preview_media.CACHE_MAX_BYTES = previous_limit
                if previous_cache is None:
                    os.environ.pop("XDG_CACHE_HOME", None)
                else:
                    os.environ["XDG_CACHE_HOME"] = previous_cache

            self.assertFalse(first.exists())
            self.assertTrue(second.exists())


class LocalServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/large":
                    body = b"x" * 4096
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except BrokenPipeError:
                        pass
                elif self.path == "/slow":
                    time.sleep(0.6)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    try:
                        self.wfile.write(b"<title>Late</title>")
                    except BrokenPipeError:
                        pass
                elif self.path == "/meta":
                    body = b"<title>Pinned connection</title>"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/image":
                    body = b"\x89PNG\r\n\x1a\nminimal-test-payload"
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif self.path == "/fake-image":
                    body = b"<script>not an image</script>"
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def log_message(self, format, *args):
                pass

        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=1)

    def client(self, **values):
        return preview_network.NetworkClient(policy=preview_network.NetworkPolicy(allow_non_global=True), **values)

    def test_enforces_body_limit_before_accumulating_extra_bytes(self):
        with self.assertRaisesRegex(link_preview.PreviewError, "too large"):
            self.client().fetch(self.url + "/large", accept="text/html", max_body_bytes=128)

    def test_enforces_total_timeout(self):
        with self.assertRaisesRegex(link_preview.PreviewError, "timed out"):
            self.client(timeout=0.2).fetch(self.url + "/slow", accept="text/html", max_body_bytes=1024)

    def test_pins_the_validated_dns_address_for_curl(self):
        def resolver(host, port, family, socktype):
            return [(family, socktype, 6, "", ("127.0.0.1", port))]

        client = self.client(resolver=resolver)
        response, target = client.fetch(
            f"http://preview.test:{self.server.server_port}/meta",
            accept="text/html",
            max_body_bytes=1024,
        )
        metadata = preview_metadata.parse_metadata(response.body, response.headers.get("content-type", ""), target.url)
        self.assertEqual(metadata.title, "Pinned connection")

    def test_accepts_supported_image_with_matching_signature(self):
        response, target = self.client().fetch(
            self.url + "/image", accept="image/png", max_body_bytes=preview_media.MEDIA_MAX_BYTES
        )
        self.assertTrue(response.body.startswith(b"\x89PNG"))
        self.assertEqual(target.url, self.url + "/image")

    def test_rejects_mime_type_spoofing(self):
        with self.assertRaisesRegex(preview_network.PreviewError, "signature is invalid"):
            preview_media.fetch_preview_image(self.url + "/fake-image", self.client())

    def test_blocks_private_image_redirect_before_following_it(self):
        client = preview_network.NetworkClient(resolver=public_resolver)
        calls = []

        def request_once(target, timeout, accept, max_body_bytes):
            calls.append(target.url)
            return preview_network.Response(302, {"location": "http://127.0.0.1/private.png"}, b"")

        client.request_once = request_once
        with self.assertRaises(preview_network.PreviewError):
            client.fetch("https://images.example.test/card.png", accept="image/png", max_body_bytes=1024)
        self.assertEqual(calls, ["https://images.example.test/card.png"])


if __name__ == "__main__":
    unittest.main()
