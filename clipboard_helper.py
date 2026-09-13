#!/usr/bin/python3
"""Security boundary for clipboard capture, watcher lifecycle, and history storage."""
from __future__ import annotations

import hashlib
import ctypes
import json
import os
import selectors
import signal
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

from secure_storage import (
    StorageError, atomic_write_at, open_private_dir, quarantine_entry_at,
    read_file_at, unlink_regular_at,
)

TEXT_MAX_BYTES = 256 * 1024
IMAGE_MAX_BYTES = 20 * 1024 * 1024
CAPTURE_OUTPUT_MAX_BYTES = 512 * 1024
HISTORY_MAX_BYTES = 8 * 1024 * 1024
CONFIG_MAX_BYTES = 64 * 1024
PROTOCOL_INPUT_MAX_BYTES = HISTORY_MAX_BYTES + 64 * 1024
SNAPSHOT_TIMEOUT_SECONDS = 3.0
WATCH_RECORD_TIMEOUT_SECONDS = 3.0
SOURCE_TIMEOUT_SECONDS = 1.0
WATCH_RESTART_MIN_SECONDS = 1.0
RATE_BURST = 20
RATE_PER_SECOND = 2.0

TRUSTED_CANDIDATES = {
    "wl-paste": ("/usr/bin/wl-paste", "/usr/local/bin/wl-paste"),
    "hyprctl": ("/usr/bin/hyprctl", "/usr/local/bin/hyprctl"),
}
PASSTHROUGH_ENV = (
    "HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "WAYLAND_DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "CLIPBOARD_STATE", "LANG", "LC_ALL",
)
STOP = threading.Event()
CHILDREN: set[subprocess.Popen] = set()
CHILDREN_LOCK = threading.Lock()
OUTPUT_LOCK = threading.Lock()
INHERIT_PROCESS_GROUP = False
_SUBREAPER_ENABLED = False


def closed_environment() -> dict[str, str]:
    result = {key: os.environ[key] for key in PASSTHROUGH_ENV if key in os.environ}
    result["PATH"] = "/usr/bin:/bin"
    if os.environ.get("CLIPBOARD_TESTING") == "1":
        result["CLIPBOARD_TESTING"] = "1"
        for key, value in os.environ.items():
            if key.startswith("CLIPBOARD_TEST_") or key == "MOCK_SOURCE_CLASS": result[key] = value
    return result


def _trusted_system_path(candidate: str) -> str | None:
    resolved = os.path.realpath(candidate)
    try:
        current = "/"
        for part in Path(resolved).parts[1:]:
            current = os.path.join(current, part)
            value = os.stat(current, follow_symlinks=False)
            if value.st_uid != 0 or value.st_mode & 0o022: return None
        value = os.stat(resolved, follow_symlinks=False)
        if stat.S_ISREG(value.st_mode) and os.access(resolved, os.X_OK): return resolved
    except OSError:
        pass
    return None


def trusted_executable(name: str) -> str:
    if os.environ.get("CLIPBOARD_TESTING") == "1":
        override = os.environ.get("CLIPBOARD_TEST_" + name.upper().replace("-", "_"))
        if override: return os.path.abspath(override)
    for candidate in TRUSTED_CANDIDATES[name]:
        trusted = _trusted_system_path(candidate)
        if trusted: return trusted
    raise RuntimeError(f"trusted {name} executable is unavailable")


def spawn(command: list[str], **kwargs) -> subprocess.Popen:
    enable_subreaper()
    setpriv = _trusted_system_path("/usr/bin/setpriv")
    if not setpriv: raise RuntimeError("trusted process supervisor is unavailable")
    supervised = [setpriv, "--pdeathsig", "TERM", "--"] + command
    process = subprocess.Popen(supervised, env=closed_environment(), start_new_session=not INHERIT_PROCESS_GROUP, **kwargs)
    process._clipboard_owns_group = not INHERIT_PROCESS_GROUP
    with CHILDREN_LOCK:
        CHILDREN.add(process)
    return process


