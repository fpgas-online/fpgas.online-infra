"""tests/vm/ssh_auth.py against an in-process paramiko server.

The VM test uses these probes to prove the server VM refuses passwords
after the converge (roles/sshd); here they are checked against a server
whose offered methods are known, both password-enabled and key-only.
"""

import socket
import subprocess
import threading
from pathlib import Path

import paramiko
import pytest

from tests.vm.ssh_auth import allowed_methods, openssh_password_attempt, probe

PASSWORD = "s3cret"


class _Server(paramiko.ServerInterface):
    def __init__(self, methods: list[str], client_key: paramiko.PKey):
        self.methods = methods
        self.client_key = client_key

    def get_allowed_auths(self, username):
        return ",".join(self.methods)

    def check_auth_password(self, username, password):
        if "password" in self.methods and password == PASSWORD:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_auth_publickey(self, username, key):
        if "publickey" in self.methods and key == self.client_key:
            return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED


@pytest.fixture(scope="module")
def host_key():
    return paramiko.RSAKey.generate(2048)


@pytest.fixture(scope="module")
def client_key(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("key") / "id_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(path)], check=True)
    return path


def _serve(methods: list[str], host_key, client_key: Path, connections: int) -> int:
    """Serve `connections` SSH connections on an ephemeral port; return it."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(connections)
    pkey = paramiko.Ed25519Key.from_private_key_file(str(client_key))

    def loop():
        for _ in range(connections):
            conn, _addr = listener.accept()
            transport = paramiko.Transport(conn)
            transport.add_server_key(host_key)
            transport.start_server(server=_Server(methods, pkey))
        listener.close()

    threading.Thread(target=loop, daemon=True).start()
    return listener.getsockname()[1]


def test_password_enabled_server(host_key, client_key):
    port = _serve(["publickey", "password"], host_key, client_key, connections=3)
    result = probe("127.0.0.1", port, "debian", password=PASSWORD, key_path=client_key)
    assert result.allowed == ["password", "publickey"]
    assert result.password_ok is True
    assert result.key_ok is True


def test_key_only_server(host_key, client_key):
    port = _serve(["publickey"], host_key, client_key, connections=3)
    result = probe("127.0.0.1", port, "debian", password=PASSWORD, key_path=client_key)
    assert result.allowed == ["publickey"]
    assert result.password_ok is False
    assert result.key_ok is True


def test_wrong_key_refused(host_key, client_key, tmp_path):
    other = tmp_path / "other"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(other)], check=True)
    port = _serve(["publickey"], host_key, client_key, connections=3)
    assert allowed_methods("127.0.0.1", port, "debian") == ["publickey"]
    assert probe("127.0.0.1", port, "debian", key_path=other).key_ok is False


@pytest.mark.parametrize("methods, expected", [
    (["publickey"], "Permission denied (publickey)."),
    (["publickey", "password"], "Permission denied (publickey,password)."),
])
def test_openssh_client_message(host_key, client_key, methods, expected):
    port = _serve(methods, host_key, client_key, connections=1)
    assert expected in openssh_password_attempt("127.0.0.1", port, "debian")
