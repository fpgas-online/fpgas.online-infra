"""Network helpers for QEMU VM testing."""

import socket
import time


def wait_for_socket_listen(port: int, host: str = "127.0.0.1", timeout: int = 30) -> bool:
    """Wait for a TCP port to be listening (for QEMU socket networking sequencing)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def proxy_jump_string(user: str, host: str, port: int) -> str:
    """Generate a ProxyJump connection string."""
    return f"{user}@{host}:{port}"
