"""Tests for scripts/serve.ps1 (the server for Windows without Python).

Each test starts serve.ps1 in a child process with every PowerShell found
(``pwsh`` on any system, and ``powershell`` on Windows), reads the ``SERVING``
line, and sends raw HTTP requests over 127.0.0.1.

When no PowerShell is found, the tests are skipped. With
``SHOW_AND_GRILL_REQUIRE_PWSH=1`` (set in CI), each expected PowerShell that
is missing fails instead (on Windows, both ``powershell`` and ``pwsh``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import http.client
import os
from pathlib import Path
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

_REPO_DIR = Path(__file__).resolve().parents[1]
_SERVE_PS1 = _REPO_DIR / "skills" / "show-and-grill" / "scripts" / "serve.ps1"
_START_TIMEOUT = 60.0


def _shell_names() -> list[str]:
    """Name the PowerShell executables to test with.

    Returns:
        ``pwsh`` anywhere, and ``powershell`` (5.1) on Windows.
    """
    names = ["pwsh"]
    if sys.platform == "win32":
        names.append("powershell")
    return names


def _shell_params() -> list[object]:
    """Build the parametrize values: one per shell, or a skip / fail marker.

    With ``SHOW_AND_GRILL_REQUIRE_PWSH=1``, each expected shell that is missing
    becomes a failing value, so CI cannot pass with only one of them.

    Returns:
        The values for ``pytest.mark.parametrize``.
    """
    require = os.environ.get("SHOW_AND_GRILL_REQUIRE_PWSH") == "1"
    found: list[object] = []
    missing: list[object] = []
    for name in _shell_names():
        path = shutil.which(name)
        if path:
            found.append(pytest.param(path, id=name))
        elif require:
            missing.append(pytest.param(f"missing:{name}", id=f"no-{name}"))
    if found or missing:
        return found + missing
    return [
        pytest.param(
            None,
            id="no-powershell",
            marks=pytest.mark.skip(reason="no pwsh (or powershell on Windows) found"),
        )
    ]


@pytest.fixture(params=_shell_params())
def shell(request: pytest.FixtureRequest) -> str:
    """The PowerShell executable for this test.

    Returns:
        Its full path.
    """
    path = request.param
    if isinstance(path, str) and path.startswith("missing:"):
        name = path.removeprefix("missing:")
        pytest.fail(f"SHOW_AND_GRILL_REQUIRE_PWSH=1 is set, but {name} was not found")
    return str(path)


@pytest.mark.parametrize("require", ["1", ""])
def test_ci_requires_each_powershell_on_windows(monkeypatch: pytest.MonkeyPatch, require: str) -> None:
    """On Windows with only pwsh, CI mode adds a failing value for powershell; otherwise only pwsh runs."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(shutil, "which", lambda name: "C:/ps/pwsh.exe" if name == "pwsh" else None)
    monkeypatch.setenv("SHOW_AND_GRILL_REQUIRE_PWSH", require)
    values = [p.values[0] for p in _shell_params()]  # type: ignore[attr-defined]
    if require:
        assert values == ["C:/ps/pwsh.exe", "missing:powershell"]
    else:
        assert values == ["C:/ps/pwsh.exe"]


def _command(shell: str, *args: str) -> list[str]:
    """Build the command line that runs serve.ps1.

    Args:
        shell: The PowerShell executable.
        *args: Arguments for serve.ps1.

    Returns:
        The command line.
    """
    return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(_SERVE_PS1), *args]


@dataclass
class Server:
    """A running serve.ps1."""

    proc: subprocess.Popen[str]
    url: str
    port: int
    root: Path


def _read_serving_line(proc: subprocess.Popen[str]) -> str:
    """Wait for the ``SERVING`` line, with a time limit.

    Args:
        proc: The server process (stdout is a pipe).

    Returns:
        The URL from the line.
    """
    lines: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.put(line)

    threading.Thread(target=reader, daemon=True).start()
    deadline = time.monotonic() + _START_TIMEOUT
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=0.2)
        except queue.Empty:
            if proc.poll() is not None:
                break
            continue
        if line.startswith("SERVING "):
            return line.split()[1]
    raise AssertionError("serve.ps1 did not print a SERVING line")


def _stop(proc: subprocess.Popen[str]) -> None:
    """Stop the server process (terminate, then kill after a time limit).

    Args:
        proc: The server process.
    """
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)


