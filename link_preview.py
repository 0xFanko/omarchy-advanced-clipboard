#!/usr/bin/env python3
"""Build an inert, bounded link-preview payload for the QML clipboard UI."""

from __future__ import annotations

import json
import os
import signal
import sys

import preview_media
import preview_network
from preview_media import fetch_preview_image
from preview_metadata import PreviewMetadata, parse_metadata
from preview_network import NetworkClient, PreviewError

HTML_MAX_BYTES = 2 * 1024 * 1024
HELPER_TIMEOUT_SECONDS = 14.0
HELPER_OUTPUT_MAX_BYTES = 8 * 1024


def fetch_metadata(url: str) -> PreviewMetadata:
    response, target = NetworkClient().fetch(
        url,
        accept="text/html, application/xhtml+xml",
        max_body_bytes=HTML_MAX_BYTES,
    )
    content_type = response.headers.get("content-type", "").lower()
    if content_type and "text/html" not in content_type and "application/xhtml+xml" not in content_type:
        raise PreviewError("The address does not return an HTML page.")
    return parse_metadata(response.body, content_type, target.url)


def preview_payload(url: str) -> dict[str, str]:
    metadata = fetch_metadata(url)
    image = ""
    if metadata.image_url:
        try:
            image = fetch_preview_image(metadata.image_url)
        except PreviewError as error:
            print(f"Link preview image skipped: {error}", file=sys.stderr)
    return {
        "title": metadata.title,
        "description": metadata.description,
        "image": image,
        "site": metadata.site,
    }


def result(serial: int, state: str, **values: str) -> str:
    payload = json.dumps({"serial": serial, "state": state, **values}, ensure_ascii=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) + 1 > HELPER_OUTPUT_MAX_BYTES:
        payload = json.dumps({"serial": serial, "state": "error", "error": "The preview result was too large."}, separators=(",", ":"))
    return payload


def emit_result(payload: str) -> None:
    encoded = payload.encode("utf-8")
    if len(encoded) + 1 > HELPER_OUTPUT_MAX_BYTES: raise RuntimeError("preview output boundary failed")
    os.write(sys.stdout.fileno(), encoded + b"\n")


def terminate_children() -> None:
    preview_network.terminate_all_children()
    preview_media.terminate_all_children()


def main(argv: list[str]) -> int:
    serial = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 0
    if len(argv) < 2:
        emit_result(result(serial, "error", error="A page URL is required."))
        return 0

    def deadline_expired(signum, frame) -> None:
        terminate_children()
        raise PreviewError("The preview request timed out.")

    previous_handler = signal.signal(signal.SIGALRM, deadline_expired)
    previous_term_handler = signal.signal(signal.SIGTERM, deadline_expired)
    previous_int_handler = signal.signal(signal.SIGINT, deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, HELPER_TIMEOUT_SECONDS)
    try:
        preview = preview_payload(argv[1])
        state = "ready" if any(preview.values()) else "empty"
        emit_result(result(serial, state, **preview))
    except PreviewError as error:
        emit_result(result(serial, "error", error=str(error)[:1000]))
    except Exception:
        emit_result(result(serial, "error", error="The page preview failed unexpectedly."))
    finally:
        terminate_children()
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        signal.signal(signal.SIGTERM, previous_term_handler)
        signal.signal(signal.SIGINT, previous_int_handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
