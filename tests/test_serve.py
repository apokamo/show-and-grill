"""Tests for scripts/serve.py.

- Pure functions (hidden paths, the Host header, stop requests, state) are called directly.
- Server tests make a served folder in ``tmp_path`` and talk to the server over a
  127.0.0.1 socket, in a thread or in a child process.
"""

from __future__ import annotations

from collections.abc import Iterator
import functools
import http.client
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING
import urllib.request

import pytest

if TYPE_CHECKING:
    from http.server import ThreadingHTTPServer
    from types import ModuleType

SERVE_PY = (
    Path(__file__).resolve().parents[1] / "skills" / "show-and-grill" / "scripts" / "serve.py"
)


@functools.cache
def _load_serve() -> ModuleType:
    """Load serve.py from its file path (once; later calls return the same module).

    Returns:
        The loaded serve.py module.
    """
    spec = importlib.util.spec_from_file_location("show_and_grill_serve", SERVE_PY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Do not create __pycache__ in the skill folder (a contract test reads the skill files as text)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


# --- Pure functions: hidden paths ---------------------------------------------


@pytest.mark.parametrize(
    "raw_path",
    [
        "/.serve.json",
        "/.hidden-test",
        "/%2eserve.json",
        "/%2Eserve.json",
        "/assets/%2ehidden",
        "/%2e%2e/secret",
        "/../secret",
        "/.serve.json?x=1",
        "//.serve.json",
        "//assets/.hidden",
    ],
)
def test_hidden_paths_are_rejected(raw_path: str) -> None:
    """A path with a part that starts with a dot, after percent-decoding, is hidden."""
    assert _load_serve()._is_hidden_path(raw_path) is True


@pytest.mark.parametrize(
    "raw_path",
    [
        "/",
        "/q01.html",
        "/assets/grill.js",
        "/q01.html?v=2",
        "/a%20b.html",
        "//q01.html",
    ],
)
def test_normal_paths_are_allowed(raw_path: str) -> None:
    """A path with no part that starts with a dot is served."""
    assert _load_serve()._is_hidden_path(raw_path) is False


# --- Pure functions: the Host header ------------------------------------------


@pytest.mark.parametrize(
    "host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST:8765", "LocalHost:8765"]
)
def test_host_on_own_port_is_allowed(host: str) -> None:
    """Only 127.0.0.1 and localhost on our own port pass (any letter case)."""
    assert _load_serve()._host_allowed(host, 8765) is True


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1:8766",
        "localhost:80",
        "127.0.0.1",
        "localhost",
        "evil.example:8765",
        "127.0.0.1.evil.example:8765",
        "",
    ],
)
def test_other_hosts_are_rejected(host: str) -> None:
    """Another port, another name, no port, or an empty Host does not pass (DNS rebinding)."""
    assert _load_serve()._host_allowed(host, 8765) is False


# --- Pure functions: stop requests ---------------------------------------------


@pytest.mark.parametrize("raw_path", ["/", "/?x=1"])
def test_stop_allowed_with_matching_token(raw_path: str) -> None:
    """Allowed only when the path is ``/`` (query ignored) and the header matches ``token``."""
    assert _load_serve()._stop_allowed(raw_path, "tok-1", "tok-1") is True


@pytest.mark.parametrize(
    ("raw_path", "given", "token"),
    [
        ("/", None, "tok-1"),
        ("/", "", "tok-1"),
        ("/", "tok-2", "tok-1"),
        ("/", "tök-1", "tok-1"),
        ("/q01.html", "tok-1", "tok-1"),
        ("//", "tok-1", "tok-1"),
        ("/", "tok-1", None),
        ("/", "", ""),
    ],
)
def test_stop_not_allowed(raw_path: str, given: str | None, token: str | None) -> None:
    """No, empty, wrong, or non-ASCII header, a path other than ``/``, or a server without ``token``: refused."""
    assert _load_serve()._stop_allowed(raw_path, given, token) is False