@pytest.fixture
def server(shell: str, tmp_path: Path) -> Iterator[Server]:
    """Start serve.ps1 on a served folder with a normal file, hidden files, and an outside file.

    Yields:
        The running server.
    """
    root = tmp_path / "grill"
    (root / ".hidden").mkdir(parents=True)
    (root / "sub").mkdir()
    (root / "q01.html").write_text("<p>q01</p>", encoding="utf-8")
    (root / "sub" / "a.css").write_text("body{}", encoding="utf-8")
    (root / ".serve.json").write_text('{"port": 1}', encoding="utf-8")
    (root / ".hidden" / "a.html").write_text("HIDDEN", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("SECRET-OUTSIDE", encoding="utf-8")
    proc = subprocess.Popen(  # noqa: S603 - run our own serve.ps1
        _command(shell, str(root)),
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        text=True,
    )
    try:
        url = _read_serving_line(proc)
        port = int(url.removeprefix("http://127.0.0.1:").rstrip("/"))
        yield Server(proc=proc, url=url, port=port, root=root)
    finally:
        _stop(proc)


def _request(
    server: Server, path: str, *, method: str = "GET", host: str | None = None
) -> tuple[int, dict[str, str], bytes]:
    """Send one request with the path written as is in the request line.

    Args:
        server: The running server.
        path: The raw path.
        method: The HTTP method.
        host: The Host header. None means ``127.0.0.1:<port>``.

    Returns:
        (status code, headers in lower case, body).
    """
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        conn.putheader("Host", f"127.0.0.1:{server.port}" if host is None else host)
        conn.endheaders()
        res = conn.getresponse()
        headers = {k.lower(): v for k, v in res.getheaders()}
        return res.status, headers, res.read()
    finally:
        conn.close()


def test_serving_line_is_on_loopback(server: Server) -> None:
    """The ``SERVING`` URL uses 127.0.0.1."""
    assert server.url.startswith("http://127.0.0.1:")
    assert server.url.endswith("/")
    assert server.port > 0


def test_serves_a_normal_file(server: Server) -> None:
    """A normal file gets 200 with its body and Content-Type."""
    status, headers, body = _request(server, "/q01.html")
    assert status == 200
    assert body == b"<p>q01</p>"
    assert headers["content-type"] == "text/html; charset=utf-8"
    status, headers, _ = _request(server, "/sub/a.css")
    assert status == 200
    assert headers["content-type"].startswith("text/css")


def test_head_has_no_body(server: Server) -> None:
    """HEAD gets the headers without the body."""
    status, headers, body = _request(server, "/q01.html", method="HEAD")
    assert status == 200
    assert body == b""
    assert headers["content-length"] == str(len(b"<p>q01</p>"))


@pytest.mark.parametrize("host", ["evil.example", "127.0.0.1:1", ""])
def test_other_host_gets_403(server: Server, host: str) -> None:
    """Another Host, another port, or an empty Host gets 403."""
    status, _, body = _request(server, "/q01.html", host=host)
    assert status == 403
    assert b"q01" not in body


@pytest.mark.parametrize("template", ["localhost:{port}", "LOCALHOST:{port}", "LocalHost:{port}"])
def test_localhost_host_is_allowed(server: Server, template: str) -> None:
    """localhost on our port is allowed, in any letter case."""
    status, _, body = _request(server, "/q01.html", host=template.format(port=server.port))
    assert status == 200
    assert body == b"<p>q01</p>"


@pytest.mark.parametrize("path", ["/.serve.json", "/%2eserve.json", "/%2Eserve.json", "/.hidden/a.html"])
def test_hidden_paths_get_404(server: Server, path: str) -> None:
    """Paths with a part that starts with a dot get 404."""
    status, _, body = _request(server, path)
    assert status == 404
    assert b'"port"' not in body
    assert b"HIDDEN" not in body


@pytest.mark.parametrize(
    "path", ["/../outside.txt", "/%2e%2e/outside.txt", "/..%5coutside.txt", "/sub/../../outside.txt"]
)
def test_paths_outside_the_folder_get_404(server: Server, path: str) -> None:
    """Paths that leave the folder get 404, however they are written."""
    status, _, body = _request(server, path)
    assert status == 404
    assert b"SECRET-OUTSIDE" not in body


@pytest.mark.parametrize("path", ["/", "/sub", "/sub/"])
def test_folders_get_404(server: Server, path: str) -> None:
    """A folder gets 404 (no listing)."""
    status, _, body = _request(server, path)
    assert status == 404
    assert b"q01.html" not in body


def test_missing_file_gets_404(server: Server) -> None:
    """A file that does not exist gets 404."""
    status, _, _ = _request(server, "/nope.html")
    assert status == 404


def test_post_gets_405(server: Server) -> None:
    """A method other than GET and HEAD gets 405."""
    status, headers, _ = _request(server, "/q01.html", method="POST")
    assert status == 405
    assert "GET" in headers.get("allow", "")


def test_link_to_outside_gets_404(server: Server, tmp_path: Path) -> None:
    """A symbolic link inside the folder that points outside gets 404."""
    try:
        (server.root / "linked.txt").symlink_to(tmp_path / "outside.txt")
        (server.root / "linked-dir").symlink_to(tmp_path, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"cannot make a symbolic link here: {exc}")
    for path in ("/linked.txt", "/linked-dir/outside.txt"):
        status, _, body = _request(server, path)
        assert status == 404, path
        assert b"SECRET-OUTSIDE" not in body


def test_broken_request_does_not_stop_the_server(server: Server) -> None:
    """After a broken request line and a connection closed early, the next request still gets 200."""
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as sock:
        sock.sendall(b"garbage\r\n\r\n")
        try:
            sock.recv(1024)
        except OSError:
            pass
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as sock:
        sock.sendall(b"GET /q01.html HTTP/1.1\r\nHo")
    status, _, body = _request(server, "/q01.html")
    assert status == 200
    assert body == b"<p>q01</p>"
    assert server.proc.poll() is None


def test_not_a_directory_exits_1(shell: str, tmp_path: Path) -> None:
    """A folder that does not exist exits 1 with ``NOT_A_DIRECTORY``."""
    missing = tmp_path / "missing"
    result = subprocess.run(  # noqa: S603 - run our own serve.ps1
        _command(shell, str(missing)),
        capture_output=True,
        text=True,
        timeout=_START_TIMEOUT,
        check=False,
    )
    assert result.returncode == 1
    assert "NOT_A_DIRECTORY" in result.stderr
