#!/usr/bin/env python3
"""Serve show-and-grill explanation pages on 127.0.0.1 only.

Like ``python3 -m http.server --bind 127.0.0.1``, it only serves the files in one folder.
It adds five things and uses only the standard library:

- Port: reuse the last port if it is free, else pick a free one (so open tabs come back on reload).
- State: write the port, pid, and a new stop token to ``<dir>/.serve.json`` when it starts.
  Only the starting process writes it, once. A stopping process does not touch it, so the
  record stays after a stop (the next start reuses the port). ``--status`` prints the URL.
- Take over: when a server for the same folder already runs (for example, one that another
  session started), ask it to stop, then serve on the same port. The server belongs to the
  session that uses it, so a resumed session keeps working after the first session ends.
  If the old server does not stop (an old version), serve on a free port instead.
- Stop: stop after a quiet period with no requests (default 120 minutes), or on
  ``POST /`` with the header ``X-Grill-Stop: <token from .serve.json>``.
- Guards: reject a Host header other than 127.0.0.1 / localhost on our port (DNS rebinding).
  Do not serve or list dot files. Do not serve a path whose real location is outside the folder
  (for example, a symbolic link to a file elsewhere). A server counts as running only if it
  answers with this folder's id (another folder's server may have taken the saved port).

Usage:
    python3 serve.py <dir>                  # take over or start, then serve in the foreground
    python3 serve.py <dir> --wait-log <log> # print the URL from the SERVING line in <log>
    python3 serve.py <dir> --status         # print the URL and exit 0 if running, else exit 1
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import hmac
import html
import http.server
import io
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
import time
from typing import TYPE_CHECKING, Any
import urllib.parse
import urllib.request

if TYPE_CHECKING:
    from collections.abc import Callable

STATE_FILE = ".serve.json"
HOST = "127.0.0.1"
STOP_HEADER = "X-Grill-Stop"
SERVING_RE = re.compile(r"^SERVING (http://127\.0\.0\.1:(\d+)/)", re.MULTILINE)


def _is_hidden_path(raw_path: str) -> bool:
    """Return whether the request path points at a dot file or dot folder.

    ``SimpleHTTPRequestHandler`` decodes ``%xx`` before it opens the file, so we check the
    decoded path (``/%2eserve.json`` counts as ``/.serve.json``).

    Args:
        raw_path: The path in the request line (it may include a query).

    Returns:
        True if any part of the path starts with a dot.
    """
    # Do not use urlsplit: it reads "//name" as a host and drops it from the path.
    path = urllib.parse.unquote(raw_path.split("?", 1)[0].split("#", 1)[0])
    parts = path.replace("\\", "/").split("/")
    return any(part.startswith(".") for part in parts if part)


def _host_allowed(host: str, port: int) -> bool:
    """Return whether the Host header names this server.

    Args:
        host: The Host header value.
        port: The port the server listens on.

    Returns:
        True only for ``127.0.0.1:<port>`` or ``localhost:<port>`` (any letter case).
    """
    return host.strip().lower() in {f"127.0.0.1:{port}", f"localhost:{port}"}


def _stop_allowed(raw_path: str, given: str | None, token: str | None) -> bool:
    """Return whether a POST is a valid stop request.

    Args:
        raw_path: The path in the request line (it may include a query).
        given: The ``X-Grill-Stop`` header value, or None if missing.
        token: This server's stop token, or None if it has none.

    Returns:
        True only for the path ``/`` and a header equal to the token (ASCII only).
    """
    if raw_path.split("?", 1)[0] != "/":
        return False
    if not token or not given or not given.isascii():
        return False
    return hmac.compare_digest(given.encode("ascii"), token.encode("ascii"))


def _inside(root: Path, fs_path: str) -> bool:
    """Return whether the real location of ``fs_path`` is inside ``root``.

    Args:
        root: The served folder (already resolved).
        fs_path: The file system path the handler would open.

    Returns:
        True if the path, after following symbolic links, is ``root`` or under it.
    """
    real = Path(fs_path).resolve()
    return real == root or root in real.parents


def _folder_id(root: Path) -> str:
    """Return a short id for the served folder (sent in the ``X-Grill-Serve`` header).

    Args:
        root: The served folder.

    Returns:
        16 hex characters made from the folder's full path.
    """
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]


def _reuse_address(platform: str) -> bool:
    """Return whether the server may set ``SO_REUSEADDR``.

    On Windows, ``SO_REUSEADDR`` lets a socket bind a port that another program is
    listening on, so the fallback to a free port would never run.

    Args:
        platform: The value of ``sys.platform``.

    Returns:
        False on Windows, else True (quick restarts on the same port).
    """
    return platform != "win32"


class _GrillServer(http.server.ThreadingHTTPServer):
    """The HTTP server, without address reuse on Windows."""

    allow_reuse_address = _reuse_address(sys.platform)


def _alive(port: int, root: Path) -> bool:
    """Return whether the show-and-grill server for ``root`` answers on 127.0.0.1:port.

    Args:
        port: The port to ask.
        root: The served folder.

    Returns:
        True only if the answer carries this folder's id.
    """
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/", timeout=1) as res:
            header: str | None = res.headers.get("X-Grill-Serve")
            return header == _folder_id(root)
    except (OSError, ValueError):
        return False


def _read_state(root: Path) -> dict[str, Any] | None:
    """Read ``.serve.json``, or return None if it is missing or broken."""
    try:
        data = json.loads((root / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _port_from_state(state: dict[str, Any] | None) -> int | None:
    """Return the saved port, or None if it is missing or not a valid port.

    Args:
        state: The content of ``.serve.json`` (or None).

    Returns:
        An int from 1 to 65535, or None.
    """
    value = state.get("port") if state else None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    try:
        port = int(value)
    except ValueError:
        return None
    return port if 1 <= port <= 65535 else None


def make_server(
    root: Path,
    preferred_port: int = 0,
    on_request: Callable[[], None] | None = None,
    stop_token: str | None = None,
    bind_wait_seconds: float = 0,
) -> http.server.ThreadingHTTPServer:
    """Create (but do not start) a server that serves ``root`` on 127.0.0.1.

    Args:
        root: The folder to serve.
        preferred_port: The port to try first. 0 means any free port.
        on_request: Called for each accepted request (used for the idle stop).
        stop_token: The token a stop request must carry. None refuses every stop request.
        bind_wait_seconds: How long to retry ``preferred_port`` (an old server may still
            be closing it) before falling back to a free port.

    Returns:
        The bound server. Call ``serve_forever()`` to start it.
    """
    root = root.resolve()
    folder_id = _folder_id(root)
    port_box: list[int] = []

    class Handler(http.server.SimpleHTTPRequestHandler):
        """Serves ``root`` with the guards in the module docstring."""

        def _allowed(self) -> bool:
            """Check the Host header and the path, and send an error if not allowed.

            Returns:
                True if the request may be served.
            """
            if not _host_allowed(self.headers.get("Host") or "", port_box[0]):
                self.send_error(403, "Host not allowed")
                return False
            if _is_hidden_path(self.path) or not _inside(
                root, self.translate_path(self.path)
            ):
                self.send_error(404)
                return False
            if on_request is not None:
                on_request()
            return True

        def do_GET(self) -> None:  # noqa: N802 - http.server naming
            """Serve a GET request after the guards."""
            if self._allowed():
                super().do_GET()

        def do_HEAD(self) -> None:  # noqa: N802 - http.server naming
            """Serve a HEAD request after the guards."""
            if self._allowed():
                super().do_HEAD()

        def do_POST(self) -> None:  # noqa: N802 - http.server naming
            """Stop the server on a valid stop request, else answer 403.

            It does not touch ``.serve.json`` and does not count as activity.
            """
            if not _host_allowed(self.headers.get("Host") or "", port_box[0]):
                self.send_error(403, "Host not allowed")
                return
            if not _stop_allowed(self.path, self.headers.get(STOP_HEADER), stop_token):
                self.send_error(403, "Stop not allowed")
                return
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.wfile.flush()
            # shutdown() waits for serve_forever() to end, so call it from another thread.
            threading.Thread(target=self.server.shutdown, daemon=True).start()

        def end_headers(self) -> None:
            """Add no-cache and this folder's id to every answer."""
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Grill-Serve", folder_id)
            super().end_headers()

        def list_directory(self, path: str | os.PathLike[str]) -> io.BytesIO | None:
            """List a folder without dot files (the index link shows this page).

            Args:
                path: The folder on disk.

            Returns:
                The page, or None after sending an error.
            """
            try:
                names = sorted(
                    (n for n in os.listdir(path) if not n.startswith(".")),
                    key=str.lower,
                )
            except OSError:
                self.send_error(404, "No permission to list directory")
                return None
            items = []
            for name in names:
                shown = name + "/" if os.path.isdir(os.path.join(path, name)) else name
                link = urllib.parse.quote(shown, errors="surrogatepass")
                items.append(f'<li><a href="{link}">{html.escape(shown)}</a></li>')
            page = (
                '<!doctype html><meta charset="utf-8"><title>Index</title><ul>'
                + "".join(items)
                + "</ul>"
            ).encode("utf-8", "surrogateescape")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            return io.BytesIO(page)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            """Do not log each request."""
            return

    handler = functools.partial(Handler, directory=str(root))
    deadline = time.monotonic() + bind_wait_seconds
    while True:
        try:
            httpd = _GrillServer((HOST, preferred_port), handler)
            break
        except OSError:
            if preferred_port == 0 or time.monotonic() >= deadline:
                httpd = _GrillServer((HOST, 0), handler)
                break
            time.sleep(0.1)
    port_box.append(int(httpd.server_address[1]))
    return httpd