# --- Pure functions: state, address reuse, folder id ---------------------------


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"port": 8765}, 8765),
        ({"port": "8765"}, 8765),
        ({"port": "abc"}, None),
        ({"port": 1.5e9}, None),
        ({"port": 0}, None),
        ({"port": True}, None),
        ({"port": None}, None),
        ({}, None),
        (None, None),
    ],
)
def test_port_from_state(state: dict[str, object] | None, expected: int | None) -> None:
    """The port in ``.serve.json`` is used only as an integer from 1 to 65535; otherwise None."""
    assert _load_serve()._port_from_state(state) == expected


@pytest.mark.parametrize(
    ("platform", "expected"), [("win32", False), ("linux", True), ("darwin", True)]
)
def test_reuse_address_is_off_on_windows(platform: str, expected: bool) -> None:
    """On Windows, turn off address reuse so a port in use cannot be shared."""
    assert _load_serve()._reuse_address(platform) is expected


def test_folder_id_differs_by_folder(tmp_path: Path) -> None:
    """Each served folder gets its own id (the same folder gets the same id)."""
    serve = _load_serve()
    assert serve._folder_id(tmp_path / "a") == serve._folder_id(tmp_path / "a")
    assert serve._folder_id(tmp_path / "a") != serve._folder_id(tmp_path / "b")


@pytest.fixture
def served(tmp_path: Path) -> Iterator[tuple[ThreadingHTTPServer, Path, Path]]:
    """Run a server for ``tmp_path/root`` in another thread.

    Yields:
        (server, served folder, a file outside the folder).
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "q01.html").write_text("<p>q01</p>", encoding="utf-8")
    (root / ".serve.json").write_text('{"port": 1, "pid": null}', encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET-OUTSIDE", encoding="utf-8")
    (root / "linked.txt").symlink_to(outside)

    httpd = _load_serve().make_server(root)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, root, outside
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(
    httpd: ThreadingHTTPServer, path: str, host: str | None = None
) -> tuple[int, bytes]:
    """Send GET to the server and return the status and the body.

    Args:
        httpd: The running server.
        path: The path to send (written as is in the request line).
        host: The Host header. None means ``127.0.0.1:<port>``.

    Returns:
        (status code, body).
    """
    port = httpd.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
        res = conn.getresponse()
        return res.status, res.read()
    finally:
        conn.close()


def test_server_listens_only_on_loopback(
    served: tuple[ThreadingHTTPServer, Path, Path],
) -> None:
    """The server listens only on 127.0.0.1."""
    httpd, _, _ = served
    assert httpd.server_address[0] == "127.0.0.1"


def test_serves_normal_file(served: tuple[ThreadingHTTPServer, Path, Path]) -> None:
    """A normal file in the folder is served (so the 404 tests below do not pass by accident)."""
    httpd, _, _ = served
    status, body = _get(httpd, "/q01.html")
    assert status == 200
    assert body == b"<p>q01</p>"


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:1", ""])
def test_other_host_gets_403(
    served: tuple[ThreadingHTTPServer, Path, Path], host: str
) -> None:
    """A different Host gets 403."""
    httpd, _, _ = served
    status, body = _get(httpd, "/q01.html", host=host or " ")
    assert status == 403
    assert b"q01" not in body


@pytest.mark.parametrize("path", ["/.serve.json", "/%2eserve.json"])
def test_hidden_file_gets_404(
    served: tuple[ThreadingHTTPServer, Path, Path], path: str
) -> None:
    """A hidden file gets 404."""
    httpd, _, _ = served
    status, body = _get(httpd, path)
    assert status == 404
    assert b'"port"' not in body


@pytest.mark.parametrize(
    "path", ["/../outside.txt", "/%2e%2e/outside.txt", "/linked.txt"]
)
def test_paths_outside_the_folder_are_not_served(
    served: tuple[ThreadingHTTPServer, Path, Path], path: str
) -> None:
    """The content of a file outside the folder never comes back, however it is written (a link also gets 404)."""
    httpd, _, _ = served
    status, body = _get(httpd, path)
    assert b"SECRET-OUTSIDE" not in body
    assert status == 404


def test_status_prints_url_of_running_server(tmp_path: Path) -> None:
    """``--status`` prints the URL of a server started in another process."""
    root = tmp_path / "grill"
    root.mkdir()
    server = subprocess.Popen(  # noqa: S603 - run our own serve.py with the same Python
        [sys.executable, "-B", str(SERVE_PY), str(root)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        result = subprocess.run(  # noqa: S603 - same as above
            [sys.executable, "-B", str(SERVE_PY), str(root), "--status"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        url = result.stdout.strip()
        assert url.startswith("http://127.0.0.1:")
        assert url.endswith("/")
        port = int(url.removeprefix("http://127.0.0.1:").rstrip("/"))
        assert port > 0
    finally:
        server.terminate()
        server.wait(timeout=10)


def test_status_returns_1_when_not_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """When no server runs, ``status()`` returns 1 (with no wait)."""
    assert _load_serve().status(tmp_path, wait_seconds=0) == 1
    assert "NOT_RUNNING" in capsys.readouterr().err


def test_directory_listing_hides_dot_files(
    served: tuple[ThreadingHTTPServer, Path, Path],
) -> None:
    """The folder listing does not show names that start with a dot."""
    httpd, _, _ = served
    status, body = _get(httpd, "/")
    assert status == 200
    assert b"q01.html" in body
    assert b".serve.json" not in body


def test_status_ignores_a_server_for_another_folder(
    served: tuple[ThreadingHTTPServer, Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A server for another folder answering on the recorded port does not count as running."""
    httpd, _, _ = served
    other = tmp_path / "other"
    other.mkdir()
    port = httpd.server_address[1]
    (other / ".serve.json").write_text(
        f'{{"port": {port}, "pid": null}}', encoding="utf-8"
    )
    assert _load_serve().status(other, wait_seconds=0) == 1
    assert "NOT_RUNNING" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ['{"port": "abc"}', '{"port": 1.5e9}', "[1, 2]"])
