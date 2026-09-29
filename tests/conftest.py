"""Shared fixtures."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


class KeyServer:
    """A fake github.com and launchpad.net serving ssh keys.

    answers["alice"] is what GET /alice.keys (GitHub) gets and
    answers["lp:alice"] what GET /~alice/+sshkeys (Launchpad) gets: a
    (status, body) pair, or a list of them answered in turn (the last one
    repeats). Unknown users get a 404. hits counts the requests per user.
    """

    def __init__(self) -> None:
        self.answers: dict[str, tuple[int, str] | list[tuple[int, str]]] = {}
        self.hits: dict[str, int] = {}
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.strip("/")
                if path.startswith("~") and path.endswith("/+sshkeys"):
                    user = "lp:" + path[1:].removesuffix("/+sshkeys")
                else:
                    user = path.removesuffix(".keys")
                n = server.hits.get(user, 0)
                server.hits[user] = n + 1
                answer = server.answers.get(user, (404, "Not Found"))
                if isinstance(answer, list):
                    answer = answer[min(n, len(answer) - 1)]
                status, body = answer
                data = body.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._httpd.server_address[1]}"
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._httpd.shutdown()


@pytest.fixture
def keyserver():
    server = KeyServer()
    yield server
    server.close()


@pytest.fixture
def github(keyserver):
    """The fake GitHub as (base url, answers), as the fixpi tests use it."""
    return keyserver.url, keyserver.answers


def closed_port_url() -> str:
    """A URL nothing listens on: a transport error (uri status -1)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"