def enable_subreaper() -> None:
    global _SUBREAPER_ENABLED
    if _SUBREAPER_ENABLED or INHERIT_PROCESS_GROUP: return
    try:
        if ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0: return  # PR_SET_CHILD_SUBREAPER
        _SUBREAPER_ENABLED = True
    except Exception:
        pass


def reap_group(pgid: int, grace: float = 0.5) -> None:
    if not _SUBREAPER_ENABLED: return
    deadline = time.monotonic() + grace
    while True:
        reaped = False
        try:
            while True:
                pid, _ = os.waitpid(-pgid, os.WNOHANG)
                if pid == 0: break
                reaped = True
        except ChildProcessError:
            return
        if time.monotonic() >= deadline: return
        if not reaped: time.sleep(0.01)


def forget(process: subprocess.Popen) -> None:
    with CHILDREN_LOCK:
        CHILDREN.discard(process)


def _signal_process(process: subprocess.Popen, signum: int) -> None:
    try:
        if getattr(process, "_clipboard_owns_group", False): os.killpg(process.pid, signum)
        else: process.send_signal(signum)
    except ProcessLookupError:
        pass


def terminate_tree(process: subprocess.Popen, grace: float = 0.5) -> None:
    # A group may still contain grandchildren after its leader exits. Signal
    # the group unconditionally, escalate, then reap the direct child.
    _signal_process(process, signal.SIGTERM)
    try: process.wait(timeout=grace)
    except subprocess.TimeoutExpired: pass
    if getattr(process, "_clipboard_owns_group", False):
        try: os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError: pass
    elif process.poll() is None:
        _signal_process(process, signal.SIGKILL)
    try: process.wait(timeout=grace)
    except subprocess.TimeoutExpired: pass
    if getattr(process, "_clipboard_owns_group", False): reap_group(process.pid, grace)
    forget(process)


def stop_all(signum=None, frame=None) -> None:
    STOP.set()
    with CHILDREN_LOCK:
        processes = list(CHILDREN)
    for process in processes: _signal_process(process, signal.SIGTERM)
    deadline = time.monotonic() + 0.5
    for process in processes:
        try: process.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired: pass
    # Escalate every owned group even if its leader already exited: a
    # TERM-ignoring descendant can otherwise outlive the broker.
    for process in processes:
        if getattr(process, "_clipboard_owns_group", False):
            try: os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError: pass
        elif process.poll() is None: _signal_process(process, signal.SIGKILL)
    for process in processes:
        try: process.wait(timeout=0.5)
        except subprocess.TimeoutExpired: pass
        if getattr(process, "_clipboard_owns_group", False): reap_group(process.pid, 0.5)
        forget(process)