def test_status_survives_a_broken_state_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], raw: str
) -> None:
    """A broken ``.serve.json`` does not crash; the answer is "not running"."""
    (tmp_path / ".serve.json").write_text(raw, encoding="utf-8")
    assert _load_serve().status(tmp_path, wait_seconds=0) == 1
    assert "NOT_RUNNING" in capsys.readouterr().err


# --- Resuming in another session (take-over, stop requests, start check) ------


def _start(
    root: Path, *args: str, stdout: int | object = subprocess.DEVNULL
) -> subprocess.Popen[bytes]:
    """Start ``serve.py <root>`` in another process (like a server started by another session).

    Args:
        root: The served folder.
        *args: More arguments.
        stdout: Where standard output goes (``subprocess.PIPE`` or a file).

    Returns:
        The started process.
    """
    return subprocess.Popen(  # noqa: S603 - run our own serve.py with the same Python
        [sys.executable, "-B", str(SERVE_PY), str(root), *args],
        stdout=stdout,  # type: ignore[arg-type]
        stderr=subprocess.DEVNULL,
    )


def _run_cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run ``serve.py <root> <args>`` and wait for it to end.

    Args:
        root: The served folder.
        *args: More arguments (``--status`` and so on).

    Returns:
        The result.
    """
    return subprocess.run(  # noqa: S603 - run our own serve.py with the same Python
        [sys.executable, "-B", str(SERVE_PY), str(root), *args],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def _port_of(url: str) -> int:
    """Get the port from ``http://127.0.0.1:<port>/``.

    Args:
        url: The server URL.

    Returns:
        The port.
    """
    return int(url.strip().removeprefix("http://127.0.0.1:").rstrip("/"))


def _state(root: Path) -> dict[str, object]:
    """Read ``.serve.json``.

    Args:
        root: The served folder.

    Returns:
        The content of ``.serve.json``.
    """
    data = json.loads((root / ".serve.json").read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _wait_for_token(root: Path, proc: subprocess.Popen[bytes]) -> dict[str, object]:
    """Wait until ``proc`` writes its own record to ``.serve.json`` (without sending HTTP).

    Args:
        root: The served folder.
        proc: The started server process.

    Returns:
        The content of ``.serve.json`` written by ``proc``.
    """
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            data = _state(root)
        except (OSError, ValueError):
            data = {}
        if data.get("pid") == proc.pid and isinstance(data.get("token"), str):
            return data
        time.sleep(0.05)
    raise AssertionError("the server did not write its record to .serve.json")


def _post_stop(
    port: int,
    token: str | None,
    *,
    host: str | None = None,
    path: str = "/",
) -> int:
    """Send a stop request (``POST`` + ``X-Grill-Stop``) and return the status.

    Args:
        port: The server port.
        token: The ``X-Grill-Stop`` value. None means no header.
        host: The Host header. None means ``127.0.0.1:<port>``.
        path: The path to send.

    Returns:
        The status code.
    """
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest("POST", path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        if token is not None:
            conn.putheader("X-Grill-Stop", token)
        conn.putheader("Content-Length", "0")
        conn.endheaders()
        res = conn.getresponse()
        res.read()
        return res.status
    finally:
        conn.close()


def _cleanup(*procs: subprocess.Popen[bytes]) -> None:
    """Stop the server processes that are still running.

    Args:
        *procs: The processes to stop.
    """
    for proc in procs:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=10)


def _http_get(url: str) -> tuple[int, bytes]:
    """Send GET to a URL.

    Args:
        url: Where to send it (``http://127.0.0.1:<port>/<path>``).

    Returns:
        (status code, body).
    """
    with urllib.request.urlopen(url, timeout=5) as res:  # noqa: S310 - 127.0.0.1 only
        return res.status, res.read()


def test_resumed_session_takes_over_the_server(tmp_path: Path) -> None:
    """Regression: a resumed session takes over the server that another session started.

    After the session that started A ends and A stops, ``--status`` still returns the URL on the
    same port and the link opens (before the fix, B ended at once with ``ALREADY_RUNNING``).
    """
    root = tmp_path / "grill"
    root.mkdir()
    (root / "q01.html").write_text("<p>q01</p>", encoding="utf-8")
    proc_a = _start(root)
    proc_b: subprocess.Popen[bytes] | None = None
    try:
        first = _run_cli(root, "--status")
        assert first.returncode == 0, first.stderr
        port = _port_of(first.stdout)

        proc_b = _start(root)  # the resumed session starts a server as the steps say
        try:
            proc_a.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass
        proc_a.kill()  # the session that started A ended (Codex closed the thread)
        proc_a.wait(timeout=10)

        after = _run_cli(root, "--status")
        assert after.returncode == 0, after.stderr
        assert _port_of(after.stdout) == port
        status, body = _http_get(f"http://127.0.0.1:{port}/q01.html")
        assert status == 200
        assert body == b"<p>q01</p>"
        assert proc_b.poll() is None
    finally:
        _cleanup(proc_a, *([proc_b] if proc_b else []))


@pytest.fixture
def served_with_token(
    tmp_path: Path,
) -> Iterator[tuple[ThreadingHTTPServer, threading.Thread, str]]:
    """Run a server that has a stop ``token`` in another thread.

    Yields:
        (server, the thread running ``serve_forever``, ``token``).
    """
    root = tmp_path / "root"
    root.mkdir()
    (root / "q01.html").write_text("<p>q01</p>", encoding="utf-8")
    token = "tok-0123456789"
    httpd = _load_serve().make_server(root, stop_token=token)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, thread, token
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("token", "host", "path"),
    [
        (None, None, "/"),
        ("wrong-token", None, "/"),
        ("tök-0123456789", None, "/"),
        ("tok-0123456789", "evil.example", "/"),
        ("tok-0123456789", None, "/q01.html"),
    ],
    ids=["no-token", "wrong-token", "non-ascii", "other-host", "other-path"],
)
def test_bad_stop_request_gets_403_and_keeps_serving(
    served_with_token: tuple[ThreadingHTTPServer, threading.Thread, str],
    token: str | None,
    host: str | None,
    path: str,
) -> None:
    """A stop request with no, wrong, or non-ASCII ``token``, another Host, or a path other than ``/`` gets 403 and the server keeps running."""
    httpd, thread, _ = served_with_token
    port = int(httpd.server_address[1])
    assert _post_stop(port, token, host=host, path=path) == 403
    thread.join(timeout=0.5)
    assert thread.is_alive()
    status, body = _get(httpd, "/q01.html")
    assert status == 200
    assert body == b"<p>q01</p>"


def test_good_stop_request_stops_the_server(
    served_with_token: tuple[ThreadingHTTPServer, threading.Thread, str],
) -> None:
    """A stop request with the right ``token`` gets 200, then ``serve_forever`` ends."""
    httpd, thread, token = served_with_token
    port = int(httpd.server_address[1])
    assert _post_stop(port, token) == 200
    thread.join(timeout=10)
    assert not thread.is_alive()


def test_server_without_token_refuses_every_stop_request(tmp_path: Path) -> None:
    """A server made without ``stop_token`` refuses every stop request."""
    root = tmp_path / "root"
    root.mkdir()
    httpd = _load_serve().make_server(root)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(httpd.server_address[1])
        assert _post_stop(port, "") == 403
        assert _post_stop(port, "None") == 403
        assert thread.is_alive()
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_stopping_server_does_not_rewrite_state_file(tmp_path: Path) -> None:
    """A server stopped by a stop request does not rewrite ``.serve.json`` of another owner with the same pid."""
    root = tmp_path / "grill"
    root.mkdir()
    proc_a = _start(root)
    try:
        state = _wait_for_token(root, proc_a)
        port = int(str(state["port"]))
        token = str(state["token"])
        # "B's record" whose pid matches because PID namespaces differ
        other = json.dumps({"port": port, "pid": proc_a.pid, "token": "other-owner"})
        (root / ".serve.json").write_text(other, encoding="utf-8")
        assert _post_stop(port, token) == 200
        proc_a.wait(timeout=15)
        assert (root / ".serve.json").read_text(encoding="utf-8") == other
    finally:
        _cleanup(proc_a)


def test_idle_stop_does_not_rewrite_state_file(tmp_path: Path) -> None:
    """A server stopped by the idle timer does not rewrite ``.serve.json`` either."""
    root = tmp_path / "grill"
    root.mkdir()
    proc_a = _start(root, "--idle-minutes", "0.05")
    try:
        state = _wait_for_token(root, proc_a)
        other = json.dumps(
            {"port": state["port"], "pid": proc_a.pid, "token": "other-owner"}
        )
        (root / ".serve.json").write_text(other, encoding="utf-8")
        proc_a.wait(timeout=30)
        assert (root / ".serve.json").read_text(encoding="utf-8") == other
    finally:
        _cleanup(proc_a)


def test_state_after_takeover_lets_the_next_session_take_over(tmp_path: Path) -> None:
    """After a take-over, ``.serve.json`` holds B's pid and ``token``, and that ``token`` stops B."""
    root = tmp_path / "grill"
    root.mkdir()
    proc_a = _start(root)
    proc_b: subprocess.Popen[bytes] | None = None
    try:
        state_a = _wait_for_token(root, proc_a)
        proc_b = _start(root)
        state_b = _wait_for_token(root, proc_b)
        proc_a.wait(timeout=15)  # A stopped on the stop request
        assert state_b["port"] == state_a["port"]
        assert state_b["token"] != state_a["token"]
        assert _post_stop(int(str(state_b["port"])), str(state_b["token"])) == 200
        proc_b.wait(timeout=15)
    finally:
        _cleanup(proc_a, *([proc_b] if proc_b else []))


def test_wait_log_ignores_the_old_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Even while A answers, no ``SERVING`` line in ``<log>`` means 1, and A's URL is not printed."""
    root = tmp_path / "grill"
    root.mkdir()
    proc_a = _start(root)
    try:
        state = _wait_for_token(root, proc_a)
        log = root / ".serve.log"
        assert _load_serve().wait_log(root, log, timeout=1) == 1
        log.write_text("", encoding="utf-8")
        assert _load_serve().wait_log(root, log, timeout=1) == 1
        out = capsys.readouterr()
        assert "NOT_READY" in out.err
        assert f":{state['port']}/" not in out.out
    finally:
        _cleanup(proc_a)


def test_wait_log_takes_the_new_server_when_the_old_one_cannot_stop(
    tmp_path: Path,
) -> None:
    """With an old server that cannot be stopped, B serves on another port, and ``--wait-log`` and ``--status`` point to B."""
    root = tmp_path / "grill"
    root.mkdir()
    (root / "q01.html").write_text("<p>q01</p>", encoding="utf-8")
    proc_a = _start(root)
    proc_b: subprocess.Popen[bytes] | None = None
    try:
        state_a = _wait_for_token(root, proc_a)
        port_a = int(str(state_a["port"]))
        # make it look like an old version of the server (a record without a token)
        (root / ".serve.json").write_text(
            json.dumps({"port": port_a, "pid": proc_a.pid}), encoding="utf-8"
        )
        log = root / ".serve.log"
        with log.open("wb") as out:
            proc_b = _start(root, stdout=out)
        result = _run_cli(root, "--wait-log", str(log))
        assert result.returncode == 0, result.stderr
        url = result.stdout.strip()
        port_b = _port_of(url)
        assert port_b != port_a
        assert _state(root)["port"] == port_b
        assert proc_a.poll() is None  # it could not be stopped

        proc_a.kill()
        proc_a.wait(timeout=10)
        status, body = _http_get(url + "q01.html")
        assert status == 200
        assert body == b"<p>q01</p>"
        after = _run_cli(root, "--status")
        assert after.returncode == 0, after.stderr
        assert _port_of(after.stdout) == port_b
    finally:
        _cleanup(proc_a, *([proc_b] if proc_b else []))


def test_state_file_is_written_before_serving_line(tmp_path: Path) -> None:
    """When the ``SERVING`` line appears, ``.serve.json`` already holds B's port, pid, and ``token``."""
    root = tmp_path / "grill"
    root.mkdir()
    proc_a = _start(root)
    proc_b: subprocess.Popen[bytes] | None = None
    try:
        _wait_for_token(root, proc_a)
        out = tmp_path / "b.log"
        with out.open("wb") as fh:
            proc_b = _start(root, stdout=fh)
        line = b""
        deadline = time.monotonic() + 15  # do not hang if a regression never prints SERVING
        while not line and time.monotonic() < deadline and proc_b.poll() is None:
            for raw in out.read_bytes().split(b"\n")[:-1]:  # only complete lines
                if raw.startswith(b"SERVING "):
                    line = raw
                    break
            else:
                time.sleep(0.01)
        assert line, "no SERVING line"
        state = _state(root)  # read right after seeing the line
        port = _port_of(line.decode().split()[1])
        assert state["port"] == port
        assert state["pid"] == proc_b.pid
        assert isinstance(state["token"], str) and state["token"]
    finally:
        _cleanup(proc_a, *([proc_b] if proc_b else []))


def test_wait_log_fails_for_a_url_that_is_not_this_folder(
    served: tuple[ThreadingHTTPServer, Path, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """1 when the URL in the ``SERVING`` line is a server for another folder or a port nobody answers."""
    httpd, _, _ = served
    other = tmp_path / "other"
    other.mkdir()
    log = other / ".serve.log"
    port = int(httpd.server_address[1])
    log.write_text(f"SERVING http://127.0.0.1:{port}/ (idle stop: 120 min)\n")
    assert _load_serve().wait_log(other, log, timeout=1) == 1

    free = _load_serve().make_server(other)
    free_port = int(free.server_address[1])
    free.server_close()  # a port nobody answers
    log.write_text(f"SERVING http://127.0.0.1:{free_port}/ (idle stop: 120 min)\n")
    assert _load_serve().wait_log(other, log, timeout=1) == 1
    assert "NOT_READY" in capsys.readouterr().err
