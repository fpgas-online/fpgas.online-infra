"""Which SSH login methods a server accepts (roles/sshd's public-key-only check)."""

import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

import paramiko


@dataclass
class AuthProbe:
    """What the server said to one user.

    allowed: the methods sshd offers after a "none" attempt (e.g.
    ["publickey", "password"]); password_ok / key_ok: whether that login
    succeeded (None when not tried).
    """

    allowed: list[str]
    password_ok: bool | None = None
    key_ok: bool | None = None


def _transport(host: str, port: int, timeout: float) -> paramiko.Transport:
    sock = socket.create_connection((host, port), timeout=timeout)
    transport = paramiko.Transport(sock)
    transport.start_client(timeout=timeout)
    return transport


def allowed_methods(host: str, port: int, username: str, timeout: float = 10) -> list[str]:
    """The authentication methods sshd offers username (RFC 4252 "none" request)."""
    transport = _transport(host, port, timeout)
    try:
        try:
            transport.auth_none(username)
        except paramiko.BadAuthenticationType as exc:
            return sorted(exc.allowed_types)
        return []  # "none" itself succeeded: no authentication at all
    finally:
        transport.close()


def password_login(host: str, port: int, username: str, password: str,
                   timeout: float = 10) -> bool:
    """True if a password login (no key, no agent) succeeds."""
    transport = _transport(host, port, timeout)
    try:
        try:
            transport.auth_password(username, password)
        except paramiko.AuthenticationException:
            return False
        return transport.is_authenticated()
    finally:
        transport.close()


def key_login(host: str, port: int, username: str, key_path: Path,
              timeout: float = 10) -> bool:
    """True if a login with the private key at key_path succeeds."""
    transport = _transport(host, port, timeout)
    try:
        try:
            transport.auth_publickey(username, paramiko.Ed25519Key.from_private_key_file(str(key_path)))
        except paramiko.AuthenticationException:
            return False
        return transport.is_authenticated()
    finally:
        transport.close()


def probe(host: str, port: int, username: str, password: str | None = None,
          key_path: Path | None = None) -> AuthProbe:
    """Ask for the offered methods, then try the password and/or key."""
    result = AuthProbe(allowed=allowed_methods(host, port, username))
    if password is not None:
        result.password_ok = password_login(host, port, username, password)
    if key_path is not None:
        result.key_ok = key_login(host, port, username, key_path)
    return result


def openssh_password_attempt(host: str, port: int, username: str) -> str:
    """What the OpenSSH client says when it may only use a password.

    BatchMode stops it prompting, so against a public-key-only server it
    fails with "Permission denied (publickey)." -- the methods the server
    offers, in the form a person trying it would see.
    """
    proc = subprocess.run(
        ["ssh", "-p", str(port),
         "-o", "BatchMode=yes",
         "-o", "PreferredAuthentications=password,keyboard-interactive",
         "-o", "PubkeyAuthentication=no",
         "-o", "StrictHostKeyChecking=no",
         "-o", "UserKnownHostsFile=/dev/null",
         "-o", "ConnectTimeout=10",
         f"{username}@{host}", "true"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
    )
    return f"rc={proc.returncode} {proc.stderr.strip()}"
