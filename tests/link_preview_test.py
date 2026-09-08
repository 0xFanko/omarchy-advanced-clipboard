#!/usr/bin/env python3

import base64
import importlib.util
import os
import pathlib
import struct
import sys
import tempfile
import threading
import time
import unittest
import zlib
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


def png_fixture(width=1, height=1):
    def chunk(kind, data):
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = (b"\0" + b"\0\0\0" * width) * height
    return (
        preview_media.PNG_SIGNATURE
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(pixels))
        + chunk(b"IEND", b"")
    )


class PreviewTests(unittest.TestCase):
    def test_preview_keeps_metadata_when_image_is_rejected(self):
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
            payload = link_preview.preview_payload("https://example.test/")

        self.assertEqual(payload["title"], "Safe title")
        self.assertEqual(payload["description"], "Safe description")
        self.assertEqual(payload["image"], "")

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
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": cache_dir}
        ):
            first = str(preview_media.cache_path("https://example.test/card"))
            second = str(preview_media.cache_path("https://example.test/card"))

        self.assertEqual(first, second)
        self.assertTrue(first.endswith(".png"))
        self.assertNotIn("example.test", pathlib.Path(first).name)

    def test_cached_media_path_reuses_existing_image(self):
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": cache_dir}
        ):
            expected = preview_media.cache_path("https://example.test/card")
            expected.write_bytes(png_fixture())
            actual = preview_media.cached_image("https://example.test/card")

        self.assertEqual(actual, str(expected))

    def test_cached_media_path_expires_after_ttl(self):
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": cache_dir}
        ):
            expected = preview_media.cache_path("https://example.test/old-card")
            expected.write_bytes(png_fixture())
            old = 1_000_000
            os.utime(expected, (old, old))
            actual = preview_media.cached_image(
                "https://example.test/old-card",
                now=old + preview_media.CACHE_TTL_SECONDS + 1,
            )
            self.assertEqual(actual, "")
            self.assertFalse(expected.exists())

    def test_cache_hits_do_not_extend_absolute_ttl(self):
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": cache_dir}
        ):
            url = "https://example.test/frequently-read-card"
            path = preview_media.cache_path(url)
            path.write_bytes(png_fixture())
            created = 1_000_000
            os.utime(path, (created, created))
            self.assertEqual(preview_media.cached_image(url, now=created + 10), str(path))
            self.assertEqual(
                preview_media.cached_image(url, now=created + preview_media.CACHE_TTL_SECONDS + 1),
                "",
            )

    def test_media_cache_prunes_oldest_files_by_count(self):
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": cache_dir}
        ):
            paths = []
            cache = pathlib.Path(cache_dir) / "omarchy" / "clipboard-link-media-v4"
            cache.mkdir(parents=True)
            for index in range(preview_media.CACHE_MAX_FILES + 2):
                path = cache / f"{index:03}.png"
                path.write_bytes(b"x")
                os.utime(path, (index + 1, index + 1))
                paths.append(path)

            preserved = str(paths[-1])
            preview_media.prune_cache(pathlib.Path(preserved))

            remaining = sorted(cache.glob("*.png"))
            self.assertEqual(len(remaining), preview_media.CACHE_MAX_FILES)
            self.assertFalse(paths[0].exists())
            self.assertFalse(paths[1].exists())
            self.assertTrue(pathlib.Path(preserved).exists())

    def test_media_cache_prunes_oldest_files_by_size(self):
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ, {"XDG_CACHE_HOME": cache_dir}
        ):
            cache = pathlib.Path(cache_dir) / "omarchy" / "clipboard-link-media-v4"
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
                preview_media.prune_cache()
            finally:
                preview_media.CACHE_MAX_BYTES = previous_limit

            self.assertFalse(first.exists())
            self.assertTrue(second.exists())

    def test_normalizes_a_valid_png(self):
        normalized = preview_media.normalize_image(png_fixture())
        self.assertTrue(normalized.startswith(preview_media.PNG_SIGNATURE))
        self.assertLessEqual(len(normalized), preview_media.MEDIA_MAX_BYTES)

    def test_rejects_a_signature_only_png(self):
        with self.assertRaisesRegex(preview_network.PreviewError, "normalized safely"):
            preview_media.normalize_image(preview_media.PNG_SIGNATURE + b"not-an-image")

    def test_rejects_excessive_image_dimensions(self):
        with self.assertRaisesRegex(preview_network.PreviewError, "normalized safely"):
            preview_media.normalize_image(png_fixture(width=8193))

    def test_translates_cache_errors_to_preview_errors(self):
        with mock.patch.object(preview_media, "cache_directory", side_effect=PermissionError("denied")):
            with self.assertRaisesRegex(preview_network.PreviewError, "cache is unavailable"):
                preview_media.fetch_preview_image("https://example.test/card.png")


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
                elif self.path == "/large-metadata":
                    body = b'<meta property="og:title" content="Large page">' + b"x" * (900 * 1024)
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
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
                    body = png_fixture()
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

    def test_accepts_large_html_within_metadata_limit(self):
        response, target = self.client().fetch(
            self.url + "/large-metadata",
            accept="text/html",
            max_body_bytes=link_preview.HTML_MAX_BYTES,
        )
        metadata = preview_metadata.parse_metadata(response.body, "text/html", target.url)
        self.assertEqual(metadata.title, "Large page")

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
        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ,
            {"XDG_CACHE_HOME": cache_dir},
        ):
            path = pathlib.Path(preview_media.fetch_preview_image(self.url + "/image", self.client()))
            self.assertEqual(path.suffix, ".png")
            self.assertTrue(path.read_bytes().startswith(b"\x89PNG"))

    def test_normalizes_webp_images_to_png(self):
        class WebpClient:
            def fetch(self, *args, **kwargs):
                return preview_network.Response(
                    200,
                    {"content-type": "image/webp"},
                    base64.b64decode("UklGRiQAAABXRUJQVlA4IBgAAAAwAQCdASoBAAEAAgA0JaQAA3AA/vv9UAA="),
                ), None

        with tempfile.TemporaryDirectory() as cache_dir, mock.patch.dict(
            os.environ,
            {"XDG_CACHE_HOME": cache_dir},
        ):
            path = pathlib.Path(preview_media.fetch_preview_image("https://example.test/card.webp", WebpClient()))
            self.assertEqual(path.suffix, ".png")
            self.assertTrue(path.read_bytes().startswith(preview_media.PNG_SIGNATURE))

    def test_rejects_mime_type_spoofing(self):
        with self.assertRaisesRegex(preview_network.PreviewError, "normalized safely"):
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
