"""The Tiny Tapeout site hands a board's serial WebSocket to whichever Pi carries the board now.

No nginx location names a board or a switch port (fpgas.online-tt issue #14).
The Commander opens /ws/board/<slug>/serial; Django answers with
"X-Accel-Redirect: /_tt-serial/<the Pi's address>" and nginx serves the same
request from the one internal location roles/ttsite renders. These tests run a
real nginx (in docker) with that rendered location, a stand-in for Django and
two stand-ins for Pi bridges, and fail if:
  - a WebSocket upgrade does not survive the hand-off (the bridge must see the
    client's Upgrade request on /serial and the client must get its frames),
  - the address Django names does not choose the Pi,
  - a client can reach the internal location itself,
  - an address outside the Pi network is proxied to,
  - a Pi's own answer can send the visitor on to another Pi,
  - the rendered file names a board, a slug or a port again.
"""

import base64
import hashlib
import http.server
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import jinja2
import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
ROLE = REPO / "ansible/roles/ttsite"
TEMPLATE = ROLE / "templates/ws-board.conf.j2"
IMAGE = "nginx:stable-alpine"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# Loopback stands in for the Pi network: 127.0.<switch>.<port>.
PI_NETWORK = "127.0"
PI_A, PI_B = "127.0.2.13", "127.0.2.36"
# A Pi a visitor has taken over: its bridge answers "X-Accel-Redirect: /_tt-serial/<PI_A>".
PI_ROGUE = "127.0.2.40"
# Which address the stand-in for Django names for each board.
WHERE = {
    "/ws/board/fpga-4/serial": f"/_tt-serial/{PI_A}",
    "/ws/board/tt-a2961e5cac65b25f/serial": f"/_tt-serial/{PI_B}",
    "/ws/board/elsewhere/serial": "/_tt-serial/10.9.9.9",
    "/ws/board/not-an-address/serial": "/_tt-serial/127.0.2.13.evil.example",
    "/ws/board/dot-is-a-dot/serial": "/_tt-serial/127x0.2.13",
    "/ws/board/no-such-octet/serial": "/_tt-serial/127.0.300.9",
    "/ws/board/rogue/serial": f"/_tt-serial/{PI_ROGUE}",
}


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def render(daemon_port):
    defaults = yaml.safe_load((ROLE / "defaults/main.yml").read_text())
    env = jinja2.Environment(undefined=jinja2.StrictUndefined, keep_trailing_newline=True)
    return env.from_string(TEMPLATE.read_text()).render(
        ansible_managed="Ansible managed", ttsite_daemon_port=daemon_port, ttsite_pi_network=PI_NETWORK,
        ttsite_ws_read_timeout=defaults["ttsite_ws_read_timeout"])