def bounded_communicate(command: list[str], *, input_data: bytes | None, max_bytes: int, timeout: float,
                        cleanup_group: bool = False) -> bytes:
    process = spawn(command, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert process.stdout is not None
    if input_data is not None:
        assert process.stdin is not None
        process.stdin.write(input_data); process.stdin.close()
    deadline = time.monotonic() + timeout
    output = bytearray()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("helper timed out")
            ready, _, _ = __import__("select").select([process.stdout], [], [], remaining)
            if not ready:
                raise RuntimeError("helper timed out")
            chunk = os.read(process.stdout.fileno(), min(65536, max_bytes - len(output) + 1))
            if not chunk:
                break
            if len(output) + len(chunk) > max_bytes:
                raise RuntimeError("helper output too large")
            output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("helper timed out")
        process.wait(timeout=remaining)
        return bytes(output)
    except Exception:
        terminate_tree(process)
        raise
    finally:
        try: process.stdout.close()
        except Exception: pass
        if cleanup_group: terminate_tree(process)
        else: forget(process)


def read_bounded_stream(stream, max_bytes: int, timeout: float) -> bytes:
    selector = selectors.DefaultSelector()
    selector.register(stream, selectors.EVENT_READ)
    data = bytearray()
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not selector.select(remaining):
            raise RuntimeError("clipboard transfer timed out")
        chunk = os.read(stream.fileno(), min(65536, max_bytes - len(data) + 1))
        if not chunk:
            return bytes(data)
        if len(data) + len(chunk) > max_bytes:
            raise RuntimeError("clipboard transfer too large")
        data.extend(chunk)


def remaining(deadline: float) -> float:
    value = deadline - time.monotonic()
    if value <= 0: raise RuntimeError("clipboard processing timed out")
    return value


def clipboard_is_sensitive(deadline: float) -> bool:
    if os.environ.get("CLIPBOARD_STATE") == "sensitive": return True
    raw = bounded_communicate(
        [trusted_executable("wl-paste"), "--list-types"], input_data=None,
        max_bytes=64 * 1024, timeout=remaining(deadline),
    )
    return "x-kde-passwordManagerHint" in raw.decode(errors="replace").splitlines()


def source_metadata(deadline: float | None = None) -> dict[str, str]:
    try:
        timeout = SOURCE_TIMEOUT_SECONDS if deadline is None else min(SOURCE_TIMEOUT_SECONDS, remaining(deadline))
        raw = bounded_communicate([trusted_executable("hyprctl"), "activewindow", "-j"], input_data=None,
                                  max_bytes=64 * 1024, timeout=timeout)
        window = json.loads(raw or b"{}")
    except Exception:
        window = {}
    source_id = str(window.get("class") or window.get("initialClass") or "")[:512]
    title = str(window.get("title") or window.get("initialTitle") or "")[:4096]
    normalized = source_id.lower()
    if normalized.startswith("brave-browser"):
        app, icon = "Brave Browser", "brave-browser"
    elif normalized.startswith("google-chrome"):
        app, icon = "Google Chrome", "google-chrome"
    elif normalized.startswith("chromium"):
        app, icon = "Chromium", "chromium"
    elif normalized.startswith("firefox") or normalized.startswith("org.mozilla.firefox"):
        app, icon = "Firefox", "firefox"
    elif normalized in ("code", "code-oss"):
        app, icon = "Visual Studio Code", "visual-studio-code"
    elif normalized in ("codium", "vscodium"):
        app, icon = "VSCodium", "vscodium"
    elif normalized in ("org.gnome.nautilus", "nautilus"):
        app, icon = "Files", source_id
    elif normalized in ("org.kde.dolphin", "dolphin"):
        app, icon = "Dolphin", source_id
    else:
        app = " ".join(part.capitalize() for part in normalized.replace(".", "-").replace("_", "-").split("-") if part)
        icon = source_id
    return {"source_id": source_id, "sourceApp": app, "sourceIcon": icon, "sourceTitle": title}


def read_bounded_config(path: Path) -> dict:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        value = os.fstat(fd)
        if (not stat.S_ISREG(value.st_mode) or value.st_uid not in (0, os.getuid())
                or value.st_nlink != 1 or value.st_size > CONFIG_MAX_BYTES):
            raise RuntimeError("unsafe clipboard configuration")
        raw = bytearray()
        while len(raw) <= CONFIG_MAX_BYTES:
            chunk = os.read(fd, min(65536, CONFIG_MAX_BYTES + 1 - len(raw)))
            if not chunk: break
            raw.extend(chunk)
        if len(raw) > CONFIG_MAX_BYTES: raise RuntimeError("clipboard configuration too large")
        return json.loads(raw)
    finally:
        os.close(fd)


def excluded(metadata: dict[str, str], script_dir: Path) -> bool:
    try:
        value = read_bounded_config(script_dir / "clipboard.json")
        candidates = value.get("excludedApplications", []) if isinstance(value, dict) else []
    except Exception:
        return False
    seen: list[str] = []
    for candidate in candidates if isinstance(candidates, list) else []:
        if isinstance(candidate, str) and candidate.strip().lower() not in seen and len(seen) < 100:
            seen.append(candidate.strip().lower())
    return metadata["source_id"].lower() in seen or metadata["sourceApp"].lower() in seen


def state_base() -> str:
    return os.environ.get("XDG_STATE_HOME") or os.path.join(os.environ.get("HOME", ""), ".local", "state")


def store_image(mime: str, body: bytes) -> str:
    if os.environ.get("CLIPBOARD_TESTING") == "1" and os.environ.get("CLIPBOARD_TEST_STORE_DELAY"):
        time.sleep(float(os.environ["CLIPBOARD_TEST_STORE_DELAY"]))
    image_fd, image_path = open_private_dir(state_base(), "clipboard-images")
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp", "image/tiff": "tiff"}.get(mime)
    if not extension:
        os.close(image_fd); raise RuntimeError("unsupported image type")
    name = hashlib.sha256(body).hexdigest() + "." + extension
    try:
        try:
            existing = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=image_fd)
        except FileNotFoundError:
            existing = -1
        if existing >= 0:
            try:
                info = os.fstat(existing)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise StorageError("unsafe image target")
            finally: os.close(existing)
        else:
            atomic_write_at(image_fd, name, body, IMAGE_MAX_BYTES)
    finally:
        os.close(image_fd)
    return os.path.join(image_path, name)


