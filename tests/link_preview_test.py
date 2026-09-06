#!/usr/bin/env python3

import importlib.util
import pathlib
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "link_preview.py"
SPEC = importlib.util.spec_from_file_location("link_preview", MODULE_PATH)
link_preview = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = link_preview
SPEC.loader.exec_module(link_preview)


def public_resolver(host, port, family, socktype):
    return [(family, socktype, 6, "", ("93.184.216.34", port))]


class PreviewTests(unittest.TestCase):
    def test_accepts_public_ipv4_and_ipv6_dns_answers(self):
        def resolver(host, port, family, socktype):
            return [
                (family, socktype, 6, "", ("93.184.216.34", port)),
                (family, socktype, 6, "", ("2001:4860:4860::8888", port, 0, 0)),
            ]

        target = link_preview.PreviewClient(resolver=resolver).validate_url("https://example.test/page")
        self.assertEqual(target.address, "93.184.216.34")
        self.assertEqual(link_preview.PreviewClient().validate_url("https://8.8.8.8/").address, "8.8.8.8")
        self.assertEqual(
            link_preview.PreviewClient().validate_url("https://[2001:4860:4860::8888]/").address,
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
        client = link_preview.PreviewClient()
        for address in blocked:
            with self.subTest(address=address), self.assertRaises(link_preview.PreviewError):
                client.validate_url(f"http://[{address}]/" if ":" in address else f"http://{address}/")

    def test_rejects_mixed_public_and_private_dns_answers(self):
        def resolver(host, port, family, socktype):
            return [
                (family, socktype, 6, "", ("93.184.216.34", port)),
                (family, socktype, 6, "", ("127.0.0.1", port)),
            ]

        with self.assertRaises(link_preview.PreviewError):
            link_preview.PreviewClient(resolver=resolver).validate_url("https://example.test/")

    def test_blocks_local_redirect_before_second_request(self):
        client = link_preview.PreviewClient(resolver=public_resolver)
        calls = []

        def request_once(target, timeout):
            calls.append(target.url)
            return link_preview.Response(302, {"location": "http://127.0.0.1/admin"}, b"")

        client.request_once = request_once
        with self.assertRaises(link_preview.PreviewError):
            client.fetch("https://public.example/start")
        self.assertEqual(calls, ["https://public.example/start"])

    def test_parses_plain_metadata_without_returning_remote_markup(self):
        metadata = link_preview.parse_metadata(
            b'<title><script>alert(1)</script>Safe &amp; plain</title>'
            b'<meta property="og:description" content="A useful description">',
            "text/html; charset=utf-8",
            "https://example.test/",
        )
        self.assertEqual(metadata, {"title": "Safe & plain", "description": "A useful description"})


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
        return link_preview.PreviewClient(policy=link_preview.NetworkPolicy(allow_non_global=True), **values)

    def test_enforces_body_limit_before_accumulating_extra_bytes(self):
        with self.assertRaisesRegex(link_preview.PreviewError, "too large"):
            self.client(max_body_bytes=128).fetch(self.url + "/large")

    def test_enforces_total_timeout(self):
        with self.assertRaisesRegex(link_preview.PreviewError, "timed out"):
            self.client(timeout=0.2).fetch(self.url + "/slow")

    def test_pins_the_validated_dns_address_for_curl(self):
        def resolver(host, port, family, socktype):
            return [(family, socktype, 6, "", ("127.0.0.1", port))]

        client = self.client(resolver=resolver)
        metadata = client.fetch(f"http://preview.test:{self.server.server_port}/meta")
        self.assertEqual(metadata["title"], "Pinned connection")


if __name__ == "__main__":
    unittest.main()