def status(root: Path, wait_seconds: float) -> int:
    """Print the URL and return 0 if running, else return 1.

    Waits a little, because the server may have just started.

    Args:
        root: The served folder.
        wait_seconds: How long to keep checking.

    Returns:
        0 if a server answers, else 1.
    """
    deadline = time.monotonic() + wait_seconds
    while True:
        port = _port_from_state(_read_state(root))
        if port and _alive(port, root):
            print(f"http://{HOST}:{port}/")
            return 0
        if time.monotonic() >= deadline:
            print("NOT_RUNNING", file=sys.stderr)
            return 1
        time.sleep(0.3)


def wait_log(root: Path, log: Path, timeout: float = 10) -> int:
    """Print the URL from the first ``SERVING`` line in ``log`` once that server answers.

    ``log`` holds the output of the server that this session just started. Do not use
    ``.serve.json`` or ``--status`` here: an old server may still answer before the new one
    takes over.

    Args:
        root: The served folder.
        log: The file that holds the new server's output.
        timeout: How long to wait, in seconds.

    Returns:
        0 if the URL in the line answers with this folder's id, else 1.
    """
    deadline = time.monotonic() + timeout
    while True:
        try:
            match = SERVING_RE.search(log.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            match = None
        if match and _alive(int(match.group(2)), root):
            print(match.group(1))
            return 0
        if time.monotonic() >= deadline:
            print("NOT_READY", file=sys.stderr)
            return 1
        time.sleep(0.2)


def _ask_to_stop(port: int, token: object) -> bool:
    """Send a stop request to the server on ``port``.

    Args:
        port: The server's port.
        token: The token from ``.serve.json`` (any type; only an ASCII string is sent).

    Returns:
        True if the server answered 200.
    """
    if not isinstance(token, str) or not token or not token.isascii():
        return False
    req = urllib.request.Request(  # noqa: S310 - fixed http://127.0.0.1 URL
        f"http://{HOST}:{port}/", data=b"", method="POST", headers={STOP_HEADER: token}
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as res:  # noqa: S310
            return bool(res.status == 200)
    except (OSError, ValueError):
        return False


def _wait_until_gone(port: int, root: Path, seconds: float) -> None:
    """Wait until the server for ``root`` on ``port`` no longer answers.

    Args:
        port: The port to ask.
        root: The served folder.
        seconds: The longest time to wait.
    """
    deadline = time.monotonic() + seconds
    while _alive(port, root) and time.monotonic() < deadline:
        time.sleep(0.1)


def _write_state(root: Path, data: dict[str, Any]) -> None:
    """Write ``.serve.json`` in one step (a reader never sees half a file).

    Args:
        root: The served folder.
        data: The content to write.
    """
    fd, tmp = tempfile.mkstemp(dir=root, prefix=".serve.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, root / STATE_FILE)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def serve(root: Path, idle_minutes: float) -> int:
    """Take over or start a server for ``root``, and serve until idle or stopped.

    If a server for the same folder already runs, ask it to stop and serve on its port.
    Print the ``SERVING`` line only after the port is bound and ``.serve.json`` is written.

    Args:
        root: The folder to serve.
        idle_minutes: Stop after this many minutes with no requests.

    Returns:
        The exit code (0).
    """
    root = root.resolve()
    state = _read_state(root)
    saved_port = _port_from_state(state)
    stopped = False
    blocked = False  # an old server that does not stop still holds the saved port
    if saved_port and _alive(saved_port, root):
        stopped = _ask_to_stop(saved_port, (state or {}).get("token"))
        if stopped:
            _wait_until_gone(saved_port, root, seconds=5)
            print(f"TOOK_OVER the server on port {saved_port}", flush=True)
        else:
            blocked = True
            print(
                f"COULD_NOT_STOP the server on port {saved_port}; using a free port",
                flush=True,
            )

    last = [time.monotonic()]

    def touch() -> None:
        last[0] = time.monotonic()

    token = secrets.token_urlsafe(32)
    httpd = make_server(
        root,
        0 if blocked else saved_port or 0,
        on_request=touch,
        stop_token=token,
        bind_wait_seconds=5 if stopped else 0,
    )
    port = int(httpd.server_address[1])
    _write_state(root, {"port": port, "pid": os.getpid(), "token": token})

    def watchdog() -> None:
        while time.monotonic() - last[0] < idle_minutes * 60:
            time.sleep(min(10.0, max(0.1, idle_minutes * 60)))
        httpd.shutdown()

    threading.Thread(target=watchdog, daemon=True).start()
    print(
        f"SERVING http://{HOST}:{port}/ (idle stop: {idle_minutes:g} min)", flush=True
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Do not touch .serve.json: a newer server may own it now. The saved port stays,
        # so the next start reuses it and open tabs come back on reload.
        httpd.server_close()
    return 0


def main() -> int:
    """Run the command line."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "dir", type=Path, help="the folder to serve (the grill/ work folder)"
    )
    parser.add_argument(
        "--status", action="store_true", help="print the URL if running"
    )
    parser.add_argument(
        "--wait-log",
        type=Path,
        metavar="LOG",
        help="print the URL from the SERVING line in LOG (the new server's output)",
    )
    parser.add_argument(
        "--idle-minutes",
        type=float,
        default=120,
        help="stop after this many idle minutes",
    )
    args = parser.parse_args()
    root = args.dir.resolve()
    if not root.is_dir():
        print(f"NOT_A_DIRECTORY {root}", file=sys.stderr)
        return 2
    if args.status:
        return status(root, wait_seconds=5)
    if args.wait_log is not None:
        return wait_log(root, args.wait_log)
    return serve(root, args.idle_minutes)


if __name__ == "__main__":
    sys.exit(main())
