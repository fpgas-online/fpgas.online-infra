#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Lab proof of the "key-types trick" for one ssh name answered by two proxies.

One name (`welland.fpgas.online`, port 22) is answered by two different
sshpiper proxies with different host keys: one over IPv4, one over IPv6.
The claim under test: if proxy A presents only an ECDSA host key, proxy B
presents only an Ed25519 host key, and neither forwards the
`hostkeys-00@openssh.com` announcement (`--drop-hostkeys-message`), then an
OpenSSH client keeps both keys under the one name and never shows the
"REMOTE HOST IDENTIFICATION HAS CHANGED" refusal.

Everything runs in Docker containers on one private, internal network.
Nothing outside that network is contacted by ssh. See README.md.

Run:  uv run tests/lab/ssh_key_types/ssh_key_types_lab.py
"""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import datetime
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
WORK = REPO / "tmp" / "ssh_key_types"
IMAGES_BEFORE = WORK / "images-before.txt"

SSHPIPER_REPO = "https://github.com/fpgas-online/sshpiper"
SSHPIPER_COMMIT = "2038d993852bd0c788d32f453f3f15da18a8e701"

PREFIX = "fo-sshkt"
NETWORK = f"{PREFIX}-net"
V4_NET = "172.31.222"
V6_NET = "fd5a:6b74:7970::"
NAME = "welland.fpgas.online"
USER = "pi-sw2-p47"
PASSWORD = "lab-only-password"

# Client images: tag -> (base image, extra packages).
CLIENTS = {
    "ubuntu-20.04": ("ubuntu:20.04", ""),
    "ubuntu-22.04": ("ubuntu:22.04", ""),
    "debian-bookworm": ("debian:bookworm", ""),
    "ubuntu-24.04": ("ubuntu:24.04", ""),
    "debian-trixie": ("debian:trixie", "dropbear-bin putty-tools"),
    "debian-sid": ("debian:sid", ""),
}
OTHER_CLIENTS_IN = "debian-trixie"  # dbclient and plink are run from this image

SERVER_DOCKERFILE = f"""\
FROM debian:trixie-slim
RUN apt-get update \\
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-server \\
 && rm -rf /var/lib/apt/lists/* \\
 && mkdir -p /run/sshd \\
 && useradd -m -s /bin/sh {USER} \\
 && echo '{USER}:{PASSWORD}' | chpasswd
"""

CLIENT_DOCKERFILE = """\
ARG BASE
FROM ${BASE}
ARG EXTRA=""
RUN apt-get update \\
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends openssh-client ${EXTRA} \\
 && rm -rf /var/lib/apt/lists/*
COPY askpass /usr/local/bin/askpass
RUN chmod 755 /usr/local/bin/askpass
"""

# Answers ssh's questions without a terminal and records every question, so
# the transcript shows exactly what a visitor would have been asked.
ASKPASS = """\
#!/bin/sh
printf '%s\\n----\\n' "$1" >> /root/askpass.log
case "$1" in
  *"continue connecting"*) printf '%s\\n' "${LAB_HOSTKEY_ANSWER:-yes}" ;;
  *assword*) printf '%s\\n' "$LAB_PASSWORD" ;;
  *) printf '\\n' ;;
esac
"""

INTERESTING = re.compile(
    r"Server host key|kex: host key algorithm|debug2: host key algorithms|hostkeys|"
    r"is known and matches|Permanently added|REMOTE HOST IDENTIFICATION|Offending|"
    r"Host key verification failed|no matching host key|authenticity of host|"
    r"host key is known|has changed|update_known_hosts|[Dd]eprecat|"
    r"Learned new hostkey|key fingerprint is|Add correct host key|strict checking|"
    r"POSSIBLE|man-in-the-middle|not in the list|different|Connection (closed|abandoned)|"
    r"FATAL|Store key|cache|WARNING|[Hh]ost key|mismatch|\(y/n|bad signature|Removed"
)


def now() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


class Log:
    def __init__(self, path: Path):
        self.f = path.open("w")
        self.lock = threading.Lock()

    def write(self, text: str, echo: bool = False) -> None:
        with self.lock:
            self.f.write(text + "\n")
            self.f.flush()
            if echo:
                print(text, flush=True)


LOG: Log


def run(argv: list[str], *, check: bool = True, input: str | None = None, timeout: int = 900) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        argv, input=input, text=True, capture_output=True, timeout=timeout,
        stdin=None if input is not None else subprocess.DEVNULL,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"command failed ({proc.returncode}): {argv}\nstdout: {proc.stdout}\nstderr: {proc.stderr}")
    return proc


# --------------------------------------------------------------------------
# Lab construction
# --------------------------------------------------------------------------

@dataclasses.dataclass
class Server:
    name: str
    index: int
    describe: str
    command: list[str]

    @property
    def container(self) -> str:
        return f"{PREFIX}-srv-{self.name}"

    @property
    def v4(self) -> str:
        return f"{V4_NET}.{self.index}"

    @property
    def v6(self) -> str:
        return f"{V6_NET}{self.index:x}"


BACKEND = Server("backend", 10, "plain sshd the proxies route to", ["/usr/sbin/sshd", "-D", "-e"])


def piper(name: str, index: int, describe: str, key_glob: str, drop: bool) -> Server:
    cmd = ["/opt/lab/sshpiperd", "--address", "::", "--port", "22", "--server-key", key_glob]
    if drop:
        cmd.append("--drop-hostkeys-message")
    cmd += ["/opt/lab/fixed", "--target", f"{BACKEND.v4}:22"]
    return Server(name, index, describe, cmd)


def sshd(name: str, index: int, describe: str, key: str) -> Server:
    return Server(name, index, describe, ["/usr/sbin/sshd", "-D", "-e", "-h", key])


SERVERS = {s.name: s for s in [
    BACKEND,
    piper("pA", 21, "sshpiper, ECDSA key only, --drop-hostkeys-message", "/keys/a_ecdsa", True),
    piper("pB", 22, "sshpiper, Ed25519 key only, --drop-hostkeys-message", "/keys/b_ed25519", True),
    piper("pA-fwd", 23, "sshpiper, ECDSA key only, announcement NOT dropped", "/keys/a_ecdsa", False),
    piper("pB-fwd", 24, "sshpiper, Ed25519 key only, announcement NOT dropped", "/keys/b_ed25519", False),
    piper("pB-ecdsa", 25, "sshpiper, a different ECDSA key, --drop-hostkeys-message", "/keys/b_ecdsa", True),
    piper("pB-both", 26, "sshpiper, its own ECDSA and Ed25519 keys, --drop-hostkeys-message", "/keys/both_*_key", True),
    sshd("sA", 31, "plain sshd, ECDSA key only (announces its keys)", "/keys/a_ecdsa"),
    sshd("sB", 32, "plain sshd, Ed25519 key only (announces its keys)", "/keys/b_ed25519"),
]}

KEYS = {
    "a_ecdsa": "ecdsa", "b_ed25519": "ed25519", "b_ecdsa": "ecdsa",
    "both_ecdsa_key": "ecdsa", "both_ed25519_key": "ed25519",
}


def build_sshpiper() -> None:
    bindir = WORK / "bin"
    if (bindir / "sshpiperd").exists() and (bindir / "fixed").exists():
        return
    src = WORK / "sshpiper"
    if not src.exists():
        run(["git", "clone", SSHPIPER_REPO, str(src)])
    run(["git", "-C", str(src), "checkout", "--detach", SSHPIPER_COMMIT])
    bindir.mkdir(parents=True, exist_ok=True)
    env_go = ["env", "CGO_ENABLED=0", "go", "build"]
    run([*env_go, "-C", str(src / "cmd" / "sshpiperd"), "-o", str(bindir / "sshpiperd"), "."])
    run([*env_go, "-C", str(src), "-tags", "full", "-o", str(bindir / "fixed"), "./plugin/fixed"])


def make_keys() -> dict[str, str]:
    """Generate the lab host keys; return base64 blob -> label."""
    keydir = WORK / "keys"
    keydir.mkdir(parents=True, exist_ok=True)
    labels = {}
    for name, kind in KEYS.items():
        path = keydir / name
        if not path.exists():
            run(["ssh-keygen", "-q", "-t", kind, "-N", "", "-C", f"lab-{name}", "-f", str(path)])
        labels[(keydir / f"{name}.pub").read_text().split()[1]] = name
    return labels


def existing_images() -> set[str]:
    out = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"]).stdout
    return set(out.split())


def build_images(clients: list[str]) -> None:
    ctx = WORK / "ctx"
    ctx.mkdir(parents=True, exist_ok=True)
    (ctx / "askpass").write_text(ASKPASS)
    (ctx / "Dockerfile.server").write_text(SERVER_DOCKERFILE)
    (ctx / "Dockerfile.client").write_text(CLIENT_DOCKERFILE)
    run(["docker", "build", "-q", "-t", f"{PREFIX}-server", "-f", str(ctx / "Dockerfile.server"), str(ctx)])
    for tag in clients:
        base, extra = CLIENTS[tag]
        run(["docker", "build", "-q", "-t", f"{PREFIX}-client-{tag}", "--build-arg", f"BASE={base}",
             "--build-arg", f"EXTRA={extra}", "-f", str(ctx / "Dockerfile.client"), str(ctx)])


def start_lab(clients: list[str]) -> None:
    run(["docker", "network", "create", "--internal", "--ipv6",
         "--subnet", f"{V4_NET}.0/24", "--subnet", f"{V6_NET}/64", NETWORK])
    for s in SERVERS.values():
        run(["docker", "run", "-d", "--name", s.container, "--network", NETWORK,
             "--ip", s.v4, "--ip6", s.v6,
             "-v", f"{WORK / 'keys'}:/keys:ro", "-v", f"{WORK / 'bin'}:/opt/lab:ro",
             f"{PREFIX}-server", *s.command])
    for tag in clients:
        run(["docker", "run", "-d", "--name", f"{PREFIX}-cli-{tag}", "--network", NETWORK,
             f"{PREFIX}-client-{tag}", "sleep", "infinity"])
    time.sleep(2)
    for s in SERVERS.values():
        state = run(["docker", "inspect", "-f", "{{.State.Running}}", s.container]).stdout.strip()
        if state != "true":
            logs = run(["docker", "logs", s.container], check=False)
            raise RuntimeError(f"server {s.name} is not running:\n{logs.stdout}\n{logs.stderr}")


def note_images_before() -> None:
    """Remember which images existed before the first run, so cleanup removes only what the lab added."""
    if not IMAGES_BEFORE.exists():
        WORK.mkdir(parents=True, exist_ok=True)
        IMAGES_BEFORE.write_text("\n".join(sorted(existing_images())) + "\n")


def stop_lab() -> None:
    names = run(["docker", "ps", "-aq", "--filter", f"name={PREFIX}-"]).stdout.split()
    if names:
        run(["docker", "rm", "-f", *names])
    if run(["docker", "network", "ls", "-q", "--filter", f"name={NETWORK}"]).stdout.strip():
        run(["docker", "network", "rm", NETWORK])


def remove_images() -> None:
    """Remove the lab's own images and the base images it pulled; leave every image that was there before."""
    before = set(IMAGES_BEFORE.read_text().split())
    ours = {i for i in existing_images() if i.startswith(f"{PREFIX}-")}
    pulled = {base for base, _ in CLIENTS.values()} & existing_images() - before
    for image in sorted(ours | pulled):
        run(["docker", "rmi", image])
    IMAGES_BEFORE.unlink()


# --------------------------------------------------------------------------
# Driving a client
# --------------------------------------------------------------------------

@dataclasses.dataclass
class Obs:
    client: str
    scenario: str
    step: str
    target: str
    outcome: str
    rc: int
    prompts: list[str]
    negotiated: str
    proposal: str
    announcements: int
    bad_signature: bool
    known_hosts: list[str]
    expect: str | None
    lines: list[str]

    @property
    def ok(self) -> bool | None:
        return None if self.expect is None else self.outcome in self.expect.split("|")


class Client:
    def __init__(self, tag: str, key_labels: dict[str, str]):
        self.tag = tag
        self.container = f"{PREFIX}-cli-{tag}"
        self.key_labels = key_labels
        self.version = self.exec(["ssh", "-V"]).stderr.strip()
        m = re.search(r"OpenSSH_(\d+)\.(\d+)", self.version)
        self.ver = (int(m.group(1)), int(m.group(2)))
        self.obs: list[Obs] = []

    def exec(self, argv: list[str], env: dict[str, str] | None = None, input: str | None = None) -> subprocess.CompletedProcess:
        cmd = ["docker", "exec"]
        if input is not None:
            cmd.append("-i")
        for k, v in (env or {}).items():
            cmd += ["-e", f"{k}={v}"]
        return run([*cmd, self.container, *argv], check=False, input=input, timeout=120)

    def fresh(self) -> None:
        self.exec(["rm", "-rf", "/root/.ssh", "/root/.putty", "/root/askpass.log"])

    def point(self, address: str) -> None:
        hosts = f"127.0.0.1 localhost\n::1 localhost\n{address} {NAME}\n"
        self.exec(["tee", "/etc/hosts"], input=hosts)

    def known_hosts(self, path: str = "/root/.ssh/known_hosts") -> list[str]:
        proc = self.exec(["cat", path])
        if proc.returncode != 0:
            return []
        out = []
        for line in proc.stdout.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            host = "<hashed>" if parts[0].startswith("|1|") else parts[0]
            out.append(f"{host} {parts[1]} {self.key_labels.get(parts[2], 'OTHER:' + parts[2][-12:])}")
        return out

    def seed_known_hosts(self, keys: list[str], host: str = NAME) -> None:
        """Put published keys in known_hosts before any connection, as a visitor pasting lines would."""
        self.exec(["mkdir", "-p", "-m", "700", "/root/.ssh"])
        text = "".join(f"{host} {' '.join((WORK / 'keys' / f'{k}.pub').read_text().split()[:2])}\n" for k in keys)
        self.exec(["tee", "/root/.ssh/known_hosts"], input=text)
        LOG.write(f"--- {self.tag}: known_hosts pre-seeded with:\n{text}")

    def forget(self) -> None:
        """What the CHANGED refusal tells the visitor to run."""
        proc = self.exec(["ssh-keygen", "-R", NAME])
        LOG.write(f"--- {self.tag}: $ ssh-keygen -R {NAME}\n{proc.stdout}{proc.stderr}"
                  f"known_hosts after: {'; '.join(self.known_hosts()) or '(empty)'}\n")

    def connect(self, scenario: str, server: str, family: int, *, opts: list[str] | None = None,
                answer: str = "yes", expect: str | None = None, hold: bool = False) -> Obs:
        """One `ssh pi-sw2-p47@welland.fpgas.online` with the name pointing at one proxy."""
        s = SERVERS[server]
        address = s.v4 if family == 4 else s.v6
        self.point(address)
        self.exec(["rm", "-f", "/root/askpass.log"])
        remote = "sleep 1; echo CONNECTED" if hold else "echo CONNECTED"
        argv = ["ssh", "-vv", *(x for o in (opts or []) for x in ("-o", o)), f"{USER}@{NAME}", remote]
        env = {"DISPLAY": "none", "SSH_ASKPASS": "/usr/local/bin/askpass", "SSH_ASKPASS_REQUIRE": "force",
               "LAB_HOSTKEY_ANSWER": answer, "LAB_PASSWORD": PASSWORD}
        proc = self.exec(argv, env)
        asked = self.exec(["cat", "/root/askpass.log"]).stdout
        prompts = [p.strip() for p in asked.split("----\n") if "continue connecting" in p]
        err = proc.stderr
        connected = "CONNECTED" in proc.stdout
        if "REMOTE HOST IDENTIFICATION HAS CHANGED" in err:
            outcome = "REFUSED-CHANGED"
        elif connected and prompts:
            outcome = "PROMPT-NEW"
        elif connected and re.search(r"Permanently added '[^']*' \(", err):
            outcome = "AUTO-ADDED"
        elif connected and "Permanently added" in err:
            outcome = "ADDED-IP-NOTE"  # pre-8.5 CheckHostIP: known key also recorded under the address
        elif connected:
            outcome = "SILENT"
        elif "no matching host key type" in err:
            outcome = "NO-COMMON-ALGO"
        elif "Host key verification failed" in err:
            outcome = "REFUSED-UNKNOWN"
        else:
            outcome = "FAIL-OTHER"
        negotiated = re.search(r"kex: host key algorithm: (\S+)", err)
        proposal = re.search(r"debug2: host key algorithms: (\S+)", err)
        step = f"{len([o for o in self.obs if o.scenario == scenario]) + 1}"
        obs = Obs(
            client=self.tag, scenario=scenario, step=step,
            target=f"{server} over IPv{family}", outcome=outcome, rc=proc.returncode, prompts=prompts,
            negotiated=negotiated.group(1) if negotiated else "",
            proposal=",".join(proposal.group(1).split(",")[:4]) if proposal else "",
            announcements=len(re.findall(r"client_input_global_request: rtype hostkeys-00@openssh.com", err)),
            bad_signature="server gave bad signature" in err,
            known_hosts=self.known_hosts(), expect=expect,
            lines=[ln for ln in err.splitlines() if INTERESTING.search(ln) and "debug3" not in ln],
        )
        self.record(obs, argv, opts)
        (WORK / "logs" / f"{self.tag}--{scenario}--{step}.stderr").write_text(err)
        return obs

    def record(self, obs: Obs, argv: list[str], opts: list[str] | None) -> None:
        self.obs.append(obs)
        verdict = {None: "", True: "  [as expected]", False: f"  [UNEXPECTED, expected {obs.expect}]"}[obs.ok]
        text = [
            f"=== {self.tag} ({self.version}) | {obs.scenario} step {obs.step} | {NAME} -> {obs.target}",
            f"$ {' '.join(argv)}",
            f"outcome: {obs.outcome} (exit {obs.rc}){verdict}",
        ]
        if obs.proposal:
            text.append(f"client proposal starts: {obs.proposal}")
        if obs.negotiated:
            text.append(f"negotiated host key algorithm: {obs.negotiated}")
        text.append(f"hostkeys-00 announcements received: {obs.announcements}")
        for p in obs.prompts:
            text.append("client asked:\n    " + p.replace("\n", "\n    "))
        text += [f"  stderr| {ln}" for ln in obs.lines]
        text.append("known_hosts after: " + ("; ".join(obs.known_hosts) or "(empty)"))
        LOG.write("\n".join(text) + "\n")

    def other(self, scenario: str, tool: str, server: str, family: int, argv: list[str], *,
              env: dict[str, str] | None = None, input: str | None = None, state: list[str]) -> Obs:
        """One connection with a non-OpenSSH client (dbclient, plink)."""
        s = SERVERS[server]
        self.point(s.v4 if family == 4 else s.v6)
        proc = self.exec(argv, env, input=input if input is not None else "")
        connected = "CONNECTED" in proc.stdout
        text = proc.stderr + proc.stdout
        asked = bool(re.search(r"\(y/n|Store key in cache|Do you want to continue connecting", text))
        mismatch = bool(re.search(r"mismatch|POTENTIAL SECURITY BREACH|does not match|HOST KEY.*CHANGED|differs", text, re.I))
        if mismatch and not connected:
            outcome = "REFUSED-CHANGED"
        elif mismatch:
            outcome = "WARNED-CHANGED-THEN-CONNECTED"
        elif connected and asked:
            outcome = "PROMPT-NEW"
        elif connected:
            outcome = "SILENT"
        else:
            outcome = "REFUSED-UNKNOWN" if asked else "FAIL-OTHER"
        step = f"{len([o for o in self.obs if o.scenario == scenario]) + 1}"
        stored = self.exec(["cat", *state]).stdout.splitlines()
        short = []
        for line in stored:
            parts = line.split()
            label = next((self.key_labels[p] for p in parts if p in self.key_labels), None)
            short.append(f"{parts[0]} {label or line[:70]}" if parts else "")
        obs = Obs(client=tool, scenario=scenario, step=step, target=f"{server} over IPv{family}", outcome=outcome,
                  rc=proc.returncode, prompts=[], negotiated="", proposal="", announcements=0, bad_signature=False,
                  known_hosts=short,
                  expect=None, lines=[ln for ln in text.splitlines() if ln.strip() and "CONNECTED" not in ln][:40])
        self.obs.append(obs)
        LOG.write("\n".join([
            f"=== {tool} (in {self.tag}) | {scenario} step {step} | {NAME} -> {obs.target}",
            f"$ {' '.join(argv)}   (stdin: {input!r})",
            f"outcome: {outcome} (exit {proc.returncode})",
            *[f"  output| {ln}" for ln in obs.lines],
            "stored host keys after: " + ("; ".join(short) or "(empty)"),
        ]) + "\n")
        return obs


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------

A, B = ("pA", 4), ("pB", 6)
SA, SB = ("sA", 4), ("sB", 6)
FA, FB = ("pA-fwd", 4), ("pB-fwd", 6)
BOTH = ["a_ecdsa", "b_ed25519"]
CHANGED = "REFUSED-CHANGED"


def series(c: Client, scenario: str, order: list[tuple], expects: list[str | None], *, opts=None,
           seed: list[str] | None = None, host: str = NAME, hold: bool = False) -> list[Obs]:
    """Fresh HOME, optionally the given keys already in known_hosts, then the connections in order."""
    c.fresh()
    if seed:
        c.seed_known_hosts(seed, host)
    return [c.connect(scenario, srv, fam, opts=opts, expect=e, hold=hold) for (srv, fam), e in zip(order, expects)]


def openssh_scenarios(c: Client) -> None:
    uhk = c.ver >= (8, 5)       # UpdateHostKeys defaults to yes from OpenSSH 8.5
    ipnote = c.ver < (8, 5)     # CheckHostIP defaulted to yes before 8.5: one-off "added for IP address" note
    quiet = "SILENT|ADDED-IP-NOTE" if ipnote else "SILENT"
    ed_first = c.ver >= (8, 5)  # the default HostKeyAlgorithms order puts Ed25519 before ECDSA from 8.5
    accept_new = ["StrictHostKeyChecking=accept-new"]
    strict = ["StrictHostKeyChecking=yes"]

    # 1. The claim as stated: nothing in known_hosts, first contact with one proxy then the other.
    series(c, "1a trick, default config (ask, answer yes), A B A B", [A, B, A, B],
           ["PROMPT-NEW", CHANGED, "SILENT", CHANGED])
    series(c, "1b trick, default config (ask, answer yes), B A B A", [B, A, B, A],
           ["PROMPT-NEW", CHANGED, "SILENT", CHANGED])
    series(c, "1c trick, StrictHostKeyChecking=accept-new, A B A B", [A, B, A, B],
           ["AUTO-ADDED", CHANGED, "SILENT", CHANGED], opts=accept_new)
    series(c, "1d trick, StrictHostKeyChecking=accept-new, B A B A", [B, A, B, A],
           ["AUTO-ADDED", CHANGED, "SILENT", CHANGED], opts=accept_new)
    series(c, "1e trick, StrictHostKeyChecking=yes, A B", [A, B], ["REFUSED-UNKNOWN"] * 2, opts=strict)
    series(c, "1f trick, BatchMode=yes, A B", [A, B], ["REFUSED-UNKNOWN"] * 2, opts=["BatchMode=yes"])

    # 1g. The visitor does what the refusal tells them to (ssh-keygen -R), then the other proxy answers again.
    s = "1g trick, visitor runs ssh-keygen -R after each refusal, A B(-R)B A(-R)A B"
    c.fresh()
    c.connect(s, *A, expect="PROMPT-NEW")
    c.connect(s, *B, expect=CHANGED)
    c.forget()
    c.connect(s, *B, expect="PROMPT-NEW")
    c.connect(s, *A, expect=CHANGED)
    c.forget()
    c.connect(s, *A, expect="PROMPT-NEW")
    c.connect(s, *B, expect=CHANGED)

    # 2. Algorithm ordering: with one type known the client still negotiates the other type
    #    (the proposal is reordered, not restricted) -- and then refuses the key it gets.
    series(c, "2a only A's ECDSA key known, connect to Ed25519-only B", [B], [CHANGED], seed=["a_ecdsa"])
    series(c, "2b only B's Ed25519 key known, connect to ECDSA-only A", [A], [CHANGED], seed=["b_ed25519"])

    # 3. Does the announcement reach the client? (Nothing seeded.)
    series(c, "3a plain sshd pair (each announces its own key), default config, A B A B", [SA, SB, SA, SB],
           ["PROMPT-NEW", CHANGED, "SILENT", CHANGED], hold=True)
    series(c, "3b sshpiper pair WITHOUT --drop-hostkeys-message, default config, A B A B", [FA, FB, FA, FB],
           ["PROMPT-NEW", CHANGED, "SILENT", CHANGED], hold=True)

    # 4. Controls and what else refuses (nothing seeded).
    series(c, "4a CONTROL: both proxies ECDSA with different keys, A B", [A, ("pB-ecdsa", 6)], ["PROMPT-NEW", CHANGED])
    series(c, "4b A ECDSA only, B offers ECDSA and Ed25519; A B A B", [A, ("pB-both", 6)] * 2,
           ["PROMPT-NEW", CHANGED, "SILENT", CHANGED])
    series(c, "4c A ECDSA only, B offers ECDSA and Ed25519; B A B A", [("pB-both", 6), A] * 2,
           ["PROMPT-NEW", CHANGED, "SILENT", CHANGED])
    series(c, "4d client pinned to HostKeyAlgorithms=ssh-ed25519, A B", [A, B], ["NO-COMMON-ALGO", "PROMPT-NEW"],
           opts=["HostKeyAlgorithms=ssh-ed25519"])
    series(c, "4e client pinned to HostKeyAlgorithms=ecdsa-sha2-nistp256, A B", [A, B],
           ["PROMPT-NEW", "NO-COMMON-ALGO"], opts=["HostKeyAlgorithms=ecdsa-sha2-nistp256"])
    series(c, "4f client uses HostKeyAlias=fpga-lab, A B", [A, B], ["PROMPT-NEW", CHANGED],
           opts=["HostKeyAlias=fpga-lab"])

    # 6. The variant that can work: BOTH keys already in known_hosts before the first visit
    #    (two published known_hosts lines), so no key is ever learned at connect time.
    abab = [A, B, A, B]
    series(c, "6a both keys pre-seeded, trick pair, default config, A B A B", abab, [quiet] * 4, seed=BOTH, hold=True)
    series(c, "6b both keys pre-seeded, trick pair, StrictHostKeyChecking=yes, B A B A", [B, A, B, A], [quiet] * 4,
           seed=BOTH, opts=strict)
    series(c, "6c both keys pre-seeded, trick pair, UpdateHostKeys=yes forced, A B A B", abab, [quiet] * 4,
           seed=BOTH, opts=["UpdateHostKeys=yes"], hold=True)
    # The danger case: servers that announce their own keys.
    series(c, "6d both keys pre-seeded, plain sshd pair (announcing), default config, A B A B", [SA, SB, SA, SB],
           [quiet, CHANGED, "SILENT", CHANGED] if uhk else [quiet] * 4, seed=BOTH, hold=True)
    series(c, "6e both keys pre-seeded, plain sshd pair (announcing), UpdateHostKeys=yes forced, A B A B",
           [SA, SB, SA, SB], [quiet, CHANGED, "SILENT", CHANGED], seed=BOTH, opts=["UpdateHostKeys=yes"], hold=True)
    series(c, "6f both keys pre-seeded, sshpiper pair WITHOUT --drop-hostkeys-message, default config, A B A B",
           [FA, FB, FA, FB], [quiet] * 4, seed=BOTH, hold=True)
    series(c, "6g both keys pre-seeded, sshpiper pair WITHOUT --drop-hostkeys-message, UpdateHostKeys=yes forced",
           [FA, FB, FA, FB], [quiet] * 4, seed=BOTH, opts=["UpdateHostKeys=yes"], hold=True)
    # What breaks the pre-seeded variant.
    series(c, "6h pre-seeded A's ECDSA + B's Ed25519, but B ALSO offers an ECDSA key; A B A B",
           [A, ("pB-both", 6)] * 2, ["SILENT"] * 4 if ed_first else [quiet, CHANGED, "SILENT", CHANGED],
           seed=["a_ecdsa", "both_ed25519_key"])
    series(c, "6i both keys pre-seeded, client pinned to HostKeyAlgorithms=ssh-ed25519, A B", [A, B],
           ["NO-COMMON-ALGO", quiet], seed=BOTH, opts=["HostKeyAlgorithms=ssh-ed25519"])
    series(c, "6j both keys pre-seeded under the name, client uses HostKeyAlias=fpga-lab, A B", [A, B],
           ["PROMPT-NEW", CHANGED], seed=BOTH, opts=["HostKeyAlias=fpga-lab"])
    series(c, "6k both keys pre-seeded under the alias, client uses HostKeyAlias=fpga-lab, A B A B", abab,
           [quiet] * 4, seed=BOTH, host="fpga-lab", opts=["HostKeyAlias=fpga-lab"])
    series(c, "6l only ONE key pre-seeded (A's), A B", [A, B], [quiet, CHANGED], seed=["a_ecdsa"])


def other_scenarios(c: Client) -> None:
    target = f"{USER}@{NAME}"
    db_env = {"DROPBEAR_PASSWORD": PASSWORD}
    version = c.exec(["dbclient", "-V"])
    LOG.write(f"dbclient version: {(version.stderr + version.stdout).strip()}")
    c.fresh()
    s = "5a dbclient, answer y to questions, A B A B"
    for srv, fam in (A, B, A, B):
        c.other(s, "dbclient", srv, fam, ["dbclient", target, "echo CONNECTED"], env=db_env, input="y\n",
                state=["/root/.ssh/known_hosts"])
    c.fresh()
    s = "5b dbclient -y (accept new keys), A B A B"
    for srv, fam in (A, B, A, B):
        c.other(s, "dbclient", srv, fam, ["dbclient", "-y", target, "echo CONNECTED"], env=db_env,
                state=["/root/.ssh/known_hosts"])
    c.fresh()
    s = "5c dbclient CONTROL: same key type, different keys"
    c.other(s, "dbclient", "pA", 4, ["dbclient", "-y", target, "echo CONNECTED"], env=db_env,
            state=["/root/.ssh/known_hosts"])
    c.other(s, "dbclient", "pB-ecdsa", 6, ["dbclient", "-y", target, "echo CONNECTED"], env=db_env,
            state=["/root/.ssh/known_hosts"])

    version = c.exec(["plink", "-V"])
    LOG.write(f"plink version: {(version.stderr + version.stdout).strip()}")
    plink = ["plink", "-ssh", "-pw", PASSWORD, target, "echo CONNECTED"]
    batch = ["plink", "-ssh", "-batch", "-pw", PASSWORD, target, "echo CONNECTED"]
    c.fresh()
    s = "5d plink, answer y to questions, A B A B, then -batch A B"
    for srv, fam in (A, B, A, B):
        c.other(s, "plink", srv, fam, plink, input="y\n", state=["/root/.putty/sshhostkeys"])
    for srv, fam in (A, B):
        c.other(s, "plink", srv, fam, batch, state=["/root/.putty/sshhostkeys"])
    c.fresh()
    s = "5e plink -batch on first contact"
    c.other(s, "plink", "pA", 4, batch, state=["/root/.putty/sshhostkeys"])
    c.fresh()
    s = "5f plink CONTROL: same key type, different keys"
    c.other(s, "plink", "pA", 4, plink, input="y\n", state=["/root/.putty/sshhostkeys"])
    c.other(s, "plink", "pB-ecdsa", 6, batch, state=["/root/.putty/sshhostkeys"])


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

def summarise(clients: list[Client]) -> str:
    scenarios: dict[str, dict[str, list[Obs]]] = {}
    for c in clients:
        for o in c.obs:
            scenarios.setdefault(o.scenario, {}).setdefault(o.client, []).append(o)
    out = [f"Run finished {now()}", ""]
    out.append("Clients: " + "; ".join(dict.fromkeys(f"{c.tag} = {c.version}" for c in clients)))
    out.append("")
    for scenario in sorted(scenarios):
        out.append(f"## {scenario}")
        for client, obs in scenarios[scenario].items():
            steps = ", ".join(f"{o.target.split()[0]}: {o.outcome}" + (" +bad-signature error" if o.bad_signature else "")
                              + ("" if o.ok is not False else " (UNEXPECTED)") for o in obs)
            out.append(f"- {client}: {steps}")
            out.append(f"  - stored keys at end: {'; '.join(obs[-1].known_hosts) or '(none)'}")
        out.append("")
    return "\n".join(out)


def verdict(clients: list[Client]) -> str:
    """The claim, judged from what the OpenSSH clients did (not from the expectations)."""
    out = ["## Verdict per OpenSSH client"]
    for c in clients:
        if not any(o.client == c.tag for o in c.obs):
            continue
        def outcomes(prefix: str) -> list[str]:
            return [o.outcome for o in c.obs if o.scenario.startswith(prefix)]
        second = outcomes("1a")[1:2] + outcomes("1b")[1:2]
        as_stated = all(x in ("PROMPT-NEW", "AUTO-ADDED", "SILENT") for x in second)
        seeded = all(x in ("SILENT", "ADDED-IP-NOTE") for x in outcomes("6a") + outcomes("6b"))
        announced = outcomes("6d")
        out.append(f"- {c.tag} ({c.version.split(',')[0]}): claim as stated "
                   f"{'HOLDS' if as_stated else 'DOES NOT HOLD'} (second proxy on first contact: {', '.join(second)}); "
                   f"with both keys pre-seeded and announcements dropped {'HOLDS' if seeded else 'DOES NOT HOLD'}; "
                   f"pre-seeded but servers announce their keys: {', '.join(announced)}")
    return "\n".join(out) + "\n"


def main() -> int:
    global LOG
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--clients", nargs="*", default=list(CLIENTS), choices=list(CLIENTS))
    parser.add_argument("--keep", action="store_true", help="leave containers, network and images in place")
    args = parser.parse_args()

    (WORK / "logs").mkdir(parents=True, exist_ok=True)
    LOG = Log(WORK / "transcript.log")
    LOG.write(f"ssh key-types lab, started {now()}", echo=True)
    note_images_before()
    failed = True
    try:
        stop_lab()  # leftovers of an interrupted or --keep run
        build_sshpiper()
        version = run([str(WORK / "bin" / "sshpiperd"), "--version"], check=False)
        LOG.write(f"sshpiperd: {(version.stdout + version.stderr).strip()} (commit {SSHPIPER_COMMIT})", echo=True)
        labels = make_keys()
        build_images(args.clients)
        start_lab(args.clients)
        backend_keys = run(["docker", "exec", BACKEND.container, "sh", "-c", "cat /etc/ssh/ssh_host_*_key.pub"]).stdout
        for line in backend_keys.splitlines():
            labels[line.split()[1]] = f"BACKEND-{line.split()[0]}"
        for s in SERVERS.values():
            LOG.write(f"server {s.name}: {s.describe}; {s.v4} / {s.v6}; {' '.join(s.command)}")
        clients = [Client(tag, labels) for tag in args.clients]
        for c in clients:
            LOG.write(f"client {c.tag}: {c.version}", echo=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(clients)) as pool:
            for fut in [pool.submit(openssh_scenarios, c) for c in clients]:
                fut.result()
        if OTHER_CLIENTS_IN in args.clients:
            others = Client(OTHER_CLIENTS_IN, labels)
            other_scenarios(others)
            clients.append(others)
        summary = summarise(clients)
        (WORK / "summary.md").write_text(summary + "\n")
        (WORK / "results.json").write_text(json.dumps(
            [dataclasses.asdict(o) for c in clients for o in c.obs], indent=1) + "\n")
        print(summary)
        print(verdict(clients))
        unexpected = [o for c in clients for o in c.obs if o.ok is False]
        checked = [o for c in clients for o in c.obs if o.ok is not None]
        for o in unexpected:
            print(f"UNEXPECTED: {o.client} | {o.scenario} step {o.step}: {o.outcome}, expected {o.expect}")
        print(f"\nexpectations met: {len(checked) - len(unexpected)} of {len(checked)}; "
              f"transcript: {WORK / 'transcript.log'}")
        failed = bool(unexpected)
    finally:
        if not args.keep:
            stop_lab()
            remove_images()
            left = run(["docker", "ps", "-aq", "--filter", f"name={PREFIX}-"]).stdout.strip()
            print(f"cleanup done at {now()}; lab containers left: {left or 'none'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