class Bridge(threading.Thread):
    """A Pi's bridge: accepts a WebSocket upgrade on /serial and sends one text frame naming itself."""

    def __init__(self, address, port, send_on_to=None):
        super().__init__(daemon=True)
        self.name_sent = f"bridge at {address}"
        self.send_on_to = send_on_to
        self.requests = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((address, port))
        self.sock.listen()

    def run(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                head = b""
                while b"\r\n\r\n" not in head:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    head += chunk
                lines = head.decode("latin-1").split("\r\n")
                headers = {k.lower(): v for k, _, v in (line.partition(": ") for line in lines[1:] if line)}
                self.requests.append((lines[0], headers))
                if self.send_on_to:
                    conn.sendall(f"HTTP/1.1 418 I am a rogue\r\nX-Accel-Redirect: {self.send_on_to}\r\n"
                                 "Content-Length: 0\r\n\r\n".encode())
                    continue
                target = lines[0].split(" ")[1] if lines[0].count(" ") == 2 else ""
                if headers.get("upgrade", "").lower() != "websocket" or target.split("?")[0] != "/serial":
                    conn.sendall(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
                    continue
                accept = base64.b64encode(hashlib.sha1((headers["sec-websocket-key"] + WS_GUID).encode()).digest())
                conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                             b"Sec-WebSocket-Accept: " + accept + b"\r\n\r\n")
                text = self.name_sent.encode()
                conn.sendall(bytes([0x81, len(text)]) + text)
                time.sleep(0.2)


class Django(http.server.BaseHTTPRequestHandler):
    """ttsite.views.serial_ws: 200 with X-Accel-Redirect for a board some Pi reports, 404 otherwise."""

    def do_GET(self):  # noqa: N802 - http.server's name
        where = WHERE.get(self.path)
        self.send_response(200 if where else 404)
        if where:
            self.send_header("X-Accel-Redirect", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def ask(port, path, upgrade=True):
    """One request through nginx: (status line, the bytes after the response head)."""
    key = base64.b64encode(os.urandom(16)).decode()
    request = f"GET {path} HTTP/1.1\r\nHost: tinytapeout.test\r\n"
    if upgrade:
        request += f"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
    else:
        request += "Connection: close\r\n"
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall((request + "\r\n").encode())
        data = b""
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            head, sep, body = data.partition(b"\r\n\r\n")
            status = head.split(b"\r\n")[0].decode("latin-1")
            if sep and (" 101 " not in status or len(body) >= 2 and len(body) >= 2 + body[1]):
                return status, body
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    return head.split(b"\r\n")[0].decode("latin-1"), body


def unavailable(why):
    """No real nginx to test against: a skip on a developer's machine, a failure in CI, where a green run
    must mean the hand-off was tested."""
    if os.environ.get("CI"):
        pytest.fail(f"cannot run nginx in CI: {why}")
    pytest.skip(why)


@pytest.fixture(scope="module")
def site(tmp_path_factory):
    if not shutil.which("docker"):
        unavailable("needs docker to run a real nginx")
    tmp = tmp_path_factory.mktemp("ttsite-nginx")
    daemon_port, django_port, nginx_port = free_port(), free_port(), free_port()
    bridges = {address: Bridge(address, daemon_port) for address in (PI_A, PI_B)}
    bridges[PI_ROGUE] = Bridge(PI_ROGUE, daemon_port, send_on_to=f"/_tt-serial/{PI_A}")
    for bridge in bridges.values():
        bridge.start()
    django = http.server.ThreadingHTTPServer(("127.0.0.1", django_port), Django)
    threading.Thread(target=django.serve_forever, daemon=True).start()
    (tmp / "ws-boards.conf").write_text(render(daemon_port))
    # the shape of roles/ttsite/templates/vhost.conf.j2: the include, then everything else to Django
    (tmp / "nginx.conf").write_text(f"""
events {{}}
pid /tmp/nginx.pid;
http {{
    access_log off;
    server {{
        listen 127.0.0.1:{nginx_port};
        include /etc/nginx/test/ws-boards.conf;
        location / {{
            proxy_set_header Host $http_host;
            proxy_pass http://127.0.0.1:{django_port};
        }}
    }}
}}
""")
    name = f"ttsite-serial-handoff-{os.getpid()}"
    started = subprocess.run(
        ["docker", "run", "-d", "--name", name, "--network", "host",
         "-v", f"{tmp}:/etc/nginx/test:ro", IMAGE, "nginx", "-c", "/etc/nginx/test/nginx.conf", "-g", "daemon off;"],
        capture_output=True, text=True)
    if started.returncode != 0:
        unavailable(f"docker could not start {IMAGE}: {started.stderr.strip()}")
    try:
        deadline = time.monotonic() + 20
        while True:
            try:
                socket.create_connection(("127.0.0.1", nginx_port), timeout=1).close()
                break
            except OSError:
                if time.monotonic() > deadline:
                    logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                    raise AssertionError(f"nginx did not start: {logs.stdout}{logs.stderr}") from None
                time.sleep(0.2)
        yield nginx_port, bridges
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, text=True)
        django.shutdown()
        for bridge in bridges.values():
            bridge.sock.close()


def test_a_websocket_reaches_the_pi_django_named(site):
    port, bridges = site
    others_before = len(bridges[PI_B].requests)
    status, body = ask(port, "/ws/board/fpga-4/serial")
    assert " 101 " in status
    assert body[2:] == f"bridge at {PI_A}".encode()
    line, headers = bridges[PI_A].requests[-1]
    assert line.startswith("GET /serial ") and headers["upgrade"].lower() == "websocket"
    assert len(bridges[PI_B].requests) == others_before


def test_the_address_chooses_the_pi_not_the_slug(site):
    port, bridges = site
    status, body = ask(port, "/ws/board/tt-a2961e5cac65b25f/serial")
    assert " 101 " in status and body[2:] == f"bridge at {PI_B}".encode()


def test_a_board_django_does_not_know_is_its_404(site):
    port, _ = site
    status, _ = ask(port, "/ws/board/nope/serial")
    assert " 404 " in status


@pytest.mark.parametrize("path", [f"/_tt-serial/{PI_A}", f"/_tt-serial/{PI_A}/", "/_tt-serial/"])
def test_a_client_cannot_ask_for_the_internal_location(site, path):
    port, bridges = site
    before = len(bridges[PI_A].requests)
    for upgrade in (True, False):
        status, _ = ask(port, path, upgrade=upgrade)
        assert " 404 " in status, status
    assert len(bridges[PI_A].requests) == before


@pytest.mark.parametrize("slug", ["elsewhere", "not-an-address", "dot-is-a-dot"])
def test_an_address_outside_the_pi_network_is_not_proxied_to(site, slug):
    """Django derives the address from a Pi's registered name; nginx still takes nothing but a Pi address
    (and the dots of the network are dots: 127x0.2.13 is not on it)."""
    port, bridges = site
    before = sum(len(b.requests) for b in bridges.values())
    status, _ = ask(port, f"/ws/board/{slug}/serial")
    assert " 404 " in status, status
    assert sum(len(b.requests) for b in bridges.values()) == before


def test_digits_that_are_no_address_reach_no_pi(site):
    port, bridges = site
    before = sum(len(b.requests) for b in bridges.values())
    status, _ = ask(port, "/ws/board/no-such-octet/serial")
    assert " 502 " in status, status
    assert sum(len(b.requests) for b in bridges.values()) == before


def test_a_pis_answer_cannot_send_the_visitor_on_to_another_pi(site):
    """Visitors have root on the Pis: a bridge that answers with X-Accel-Redirect must not be followed."""
    port, bridges = site
    before = len(bridges[PI_A].requests)
    status, _ = ask(port, "/ws/board/rogue/serial")
    assert " 418 " in status, status
    assert bridges[PI_ROGUE].requests and len(bridges[PI_A].requests) == before


def test_the_rendered_file_names_no_board_and_no_port():
    text = render(8765)
    locations = [line for line in text.splitlines() if line.lstrip().startswith("location")]
    assert len(locations) == 1 and "/_tt-serial/" in locations[0]
    live = [line.strip() for line in text.splitlines() if not line.lstrip().startswith("#")]
    assert "internal;" in live and "proxy_ignore_headers X-Accel-Redirect;" in live
    assert "/ws/board/" not in "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "tt_boards" not in TEMPLATE.read_text()
