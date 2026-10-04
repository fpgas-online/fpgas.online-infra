"""The lab itself: images, networks, containers and the helpers that run
commands in them. Imported by sshd_native_routing_lab.py (same directory).

Everything is a Docker container on two private, internal networks. Nothing
outside them is contacted. Addresses are shaped like a site's:

    uplink  172.31.223.0/24 + fd5a:6b74:7971::/64   visitors, the gateway
    boards  10.121.0.0/16                           the gateway, the boards
            a board named pi-sw<S>-p<P> is at 10.121.<S>.<P>

(A real site uses 10.21.<S>.<P>. The lab uses 10.121 because the machine
that runs it may have a route to a real site's 10.21.0.0/16.)
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORK = REPO / "tmp" / "sshd_native_routing"
KEYS = WORK / "keys"
IMAGES_BEFORE = WORK / "images-before.txt"

PREFIX = "fo-snr"
NET_UP = f"{PREFIX}-uplink"
NET_BOARDS = f"{PREFIX}-boards"
UP4 = "172.31.223"
UP6 = "fd5a:6b74:7971::"
BNET = "10.121"
SITE = "welland.lab"
GW = f"{PREFIX}-gw"
GW_UP4 = f"{UP4}.10"
GW_UP6 = f"{UP6}10"
GW_BOARDS = f"{BNET}.0.1"
BOARD_PASSWORD = "lab-public-board-password"
ADMIN = "alice"

IMG_GW = f"{PREFIX}-gateway"
IMG_CLIENT = f"{PREFIX}-client"
IMG_BOARD = {"bookworm": f"{PREFIX}-board-bookworm", "trixie": f"{PREFIX}-board-trixie"}


@dataclasses.dataclass(frozen=True)
class Board:
    name: str
    dist: str
    what: str
    env: tuple[tuple[str, str], ...] = ()

    @property
    def container(self) -> str:
        return f"{PREFIX}-{self.name}"

    @property
    def ip(self) -> str:
        sw, port = self.name.removeprefix("pi-sw").split("-p")
        return f"{BNET}.{sw}.{port}"


BOARDS = {b.name: b for b in [
    Board("pi-sw2-p47", "bookworm", "an ordinary board (OpenSSH 9.2)"),
    Board("pi-sw1-p5", "bookworm", "a second ordinary board, on the other switch"),
    Board("pi-sw2-p46", "trixie", "a future board (OpenSSH 10.0, PerSourcePenalties on)"),
    Board("pi-sw2-p45", "bookworm", "a board presenting a host key that is not the fleet key",
          (("HOST_KEY", "other_key"),)),
    Board("pi-sw2-p44", "bookworm", "a board whose root changed pi's password",
          (("PI_PASSWORD", "changed-by-a-visitor"),)),
    Board("pi-sw2-p43", "trixie", "a future board whose root changed pi's password",
          (("PI_PASSWORD", "changed-by-a-visitor"),)),
    Board("pi-sw2-p42", "trixie", "as p43, but exempting the gateway from penalties",
          (("PI_PASSWORD", "changed-by-a-visitor"), ("EXEMPT", GW_BOARDS))),
]}
EMPTY_PORT = "pi-sw2-p40"  # no container at 10.121.2.40

CLIENTS = {"a": f"{UP4}.21", "b": f"{UP4}.22"}  # a: the visitor (and abuser); b: another source


def client(which: str) -> str:
    return f"{PREFIX}-cli-{which}"


@dataclasses.dataclass
class Result:
    argv: list[str]
    rc: int
    out: str
    err: str
    secs: float

    @property
    def text(self) -> str:
        return (self.out + self.err).strip()


def run(argv: list[str], *, check: bool = True, input: str | None = None, timeout: float = 900) -> Result:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv, input=input, text=True, capture_output=True, timeout=timeout, check=False,
            stdin=None if input is not None else subprocess.DEVNULL, errors="replace",
        )
        res = Result(argv, proc.returncode, proc.stdout, proc.stderr, time.monotonic() - start)
    except subprocess.TimeoutExpired as exc:
        out = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        res = Result(argv, -999, out, err + f"\n[lab: timed out after {timeout}s]", time.monotonic() - start)
    if check and res.rc != 0:
        raise RuntimeError(f"command failed ({res.rc}): {argv}\nstdout: {res.out}\nstderr: {res.err}")
    return res


def dexec(container: str, argv: list[str], *, env: dict[str, str] | None = None, input: str | None = None,
          check: bool = False, timeout: float = 120, user: str | None = None) -> Result:
    cmd = ["docker", "exec"]
    if input is not None:
        cmd.append("-i")
    if user:
        cmd += ["-u", user]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    return run([*cmd, container, *argv], check=check, input=input, timeout=timeout)


# --------------------------------------------------------------------------
# Construction


def make_keys() -> None:
    KEYS.mkdir(parents=True, exist_ok=True)
    for name in ("fleet_key", "other_key", "gateway_key", "admin_key"):
        if not (KEYS / name).exists():
            run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"lab-{name}", "-f", str(KEYS / name)])


def fingerprint(name: str) -> str:
    return run(["ssh-keygen", "-l", "-f", str(KEYS / f"{name}.pub")]).out.split()[1]


def existing_images() -> set[str]:
    return set(run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]).out.split())


def build_images() -> None:
    WORK.mkdir(parents=True, exist_ok=True)
    if not IMAGES_BEFORE.exists():
        IMAGES_BEFORE.write_text("\n".join(sorted(existing_images())) + "\n")
    ctx = WORK / "ctx"
    if ctx.exists():
        shutil.rmtree(ctx)
    shutil.copytree(HERE / "files", ctx)
    shutil.copytree(HERE / "src", ctx / "src")
    run(["docker", "build", "-q", "-t", IMG_GW, "-f", str(ctx / "Dockerfile.gateway"), str(ctx)])
    run(["docker", "build", "-q", "-t", IMG_CLIENT, "-f", str(ctx / "Dockerfile.client"), str(ctx)])
    for dist, tag in IMG_BOARD.items():
        run(["docker", "build", "-q", "-t", tag, "--build-arg", f"BASE=amd64/debian:{dist}",
             "-f", str(ctx / "Dockerfile.board"), str(ctx)])


def start_networks() -> None:
    run(["docker", "network", "create", "--internal", "--ipv6",
         "--subnet", f"{UP4}.0/24", "--subnet", f"{UP6}/64", NET_UP])
    run(["docker", "network", "create", "--internal",
         "--subnet", f"{BNET}.0.0/16", "--gateway", f"{BNET}.255.254", NET_BOARDS])


def start_board(board: Board) -> None:
    env = {"PI_PASSWORD": BOARD_PASSWORD, **dict(board.env)}
    cmd = ["docker", "run", "-d", "--name", board.container, "--hostname", board.name,
           "--network", NET_BOARDS, "--ip", board.ip, "-v", f"{KEYS}:/lab/keys:ro"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    run([*cmd, IMG_BOARD[board.dist]])


def start_clients() -> None:
    for which, ip in CLIENTS.items():
        run(["docker", "run", "-d", "--name", client(which), "--hostname", f"client-{which}",
             "--network", NET_UP, "--ip", ip, "--ip6", f"{UP6}{ip.rsplit('.', 1)[1]}", "--cap-add", "NET_ADMIN",
             "--add-host", f"{SITE}:{GW_UP4}", "--add-host", f"v6.{SITE}:{GW_UP6}",
             "-v", f"{KEYS}:/lab/keys:ro", IMG_CLIENT, "sleep", "infinity"])


MANY_FIRST, MANY_LAST = 100, 159  # extra source addresses on client a, for a many-source flood


def add_source_addresses() -> None:
    script = f"i={MANY_FIRST}; while [ $i -le {MANY_LAST} ]; do ip addr add {UP4}.$i/24 dev eth0; i=$((i+1)); done"
    dexec(client("a"), ["sh", "-c", script], check=True)


def container_running(name: str) -> bool:
    res = run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    return res.rc == 0 and res.out.strip() == "true"


def start_gateway(*, lookup: str = "extrausers", variant: str = "password", sandbox: str = "chroot",
                  nft: bool = True, startups: str = "default", pam: str = "dedicated",
                  extra_env: dict[str, str] | None = None,
                  security_opts: tuple[str, ...] = ()) -> None:
    """(Re)start the gateway with one lookup mechanism and one hand-off variant."""
    run(["docker", "rm", "-f", GW], check=False)
    env = {"LOOKUP": lookup, "VARIANT": variant, "SANDBOX": sandbox, "NFT": "1" if nft else "0",
           "STARTUPS": startups, "PAM": pam, "NET": BNET, "SWITCH_PORTS": "48 48", "BOARD_PASSWORD": BOARD_PASSWORD,
           **(extra_env or {})}
    cmd = ["docker", "create", "--name", GW, "--hostname", "gw", "--network", NET_UP,
           "--ip", GW_UP4, "--ip6", GW_UP6, "--cap-add", "NET_ADMIN", "-v", f"{KEYS}:/lab/keys:ro"]
    for opt in security_opts:
        cmd += ["--security-opt", opt]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    run([*cmd, IMG_GW])
    run(["docker", "network", "connect", "--ip", GW_BOARDS, NET_BOARDS, GW])
    run(["docker", "start", GW])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if not container_running(GW):
            logs = run(["docker", "logs", GW], check=False)
            raise RuntimeError(f"gateway exited:\n{logs.out}\n{logs.err}")
        if dexec(GW, ["nc", "-z", "-w", "1", "127.0.0.1", "22"]).rc == 0:
            return
        time.sleep(0.5)
    raise RuntimeError("gateway sshd did not start listening")


def start_lab() -> None:
    make_keys()
    start_networks()
    for board in BOARDS.values():
        start_board(board)
    start_clients()
    add_source_addresses()
    time.sleep(2)
    for board in BOARDS.values():
        if not container_running(board.container):
            logs = run(["docker", "logs", board.container], check=False)
            raise RuntimeError(f"board {board.name} is not running:\n{logs.out}\n{logs.err}")


def teardown(remove_images: bool = True) -> None:
    names = run(["docker", "ps", "-a", "--filter", f"name=^{PREFIX}-", "--format", "{{.Names}}"]).out.split()
    if names:
        run(["docker", "rm", "-f", *names])
    for net in (NET_UP, NET_BOARDS):
        if run(["docker", "network", "inspect", net], check=False).rc == 0:
            run(["docker", "network", "rm", net])
    if remove_images:
        ours = [IMG_GW, IMG_CLIENT, *IMG_BOARD.values()]
        present = [t for t in ours if f"{t}:latest" in existing_images()]
        if present:
            run(["docker", "rmi", *present])
        # Base images this lab pulled (and only those) go too.
        before = set(IMAGES_BEFORE.read_text().split()) if IMAGES_BEFORE.exists() else None
        for base in ("amd64/debian:trixie", "amd64/debian:bookworm"):
            if before is not None and base not in before and base in existing_images():
                run(["docker", "rmi", base])
        if IMAGES_BEFORE.exists():
            IMAGES_BEFORE.unlink()


# --------------------------------------------------------------------------
# What a visitor's machine runs


def ssh_env(password: str | None) -> dict[str, str]:
    """No terminal: ssh asks through the recording askpass helper."""
    return {"SSH_ASKPASS": "/usr/local/bin/askpass", "SSH_ASKPASS_REQUIRE": "force",
            "LAB_PASSWORD": password if password is not None else "", "DISPLAY": ""}


def visitor(argv: list[str], *, password: str | None = BOARD_PASSWORD, which: str = "a",
            input: str | None = None, timeout: float = 60, env: dict[str, str] | None = None) -> Result:
    """Run a client program (ssh, scp, sftp, rsync, sh) in a client container, with no terminal."""
    return dexec(client(which), argv, env={**ssh_env(password), **(env or {})}, input=input, timeout=timeout)


def prompts(which: str = "a") -> list[str]:
    """What ssh asked since the last call (and forget it)."""
    res = dexec(client(which), ["sh", "-c", "cat /root/askpass.log; rm -f /root/askpass.log"])
    return [p.strip() for p in res.out.split("----") if p.strip()] if res.rc == 0 else []


def pty(spec: dict, which: str = "a", timeout: float = 120) -> dict:
    """Run on a real terminal in the client container; see files/ptyrun.py."""
    res = dexec(client(which), ["python3", "/usr/local/bin/ptyrun.py", "-"], input=json.dumps(spec), timeout=timeout)
    if res.rc != 0:
        raise RuntimeError(f"ptyrun failed: {res.err}")
    return json.loads(res.out)


def gw_log_mark() -> int:
    return len(run(["docker", "logs", GW], check=False).err.splitlines())


def gw_log_since(mark: int) -> list[str]:
    return run(["docker", "logs", GW], check=False).err.splitlines()[mark:]


def board_log(name: str) -> list[str]:
    return run(["docker", "logs", BOARDS[name].container], check=False).err.splitlines()
