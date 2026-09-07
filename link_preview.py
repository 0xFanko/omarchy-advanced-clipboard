#!/usr/bin/env python3
"""Build an inert, bounded link-preview payload for the QML clipboard UI."""

from __future__ import annotations

import json
import signal
import sys

from preview_media import fetch_preview_image
from preview_metadata import PreviewMetadata, parse_metadata
from preview_network import NetworkClient, PreviewError

HTML_MAX_BYTES = 512 * 1024
HELPER_TIMEOUT_SECONDS = 14.0


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


def preview_payloads(url: str):
    metadata = fetch_metadata(url)
    yield {
        "phase": "metadata",
        "title": metadata.title,
        "description": metadata.description,
        "site": metadata.site,
    }

    image = ""
    if metadata.image_url:
        try:
            image = fetch_preview_image(metadata.image_url)
        except PreviewError:
            pass
    yield {"phase": "complete", "image": image}


def result(serial: int, state: str, **values: str) -> str:
    return json.dumps({"serial": serial, "state": state, **values}, ensure_ascii=False, separators=(",", ":"))


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
        for preview in preview_payloads(argv[1]):
            state = "ready" if preview.get("phase") == "metadata" else "complete"
            print(result(serial, state, **preview), flush=True)
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