def decode_text(body: bytes) -> str:
    if body.startswith((b"\xff\xfe", b"\xfe\xff")): return body.decode("utf-16", errors="replace")
    if len(body) % 2 == 0 and b"\0" in body:
        units = len(body) // 2
        even_nuls = body[0::2].count(0); odd_nuls = body[1::2].count(0)
        if odd_nuls * 4 >= units * 3 and even_nuls * 4 < units:
            candidate = body.decode("utf-16le", errors="replace")
            if not any(ord(char) < 32 and char not in "\t\n\r" for char in candidate): return candidate
        if even_nuls * 4 >= units * 3 and odd_nuls * 4 < units:
            candidate = body.decode("utf-16be", errors="replace")
            if not any(ord(char) < 32 and char not in "\t\n\r" for char in candidate): return candidate
    return body.decode("utf-8", errors="replace")


def capture_payload(mime: str, body: bytes, deadline: float | None = None) -> dict | None:
    if os.environ.get("CLIPBOARD_STATE") == "sensitive": return None
    if deadline is not None: remaining(deadline)
    metadata = source_metadata(deadline)
    if excluded(metadata, Path(__file__).resolve().parent):
        return None
    entry: dict[str, str]
    if mime == "text":
        if not body: return None
        text = decode_text(body)
        if not text.strip(): return None
        entry = {"type": "text", "text": text}
    else:
        if not body: return None
        if len(body) > IMAGE_MAX_BYTES: raise RuntimeError("clipboard image too large")
        entry = {"type": "image", "mime": mime, "path": store_image(mime, body)}
    if deadline is not None: remaining(deadline)
    entry["capturedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if metadata["sourceApp"] or metadata["sourceTitle"]:
        entry.update({key: metadata[key] for key in ("sourceApp", "sourceIcon", "sourceTitle")})
    encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > CAPTURE_OUTPUT_MAX_BYTES:
        raise RuntimeError("capture record too large")
    return entry


def emit(value: dict) -> None:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    if len(encoded) > PROTOCOL_INPUT_MAX_BYTES:
        return
    with OUTPUT_LOCK:
        sys.stdout.buffer.write(encoded + b"\n")
        sys.stdout.buffer.flush()


def capture_mode(mime: str) -> int:
    maximum = TEXT_MAX_BYTES if mime == "text" else IMAGE_MAX_BYTES
    deadline = time.monotonic() + WATCH_RECORD_TIMEOUT_SECONDS
    previous_handler = signal.getsignal(signal.SIGALRM)
    def deadline_expired(signum, frame) -> None:
        raise RuntimeError("clipboard processing timed out")
    signal.signal(signal.SIGALRM, deadline_expired)
    signal.setitimer(signal.ITIMER_REAL, WATCH_RECORD_TIMEOUT_SECONDS)
    try:
        # Password managers advertise sensitivity as a companion MIME type;
        # the payload-only watch callback cannot otherwise observe it.
        if clipboard_is_sensitive(deadline): return 0
        body = read_bounded_stream(sys.stdin.buffer, maximum, remaining(deadline))
        entry = capture_payload(mime, body, deadline)
        remaining(deadline)
        if entry: emit(entry)
    except Exception:
        return 0
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
    return 0


def snapshot_worker(wrapped: bool) -> None:
    wl_paste = trusted_executable("wl-paste")
    try:
        deadline = time.monotonic() + SNAPSHOT_TIMEOUT_SECONDS
        raw_types = bounded_communicate([wl_paste, "--list-types"], input_data=None, max_bytes=64 * 1024, timeout=remaining(deadline))
        types = raw_types.decode(errors="replace").splitlines()
        if os.environ.get("CLIPBOARD_STATE") == "sensitive" or "x-kde-passwordManagerHint" in types: return
        mime = next((item for item in ("image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff") if item in types), "text")
        maximum = IMAGE_MAX_BYTES if mime != "text" else TEXT_MAX_BYTES
        body = bounded_communicate([wl_paste, "--type", mime, "--no-newline"] if mime == "text" else [wl_paste, "--type", mime],
                                   input_data=None, max_bytes=maximum, timeout=remaining(deadline))
        entry = capture_payload(mime, body, deadline)
        remaining(deadline)
        if entry: emit({"event": "capture", "entry": entry} if wrapped else entry)
    except Exception as error:
        emit({"event": "warning", "message": str(error)[:200]})


def snapshot(wrapped: bool = True) -> None:
    # Supervise all metadata, decoding and persistence in one process group.
    # This makes the advertised deadline effective even if a filesystem call
    # blocks, while bounding bytes before they reach QML.
    command = ["/usr/bin/python3", str(Path(__file__).resolve()), "snapshot-worker", "wrapped" if wrapped else "plain"]
    try:
        raw = bounded_communicate(command, input_data=None, max_bytes=CAPTURE_OUTPUT_MAX_BYTES,
                                  timeout=SNAPSHOT_TIMEOUT_SECONDS, cleanup_group=True)
        if raw:
            for line in raw.splitlines():
                value = json.loads(line)
                if isinstance(value, dict): emit(value)
    except Exception as error:
        emit({"event": "warning", "message": str(error)[:200]})


class RateLimiter:
    def __init__(self): self.tokens, self.updated = float(RATE_BURST), time.monotonic()
    def accept(self) -> bool:
        now = time.monotonic(); self.tokens = min(RATE_BURST, self.tokens + (now - self.updated) * RATE_PER_SECOND); self.updated = now
        if self.tokens < 1: return False
        self.tokens -= 1; return True


def pump_watch(mime: str) -> None:
    limiter = RateLimiter()
    helper = str(Path(__file__).resolve())
    while not STOP.is_set():
        started = time.monotonic()
        try:
            command = [trusted_executable("wl-paste"), "--type", mime, "--watch", "/usr/bin/python3", helper, "capture", mime]
            process = spawn(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            assert process.stdout
            buffer = bytearray(); record_deadline = None
            while not STOP.is_set() and process.poll() is None:
                ready, _, _ = __import__("select").select([process.stdout], [], [], 0.25)
                if not ready:
                    if record_deadline and time.monotonic() > record_deadline: raise RuntimeError("watch record stalled")
                    continue
                chunk = os.read(process.stdout.fileno(), min(65536, CAPTURE_OUTPUT_MAX_BYTES - len(buffer) + 1))
                if not chunk: break
                if record_deadline is None: record_deadline = time.monotonic() + WATCH_RECORD_TIMEOUT_SECONDS
                buffer.extend(chunk)
                if len(buffer) > CAPTURE_OUTPUT_MAX_BYTES: raise RuntimeError("watch record too large")
                while b"\n" in buffer:
                    line, _, rest = buffer.partition(b"\n"); buffer = bytearray(rest); record_deadline = time.monotonic() + WATCH_RECORD_TIMEOUT_SECONDS if buffer else None
                    try: entry = json.loads(line)
                    except Exception: continue
                    if limiter.accept(): emit({"event": "capture", "entry": entry})
        except Exception as error:
            emit({"event": "warning", "message": str(error)[:200]})
        finally:
            if 'process' in locals(): terminate_tree(process)
        STOP.wait(max(0.0, WATCH_RESTART_MIN_SECONDS - (time.monotonic() - started)))


def history_fd() -> int:
    fd, _ = open_private_dir(state_base())
    return fd


def load_history() -> list:
    fd = history_fd()
    try: raw = read_file_at(fd, "clipboard-history.json", HISTORY_MAX_BYTES, b"[]")
    finally: os.close(fd)
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise StorageError("history is not valid JSON") from error
    if not isinstance(value, list):
        raise StorageError("history must be an array")
    return value


def initial_history_event() -> dict:
    """Always produce a readiness event, quarantining a bad history entry when possible."""
    try:
        return {"event": "ready", "history": load_history(), "degraded": False}
    except Exception as error:
        quarantine = None
        try:
            fd = history_fd()
            try: quarantine = quarantine_entry_at(fd, "clipboard-history.json")
            finally: os.close(fd)
        except Exception:
            pass
        event = {
            "event": "ready", "history": [], "degraded": True,
            "message": str(error)[:200],
        }
        if quarantine: event["quarantine"] = quarantine
        return event


def save_history(value) -> None:
    if not isinstance(value, list): raise StorageError("history must be an array")
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    fd = history_fd()
    try: atomic_write_at(fd, "clipboard-history.json", raw, HISTORY_MAX_BYTES)
    finally: os.close(fd)


def handle_command(line: bytes) -> None:
    try:
        command = json.loads(line)
        operation = command.get("op") if isinstance(command, dict) else None
        if operation == "save":
            save_history(command.get("history"))
            emit({"event": "saved", "requestId": command.get("requestId")})
        elif operation == "delete-images":
            image_fd, _ = open_private_dir(state_base(), "clipboard-images")
            try:
                for name in command.get("names", [])[:500]:
                    if isinstance(name, str) and __import__("re").fullmatch(r"[0-9a-f]{64}\.(png|jpg|webp|gif|bmp|tiff)", name): unlink_regular_at(image_fd, name)
            finally: os.close(image_fd)
            emit({"event": "deleted"})
    except Exception as error:
        emit({"event": "error", "message": str(error)[:200]})


def broker() -> int:
    signal.signal(signal.SIGTERM, stop_all); signal.signal(signal.SIGINT, stop_all)
    emit(initial_history_event())
    threading.Thread(target=snapshot, daemon=True).start()
    for mime in ("text", "image/png"):
        threading.Thread(target=pump_watch, args=(mime,), daemon=True).start()
    protocol_buffer = bytearray()
    while not STOP.is_set():
        ready, _, _ = __import__("select").select([sys.stdin.buffer], [], [], 0.25)
        if not ready: continue
        chunk = os.read(sys.stdin.buffer.fileno(), min(65536, PROTOCOL_INPUT_MAX_BYTES - len(protocol_buffer) + 1))
        if not chunk: break
        protocol_buffer.extend(chunk)
        if len(protocol_buffer) > PROTOCOL_INPUT_MAX_BYTES:
            emit({"event": "error", "message": "protocol request too large"}); break
        while b"\n" in protocol_buffer:
            line, _, rest = protocol_buffer.partition(b"\n"); protocol_buffer = bytearray(rest)
            handle_command(line)
    stop_all()
    time.sleep(0.05)
    return 0


def main(argv: list[str]) -> int:
    global INHERIT_PROCESS_GROUP
    if len(argv) >= 3 and argv[1] == "capture" and argv[2] in ("text", "image/png", "image/jpeg", "image/webp", "image/gif", "image/bmp", "image/tiff"):
        INHERIT_PROCESS_GROUP = True
        return capture_mode(argv[2])
    if len(argv) == 3 and argv[1] == "snapshot-worker" and argv[2] in ("wrapped", "plain"):
        INHERIT_PROCESS_GROUP = True
        snapshot_worker(argv[2] == "wrapped"); return 0
    if len(argv) == 2 and argv[1] == "snapshot": snapshot(False); return 0
    if len(argv) == 2 and argv[1] == "broker": return broker()
    return 2

if __name__ == "__main__": raise SystemExit(main(sys.argv))
