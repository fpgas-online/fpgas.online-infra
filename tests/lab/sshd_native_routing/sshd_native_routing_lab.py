#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Lab: board login names routed inside the gateway's own sshd.

`ssh pi-sw2-p47@<site>` on the gateway's normal sshd (port 22) lands on that
board; `ssh <administrator>@<site>` stays an ordinary key login. See
README.md for the design and results-*.md for what was measured.

    uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py            # everything
    uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py --only visitor,sandbox
    uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py up         # leave the lab running
    uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py down       # remove it

Not collected by pytest, not run in CI: it needs Docker and several minutes.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime
import json
import re
import shlex
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import lab  # noqa: E402
from lab import BOARD_PASSWORD, GW, SITE, dexec, visitor  # noqa: E402

BOARD = "pi-sw2-p47"
BOARD2 = "pi-sw1-p5"
BANNER = "fpgas.online board login."
ADMIN_SSH = ["ssh", "-i", "/lab/keys/admin_key", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new"]


def now() -> str:
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


# --------------------------------------------------------------------------
# The report


class Report:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.rows: list[tuple[str, str, str]] = []
        self.failed = 0

    def section(self, title: str, intro: str = "") -> None:
        self.parts.append(f"\n## {title}\n\n")
        if intro:
            self.parts.append(intro.strip() + "\n")
        print(f"\n== {title}", flush=True)

    def add(self, tid: str, title: str, ok: bool | None, body: str, finding: str = "") -> None:
        verdict = "INFO" if ok is None else ("PASS" if ok else "FAIL")
        if ok is False:
            self.failed += 1
        self.rows.append((tid, title, verdict))
        text = f"\n### {tid}. {title}: {verdict}\n\n"
        if finding:
            text += finding.strip() + "\n\n"
        text += "```\n" + body.strip("\n") + "\n```\n"
        self.parts.append(text)
        print(f"  {verdict:4} {tid} {title}", flush=True)

    def render(self, header: str) -> str:
        table = "| Id | Test | Result |\n|---|---|---|\n" + "".join(f"| {t} | {n} | {v} |\n" for t, n, v in self.rows)
        return header + "\n## Summary\n\n" + table + "".join(self.parts)


def clip(text: str, limit: int = 2500) -> str:
    text = "\n".join(line for line in text.splitlines() if not line.startswith(BANNER))
    if len(text) > limit:
        text = text[:limit] + f"\n[... {len(text) - limit} more characters cut ...]"
    return text


def vshow(res: lab.Result, argv: list[str]) -> str:
    return f"$ {shlex.join(argv)}\n{clip(res.text)}\n[exit {res.rc}, {res.secs:.1f} s]\n"


def run_visitor(argv: list[str], **kw) -> tuple[lab.Result, str]:
    res = visitor(argv, **kw)
    return res, vshow(res, argv)


def ssh(user: str, cmd: str | None = None, *, opts: list[str] | None = None, host: str = SITE, **kw):
    argv = ["ssh", *(opts or []), f"{user}@{host}", *([cmd] if cmd is not None else [])]
    return run_visitor(argv, **kw)


def admin(which: str = "b", host: str = SITE, timeout: float = 30) -> lab.Result:
    return dexec(lab.client(which), [*ADMIN_SSH, f"{lab.ADMIN}@{host}", "hostname"], timeout=timeout)


def gw(argv: list[str], **kw) -> lab.Result:
    return dexec(GW, argv, **kw)


def gw_sh(script: str, **kw) -> lab.Result:
    return dexec(GW, ["sh", "-c", script], **kw)


def prepare_clients() -> None:
    """Every group starts from the same visitor: gateway key known, a test file to copy."""
    script = ("mkdir -p /root/.ssh && echo \"" + SITE + " $(cut -d' ' -f1,2 /lab/keys/gateway_key.pub)\" > /root/.ssh/known_hosts; "
              "rm -f /root/askpass.log; [ -s /root/blob ] || head -c 5000000 /dev/urandom > /root/blob")
    for which in lab.CLIENTS:
        dexec(lab.client(which), ["sh", "-c", script], check=True)


def interesting(lines: list[str], pattern: str) -> str:
    rx = re.compile(pattern)
    return "\n".join(line for line in lines if rx.search(line))


def relay_procs() -> str:
    return gw(["ps", "-o", "pid,ppid,user:12,args", "-u", "950"]).out.strip()


def relay_proc_count() -> int:
    return max(len(relay_procs().splitlines()) - 1, 0)


# --------------------------------------------------------------------------
# 1. How a board name comes to exist


def g_lookup(rep: Report) -> None:
    rep.section("1. Making board names exist (user lookup)", """
sshd refuses a login name unless `getpwnam()` finds it. Four ways to make the
ninety-six names of two 48-port switches exist, each with ONE shared uid
(950). The gateway is restarted with each in turn; Debian's stock PAM stack
for sshd is used here, to show that none of them needs a PAM change.
""")
    what = {
        "accounts": "real accounts in /etc/passwd and /etc/shadow, made with `useradd -o -u 950` per port",
        "extrausers": "libnss-extrausers: one generated file /var/lib/extrausers/passwd (and shadow)",
        "nss": "the custom NSS module libnss_fpgasboard (src/nss_fpgasboard.c): no list, the formula",
        "userdb": "systemd userdb drop-ins: /etc/userdb/<name>.user JSON, read through nss-systemd",
    }
    for lookup, desc in what.items():
        lab.start_gateway(lookup=lookup, pam="stock")
        out = f"# gateway restarted with: {desc}\n"
        ok = True
        for cmd in ("getent passwd pi-sw2-p47", "getent passwd pi-sw2-p49 pi-sw3-p1 pi-sw02-p47 pi-sw2-p047 PI-SW2-P47",
                    "getent passwd 950", "getent passwd | grep -c '^pi-sw'",
                    "getent shadow pi-sw2-p47 | cut -c1-24",
                    "sshd -T -C user=pi-sw2-p47,host=x,addr=192.0.2.1 | grep -E '^(forcecommand|authenticationmethods|chrootdirectory|passwordauthentication) '",
                    "sshd -T -C user=alice,host=x,addr=192.0.2.1 | grep -E '^(forcecommand|authenticationmethods|chrootdirectory|passwordauthentication) '",
                    "sshd -T -C user=pi-sw2-p49,host=x,addr=192.0.2.1 | grep -E '^(forcecommand|authenticationmethods) '"):
            res = gw_sh(cmd)
            out += f"gw# {cmd}\n{res.text}\n[exit {res.rc}]\n"
        mark = lab.gw_log_mark()
        r1, s1 = ssh(BOARD, "hostname", opts=["-o", "StrictHostKeyChecking=accept-new"])
        r2, s2 = ssh(BOARD2, "hostname")
        r3, s3 = ssh("pi-sw2-p49", "hostname")
        asked = lab.prompts()
        ok = r1.out.strip() == BOARD and r2.out.strip() == BOARD2 and r3.rc == 255
        out += s1 + s2 + s3
        out += "# password prompts the client showed: " + json.dumps(asked) + "\n"
        out += "# gateway log:\n" + interesting(lab.gw_log_since(mark), r"Accepted|Invalid user|session opened|Starting session|Changed root")
        rep.add(f"L-{lookup}", f"lookup by {lookup}", ok, out)
    lab.start_gateway(lookup="userdb", pam="stock")
    res = gw_sh("ls /etc/userdb | wc -l; cat /etc/userdb/pi-sw2-p47.user; cat /etc/userdb/pi-sw2-p47.user-privileged | cut -c1-60; "
                "ls /run/systemd/userdb 2>&1; grep -E '^(passwd|shadow)' /etc/nsswitch.conf")
    rep.add("L-userdb-files", "what the userdb drop-ins look like", None, f"{res.text}\n",
            "No systemd is running in the lab container (no /run/systemd/userdb socket): nss-systemd reads the drop-in "
            "files itself. Many records may carry the same uid; lookups are by name.")


# --------------------------------------------------------------------------
# 2. Which sshd_config keywords a Match block takes


def g_directives(rep: Report) -> None:
    rep.section("2. What a Match block can and cannot set (OpenSSH 10.0)", """
`sshd -t` on a two-line configuration `Match User x` + the keyword. Global-only
keywords are the ones the board names necessarily share with administrators.
""")
    lab.start_gateway()
    keywords = [
        "PasswordAuthentication yes", "AuthenticationMethods password", "PermitEmptyPasswords yes",
        "KbdInteractiveAuthentication no", "PubkeyAuthentication no", "MaxAuthTries 3", "MaxSessions 4",
        "ForceCommand relay", "ChrootDirectory /srv/x", "Subsystem sftp marker", "PAMServiceName sshd-fpgas-board",
        "PermitTTY yes", "DisableForwarding yes", "AllowTcpForwarding no", "AllowStreamLocalForwarding no",
        "AllowAgentForwarding no", "X11Forwarding no", "PermitTunnel no", "PermitOpen none", "PermitListen none",
        "PermitUserRC no", "AcceptEnv LANG", "SetEnv A=b", "Banner /etc/issue.net", "ClientAliveInterval 30",
        "ChannelTimeout session=30m", "UnusedConnectionTimeout 60", "LogLevel VERBOSE", "RefuseConnection yes",
        "AuthorizedKeysCommand none", "AuthorizedKeysFile none",
        "PermitUserEnvironment no", "LoginGraceTime 20", "MaxStartups 10:30:100", "PerSourceMaxStartups 10",
        "PerSourcePenalties no", "PerSourcePenaltyExemptList 192.0.2.1", "UsePAM yes",
    ]
    out = ""
    for kw in keywords:
        res = gw_sh("cat > /root/t.conf && sshd -t -f /root/t.conf",
                    input=f"HostKey /etc/ssh/ssh_host_ed25519_key\nMatch User x\n    {kw}\n")
        verdict = "allowed in Match" if res.rc == 0 else "GLOBAL ONLY: " + res.text.splitlines()[-1].split(": ", 1)[-1]
        out += f"{kw:38} {verdict}\n"
    rep.add("H-match", "keywords accepted inside Match", None, out)
    res = gw_sh("cat > /root/t.conf && sshd -T -f /root/t.conf -C user=x,host=h,addr=192.0.2.1 | grep -i '^forcecommand'",
                input="HostKey /etc/ssh/ssh_host_ed25519_key\nMatch User x\n    ForceCommand /bin/echo %u %h\n")
    rep.add("H-token", "ForceCommand does not expand %u", "%u" in res.out, f"{res.text}\n",
            "ForceCommand takes no tokens: the relay cannot be told the name on its command line. It reads "
            "$USER, which sshd sets from the account it authenticated, after any client-supplied variables.")


# --------------------------------------------------------------------------
# 3. What a visitor can do


def g_visitor(rep: Report) -> None:
    rep.section("3. What works for a visitor", f"""
Gateway: lookup by extrausers, the board names' own PAM stack, gateway checks
the public password, sshd ChrootDirectory, the relay's Landlock sandbox, the
uid-keyed nftables filter. The visitor's machine is `{lab.client('a')}`
(OpenSSH 10.0 client). The Banner line the gateway prints before every
password prompt is cut from the outputs below except in V-prompts.
""")
    lab.start_gateway()
    a = lab.client("a")
    dexec(a, ["sh", "-c", "rm -f /root/.ssh/known_hosts /root/askpass.log"])

    # First contact on a real terminal: every prompt, in order.
    spec = {"argv": ["ssh", f"{BOARD}@{SITE}"], "env": {"TERM": "xterm-256color"}, "rows": 24, "cols": 80, "steps": [
        {"expect": r"continue connecting", "send": "yes\n"},
        {"expect": r"password: ", "send": BOARD_PASSWORD + "\n"},
        {"expect": r"\$ ", "send": "tty; echo TERM=$TERM; stty size\n"},
        {"expect": r"24 80", "resize": [40, 132], "pause": 0.5, "send": "stty size\n"},
        {"expect": r"40 132", "send": "printf '\\033[1;31mred\\033[0m\\n'; exit\n"},
    ], "final_timeout": 10}
    res = lab.pty(spec)
    ok = all(res["matched"]) and len(res["matched"]) == 5 and "/dev/pts/" in res["output"] and "TERM=xterm-256color" in res["output"] \
        and res["exit"] == 0 and "\x1b[1;31mred" in res["output"]
    fp = lab.fingerprint("gateway_key")
    rep.add("V-prompts", "first contact, interactive shell, terminal size, colours", ok and fp in res["output"],
            f"$ ssh {BOARD}@{SITE}      (on a terminal, 24x80, TERM=xterm-256color)\n" + res["output"].replace("\r", "") +
            f"\n[exit {res['exit']}; steps matched {res['matched']}]\n# gateway host key: {fp}\n# fleet key: {lab.fingerprint('fleet_key')}\n",
            "One host-key question (the gateway's own key, the same key administrators see), the banner, ONE "
            "password prompt, then the board's shell with a working terminal: the size follows a resize.")

    r, s = ssh(BOARD, "hostname; id -un; echo \"$SSH_CONNECTION\"; exit 7")
    rep.add("V-command", "remote command and its exit status", r.rc == 7 and r.out.split()[:2] == [BOARD, "pi"], s,
            "The command runs on the board as pi; the exit status comes back. The board sees the gateway's address.")
    r, s = ssh(BOARD, "printf '%s|' \"a  b\" 'c;d' \"$HOME\"; echo; echo two words | wc -w")
    rep.add("V-quoting", "quoting reaches the board's shell unchanged", r.rc == 0 and "a  b|c;d|/home/pi|" in r.out, s)
    res = lab.pty({"argv": ["ssh", "-t", f"{BOARD}@{SITE}", "tty; stty size"], "rows": 30, "cols": 100, "steps": [
        {"expect": r"password: ", "send": BOARD_PASSWORD + "\n"}], "final_timeout": 8})
    rep.add("V-tty-command", "ssh -t with a command", "/dev/pts/" in res["output"] and "30 100" in res["output"],
            f"$ ssh -t {BOARD}@{SITE} 'tty; stty size'\n" + clip(res["output"].replace("\r", "")) + f"\n[exit {res['exit']}]\n")

    want = dexec(a, ["sha256sum", "/root/blob"]).out.split()[0]
    tests = [
        ("V-scp", "scp (sftp mode, the default since OpenSSH 9.0)",
         f"scp -q /root/blob {BOARD}@{SITE}:scp1 && scp -q {BOARD}@{SITE}:scp1 /root/scp1.back && sha256sum /root/scp1.back"),
        ("V-scp-legacy", "scp -O (legacy scp protocol)",
         f"scp -q -O /root/blob {BOARD}@{SITE}:scp2 && scp -q -O {BOARD}@{SITE}:scp2 /root/scp2.back && sha256sum /root/scp2.back"),
        ("V-sftp", "sftp",
         f"printf 'put /root/blob sftp1\\nls -l sftp1\\nget sftp1 /root/sftp1.back\\n' | sftp -q {BOARD}@{SITE} && sha256sum /root/sftp1.back"),
        ("V-rsync", "rsync over ssh",
         f"rsync -a /root/blob {BOARD}@{SITE}:rsync1 && rsync -a {BOARD}@{SITE}:rsync1 /root/rsync1.back && sha256sum /root/rsync1.back"),
        ("V-pipe", "binary data through stdin and stdout",
         f"cat /root/blob | ssh {BOARD}@{SITE} 'cat > pipe1; cat pipe1' | sha256sum"),
    ]
    for tid, title, script in tests:
        r, s = run_visitor(["sh", "-c", script], timeout=120)
        rep.add(tid, title, r.rc == 0 and want in r.out, s + f"# sha256 of the file sent: {want}\n")

    r, s = ssh(BOARD, "hostname", host=f"v6.{SITE}", opts=["-o", f"HostKeyAlias={SITE}"])
    rep.add("V-ipv6", "the same over IPv6 to the gateway", r.out.strip() == BOARD, s)

    r, s = run_visitor(["sh", "-c",
                        f"ssh -M -S /root/cm -fN {BOARD}@{SITE} && for i in 1 2 3; do ssh -S /root/cm {BOARD}@{SITE} "
                        f"'echo session '$i' on $(hostname)'; done; ssh -S /root/cm -O exit {BOARD}@{SITE}"])
    rep.add("V-mux", "several sessions over one connection (ControlMaster)", r.out.count(f"on {BOARD}") == 3, s,
            "Each session is its own relay and its own connection to the board; MaxSessions bounds them.")

    # Things that are deliberately off.
    script = (f"ssh -o ExitOnForwardFailure=yes -L 18000:{lab.BOARDS[BOARD].ip}:8000 {BOARD}@{SITE} 'sleep 4' & "
              "sleep 2; printf 'GET / HTTP/1.0\\r\\n\\r\\n' | nc -w 2 127.0.0.1 18000; echo \"nc exit $?\"; wait")
    mark = lab.gw_log_mark()
    r, s = run_visitor(["sh", "-c", script])
    rep.add("V-L", "-L (local port forward to a service on the board): refused", "hello from" not in r.out and "prohibited" in r.text,
            s + "# gateway log:\n" + interesting(lab.gw_log_since(mark), "refused"),
            "Deliberately not offered: the gateway's sshd refuses every forwarding channel for board names.")
    r, s = ssh(BOARD, "true", opts=["-o", "ExitOnForwardFailure=yes", "-R", "18001:127.0.0.1:22"])
    rep.add("V-R", "-R (remote port forward): refused", r.rc != 0 and "forwarding failed" in r.text, s)
    r, s = ssh(BOARD, "echo \"DISPLAY=[$DISPLAY]\"", opts=["-X"], env={"DISPLAY": ":0"})
    rep.add("V-X11", "-X: no X11 forwarding", "DISPLAY=[]" in r.out, s)
    r, s = run_visitor(["sh", "-c", f"eval $(ssh-agent -s) > /dev/null; ssh -A {BOARD}@{SITE} 'echo \"SSH_AUTH_SOCK=[$SSH_AUTH_SOCK]\"'; ssh-agent -k > /dev/null"])
    rep.add("V-agent", "-A: no agent reaches the board", "SSH_AUTH_SOCK=[]" in r.out, s,
            "A board's root could use a forwarded agent; it never gets one, whatever the visitor asks for.")
    r, s = run_visitor(["ssh", "-J", f"{BOARD}@{SITE}", "-o", "StrictHostKeyChecking=accept-new",
                        f"pi@{lab.BOARDS[BOARD].ip}", "hostname"])
    rep.add("V-J", "ssh -J through a board name: refused in the recommended design", r.rc != 0 and "prohibited" in r.text, s,
            "See J-* below for the variant that allows it.")
    r, s = ssh(BOARD, "echo \"LANG=[$LANG] LC_ALL=[$LC_ALL] FOO=[$FOO]\"", opts=["-o", "SetEnv=LC_ALL=C.UTF-8 FOO=bar"], env={"LANG": "en_AU.UTF-8"})
    rep.add("V-env", "client environment (LANG, LC_*) does not reach the board", None, s,
            "A difference from a direct login: the relay passes TERM and nothing else.")


# --------------------------------------------------------------------------
# 4. The sandbox


def g_sandbox(rep: Report) -> None:
    rep.section("4. No shell and no reach on the gateway", """
What a visitor can make the gateway do, and what a process standing where a
compromised relay would stand can reach.
""")
    lab.start_gateway()
    a = lab.client("a")
    board_ip = lab.BOARDS[BOARD].ip

    out = ""
    ok = True
    commands = [
        "-oProxyCommand=touch /tmp/lab-pwned",
        "-F/dev/null -oProxyCommand=touch${IFS}/tmp/lab-pwned x",
        "-- -oProxyCommand=touch /tmp/lab-pwned",
        "-i /etc/board-relay/password -v localhost",
        "; touch /tmp/lab-pwned; hostname",
        "$(touch /tmp/lab-pwned) `touch /tmp/lab-pwned` hostname",
        "hostname\ntouch /tmp/lab-pwned\nhostname",
        "fpgas-board-sftp x",
        "relay",
    ]
    for cmd in commands:
        r = visitor(["ssh", f"{BOARD}@{SITE}", "--", cmd])
        out += f"$ ssh {BOARD}@{SITE} -- {cmd!r}\n{clip(r.text, 300)}\n[exit {r.rc}]\n"
    r = visitor(["sh", "-c", f"ssh {BOARD}@{SITE} \"echo $(head -c 100000 /dev/zero | tr '\\0' 'x')\" | wc -c"])
    out += f"$ ssh {BOARD}@{SITE} \"echo <100000 x characters>\" | wc -c\n{clip(r.text, 300)}\n[exit {r.rc}]\n"
    long_ok = r.out.strip() == "100001"
    res = gw_sh("ls -la /tmp/lab-pwned /srv/fpgas-board-relay/tmp /srv/fpgas-board-relay/lab-pwned 2>&1; echo; find / -xdev -name 'lab-pwned*' 2>&1 | head")
    onboard = dexec(lab.BOARDS[BOARD].container, ["ls", "-la", "/tmp/lab-pwned"])
    ok = "lab-pwned" not in gw_sh("find / -xdev -name 'lab-pwned*'").out and onboard.rc == 0 and long_ok
    out += f"# on the gateway afterwards:\ngw# ls /tmp/lab-pwned ...; find / -name 'lab-pwned*'\n{res.text}\n"
    out += f"# on the BOARD afterwards (where the visitor has a shell anyway):\n{onboard.text}\n"
    rep.add("S-command", "hostile requested commands never run on the gateway", ok, out,
            "Whatever the visitor asks for is one argument after `--` to the relay's ssh, so only the board's shell "
            "reads it. Option-looking commands are not options; the file appears on the board and never on the gateway.")

    # Escape characters of the relay's own ssh client.
    spec = {"argv": ["ssh", "-e", "none", f"{BOARD}@{SITE}"], "steps": [
        {"expect": r"password: ", "send": BOARD_PASSWORD + "\n"},
        {"expect": r"\$ ", "send": "\n~?\n"},
        {"expect": r"\$ ", "pause": 0.5, "send": "\n~C\n"},
        {"expect": r"\$ ", "pause": 0.5, "send": "\n~.\n"},
        {"expect": r"\$ ", "pause": 0.5, "send": "echo still-on-$(hostname)\n"},
        {"expect": r"still-on-pi", "send": "exit\n"},
    ], "final_timeout": 8}
    res = lab.pty(spec)
    text = res["output"].replace("\r", "")
    ok = all(res["matched"]) and "Supported escape" not in text and "ssh>" not in text and res["exit"] == 0
    rep.add("S-escape", "escape characters (~? ~C ~.) do nothing in the relay's ssh", ok,
            f"$ ssh -e none {BOARD}@{SITE}     (so that ~ reaches the gateway instead of the visitor's own client)\n"
            + clip(text) + f"\n[exit {res['exit']}; steps matched {res['matched']}]\n",
            "The relay's client runs with `EscapeChar none`: no command line, no suspend, no forwarding added from inside.")

    out = ""
    r, s = ssh(BOARD, "hostname; echo \"LD_PRELOAD=[$LD_PRELOAD] USER=$USER\"",
               opts=["-o", "SetEnv=USER=pi-sw1-p5 LOGNAME=pi-sw1-p5 LD_PRELOAD=/tmp/x.so SSH_ASKPASS=/bin/sh LC_ALL=x"])
    out += s
    r2, s2 = ssh(BOARD, "hostname", opts=["-o", "SendEnv=USER", "-o", "SetEnv=USER=pi-sw1-p5"], env={"USER": BOARD2})
    out += s2
    rep.add("S-env", "the client cannot choose the board or pass variables to the relay",
            r.out.split()[0] == BOARD and r2.out.strip() == BOARD and "LD_PRELOAD=[]" in r.out, out,
            "sshd accepts only LANG/LC_*/COLORTERM/NO_COLOR from clients (Debian's AcceptEnv), sets USER after them, "
            "and the relay drops its whole environment before it execs ssh.")

    r, s = run_visitor(["ssh", "-s", f"{BOARD}@{SITE}", "nosuch"])
    rep.add("S-subsystem", "a subsystem other than sftp", r.rc != 0 and "subsystem request failed" in r.text.lower(), s,
            "Only sftp is defined for board names, and it is the BOARD's sftp server that answers (V-sftp, V-scp).")

    names = ["pi-sw2-p49", "pi-sw3-p1", "pi-sw0-p1", "pi-sw2-p0", "pi-sw02-p47", "pi-sw2-p047", "PI-SW2-P47",
             "pi-sw2-p47x", "xpi-sw2-p47", "pi-sw2-p47;id", "pi-sw2-p4７", "pi-sw2-p", "pi-sw2-p47" * 30, "pi-sw-1-p1",
             "pi-sw2-p-1", "pi-sw2--p47", "pi-sw2-p47 ", "pi-sw2-p47/../../root", "pi-sw2-p47:x", "pi-sw1-p5/pi-sw2-p47",
             "fpgas-board", "pi", "root", lab.ADMIN]
    out = f"{'login name':34} {'outcome':62} password prompt\n"
    ok = True
    mark = lab.gw_log_mark()
    for name in names:
        dexec(a, ["rm", "-f", "/root/askpass.log"])
        r = visitor(["ssh", "-o", f"User={name}", SITE, "hostname"])
        asked = bool(lab.prompts())
        shown = name if len(name) < 32 else name[:20] + f"...({len(name)} chars)"
        last = clip(r.text).splitlines()[-1] if clip(r.text) else ""
        if r.rc == 0:
            # sshd (Debian) cuts a login name at the first "/" (SELinux role) or ":" (style), and the
            # client cuts trailing spaces: what remains must be a board, and the session must be on it.
            want = name.split("/")[0].split(":")[0].strip()
            ok = ok and r.out.strip() == want
            last = f"LOGGED IN on {r.out.strip()}"
        else:
            ok = ok and not asked
        out += f"{shown!r:34} {last[:60]:62} {'yes' if asked else 'no'}\n"
    out += "# gateway log (distinct lines):\n" + "\n".join(sorted({re.sub(r"port \d+", "port N", line)[:110] for line in lab.gw_log_since(mark)
                                                                  if re.search(r"Invalid user|not allowed|Accepted", line)}))
    rep.add("S-names", "odd, long and near-miss login names", ok, out,
            "Only exact names inside the site's shape exist. Everything else is an invalid user to sshd: it is offered "
            "`publickey` only, never a password prompt, and never reaches the relay. Debian's sshd cuts a login name at "
            "the first `/` or `:` before it looks the name up, so `pi-sw2-p47/anything` IS `pi-sw2-p47`; the relay only "
            "ever sees the name sshd looked up.")

    # The probe: what a process in the relay's position can reach.
    target = gw_sh("setpriv --reuid 950 --regid 950 --clear-groups sleep 300 & echo $!").out.strip()
    args = ["--kill", target, "--tcp", f"{board_ip}:22", "--tcp", f"{board_ip}:8000", "--tcp", "127.0.0.1:22",
            "--tcp", f"{lab.GW_UP4}:22", "--tcp", f"{lab.CLIENTS['b']}:22", "--tcp", f"{lab.BNET}.9.9:22",
            "--udp", f"{board_ip}:53", "--udp", f"{lab.CLIENTS['b']}:53"]
    probe = "/usr/lib/fpgas-board-relay/probe"
    lab.dexec(lab.client("b"), ["sh", "-c", "nc -l -p 22 >/dev/null & sleep 0.3"])  # something listening on a non-board address
    out = ""
    cases = [
        ("as it runs: sshd's chroot + the relay's Landlock + the nftables filter",
         ["chroot", "--userspec=950:950", "/srv/fpgas-board-relay", probe, *args]),
        ("chroot and nftables only (as if the relay's own sandbox were missing)",
         ["chroot", "--userspec=950:950", "/srv/fpgas-board-relay", probe, "--no-landlock", *args]),
        ("nftables only: uid 950 on the gateway's real root, no chroot, no Landlock",
         ["setpriv", "--reuid", "950", "--regid", "950", "--clear-groups", probe, "--no-landlock", *args]),
    ]
    results = {}
    for title, argv in cases:
        res = gw(argv)
        results[title] = res.text
        out += f"# {title}\ngw# {shlex.join(argv[:4])} ...\n{res.text}\n\n"
    full = results[cases[0][0]]
    lines = {tuple(line.split()[:2]): line for line in full.splitlines()}
    def allowed(kind: str, arg: str) -> bool:
        return "ALLOWED" in lines.get((kind, arg), "")
    ok = (allowed("tcp", f"{board_ip}:22") and not allowed("tcp", f"{board_ip}:8000") and not allowed("tcp", "127.0.0.1:22")
          and not allowed("tcp", f"{lab.GW_UP4}:22") and not allowed("tcp", f"{lab.CLIENTS['b']}:22")
          and not allowed("udp", f"{board_ip}:53") and not allowed("exec", "/bin/sh") and not allowed("exec", "/usr/bin/sh")
          and not allowed("read", "/etc/shadow") and not allowed("write", "/tmp/probe-file") and not allowed("signal", target))
    nft = gw(["nft", "list", "chain", "inet", "fpgas_board_relay", "board_relay"]).out
    out += "gw# nft list chain inet fpgas_board_relay board_relay\n" + nft
    gw_sh(f"kill {target}")
    rep.add("S-probe", "what a compromised relay could reach", ok, out,
            "`probe` (src/probe.c, lab only) runs as the shared uid and tries files, programs, signals and connections. "
            "With all three layers the only thing reachable is TCP port 22 of a board address.")

    r = gw_sh("cd /srv/fpgas-board-relay && find . -xdev \\( -type f -o -type l -o -type c \\) | sort; echo; du -sh .")
    rep.add("S-chroot", "everything inside sshd's ChrootDirectory", None, r.text + "\n",
            "No shell, no interpreter, no setuid program. (`probe` is there for this lab only.)")

    mark = lab.gw_log_mark()
    spec = {"argv": ["ssh", f"{BOARD}@{SITE}"], "steps": [{"expect": r"password: ", "send": BOARD_PASSWORD + "\n"},
                                                         {"expect": r"\$ ", "send": "sleep 3; exit\n"}], "final_timeout": 10}
    t = threading.Thread(target=lab.pty, args=(spec,))
    t.start()
    time.sleep(2.5)
    tree = gw(["ps", "-eo", "pid,ppid,user:12,tty,args"]).out
    t.join()
    time.sleep(0.5)
    rep.add("S-procs", "what runs on the gateway for one board login, and what the log says", relay_proc_count() == 0,
            "gw# ps -eo pid,ppid,user,tty,args      (during an interactive board session)\n" + tree +
            "\n# gateway log for that login (sshd LogLevel VERBOSE):\n" + "\n".join(lab.gw_log_since(mark)) +
            f"\n\n# processes of uid 950 after the session ended: {relay_proc_count()}\n",
            "sshd's privileged monitor (root) keeps the pty and the PAM session; the session process and the relay's ssh "
            "run as the shared uid inside the chroot. Nothing is left when the visitor leaves.")


# --------------------------------------------------------------------------
# 5. Other ways to do the hand-off and the authentication


def g_variants(rep: Report) -> None:
    rep.section("5. Variants that were evaluated", """
Each restarts the gateway with a different Match block (files/gateway-entrypoint.sh, VARIANT=...).
""")
    a = lab.client("a")
    board_ip = lab.BOARDS[BOARD].ip

    lab.start_gateway(variant="none")
    dexec(a, ["rm", "-f", "/root/askpass.log"])
    mark = lab.gw_log_mark()
    r, s = ssh(BOARD, "hostname", opts=["-o", "BatchMode=yes"])
    r2, s2 = run_visitor(["sh", "-c", f"scp -q -o BatchMode=yes /root/blob {BOARD}@{SITE}:none1 && echo copied"])
    res = lab.pty({"argv": ["ssh", f"{BOARD}@{SITE}"], "steps": [{"expect": r"\$ ", "send": "exit\n"}], "final_timeout": 6})
    rep.add("A-none", "no authentication at the gateway (PermitEmptyPasswords, method `none`)",
            r.out.strip() == BOARD and "copied" in r2.out and res["matched"] == [True],
            s + s2 + f"$ ssh {BOARD}@{SITE}     (on a terminal)\n" + clip(res["output"].replace("\r", ""), 600) +
            "\n# gateway log:\n" + interesting(lab.gw_log_since(mark), "Accepted"),
            "The name alone logs in: no prompt at all, for shells, scp and everything else. The relay still answers the "
            "board's password prompt with the public password.")

    lab.start_gateway(variant="prompt", sandbox="none")
    res = lab.pty({"argv": ["ssh", f"{BOARD}@{SITE}"], "steps": [
        {"expect": r"password: ", "send": BOARD_PASSWORD + "\n"}, {"expect": r"\$ ", "send": "hostname; exit\n"}], "final_timeout": 6})
    r, s = ssh(BOARD, "hostname")
    r2, s2 = run_visitor(["sh", "-c", f"scp -q /root/blob {BOARD}@{SITE}:prompt1; echo scp exit $?"])
    wrong = lab.pty({"argv": ["ssh", f"{BOARD}@{SITE}"], "steps": [{"expect": r"password: ", "send": "wrong\n"}], "final_timeout": 8})
    rep.add("A-prompt", "the visitor's password checked by the BOARD (gateway `none`, the relay's ssh prompts)",
            all(res["matched"]) and r.rc != 0 and "scp exit 0" not in r2.out,
            f"$ ssh {BOARD}@{SITE}     (on a terminal)\n" + clip(res["output"].replace("\r", ""), 600) + "\n" + s + s2 +
            f"$ ssh {BOARD}@{SITE}     (on a terminal, wrong password)\n" + clip(wrong["output"].replace("\r", ""), 600) + "\n",
            "Run WITHOUT sshd's ChrootDirectory: the relay's ssh asks on /dev/tty, and the chroot has none (inside the "
            "chroot this variant fails even on a terminal). "
            "Passing the typed password on to the board works only where there is a terminal: the prompt comes from the "
            "relay's ssh (`pi@fpgas-fleet's password:`), and scp, sftp, rsync and plain remote commands have no terminal "
            "to ask on, so they fail. Every wrong guess is also a failed login AT THE BOARD from the gateway's address.")

    lab.start_gateway(variant="jump")
    dexec(a, ["sh", "-c", "rm -f /root/askpass.log /root/.ssh/known_hosts"])
    jump = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-J", f"{BOARD}@{SITE}"]
    r, s = run_visitor([*jump, f"pi@{board_ip}", "hostname; echo \"$SSH_CONNECTION\""])
    asked = lab.prompts()
    r2, s2 = run_visitor([*jump, f"pi@{lab.GW_BOARDS}", "hostname"])
    r3, s3 = run_visitor([*jump, f"pi@{lab.BOARDS[BOARD2].ip}", "hostname"])
    script = (f"ssh -J {BOARD}@{SITE} -o ExitOnForwardFailure=yes -L 18000:127.0.0.1:8000 pi@{board_ip} 'sleep 4' & "
              "sleep 2.5; printf 'GET / HTTP/1.0\\r\\n\\r\\n' | nc -w 2 127.0.0.1 18000 | tail -1; wait")
    r4, s4 = run_visitor(["sh", "-c", script])
    r5, s5 = ssh(BOARD, "hostname")
    known = dexec(a, ["sh", "-c", "ssh-keygen -l -f /root/.ssh/known_hosts"]).out
    rep.add("J-jump", "`ssh -J <board-name>@<site> pi@<board address>`: sshd itself opens the connection (PermitOpen)",
            r.out.split()[0] == BOARD and r2.rc != 0 and "hello from" in r4.out and r5.out.strip() == BOARD,
            s + "# prompts: " + json.dumps(asked) + "\n" + s2 + s3 + s4 + s5 + "# the visitor's known_hosts afterwards:\n" + known,
            "End-to-end ssh to the board through a direct-tcpip channel: two password prompts (gateway, then board), two "
            "host keys (the gateway's, then the fleet key under the board's private address), and the visitor's own -L, "
            "keys and sftp work because the gateway only carries bytes. The command is not the one-liner any more. "
            "PermitOpen cannot be derived from the login name: with one shared uid any board name may jump to any "
            "board's port 22 (third command), and to nothing else (second command). The plain `ssh <board-name>@<site>` keeps working beside it.")

    lab.start_gateway(variant="rawtcp")
    dexec(a, ["sh", "-c", "rm -f /root/askpass.log"])
    argv = ["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", f"ProxyCommand=ssh -o StrictHostKeyChecking=accept-new {BOARD}@{SITE}",
            "-o", "HostKeyAlias=fleet.welland.lab", "pi@board", "hostname"]
    r, s = run_visitor(argv)
    asked = lab.prompts()
    rep.add("J-rawtcp", "ForceCommand as a plain TCP pipe (nc) with a client-side ProxyCommand", r.out.strip() == BOARD,
            s + "# prompts: " + json.dumps(asked) + "\n" + gw(["cat", "/usr/local/bin/raw-relay"]).out,
            "Works, and is the same thing for the visitor as J-jump with one more process on the gateway. It needs a "
            "ProxyCommand in the visitor's command or configuration.")


# --------------------------------------------------------------------------
# 6. Failures a visitor will meet


def g_failures(rep: Report) -> None:
    rep.section("6. Failure behaviour", "")
    lab.start_gateway()
    a = lab.client("a")

    r, s = ssh(lab.EMPTY_PORT, "hostname")
    rep.add("F-empty", "a port with no board", r.rc == 255 and r.secs < 8, s,
            "The gateway accepts the login, the relay's connection attempt fails, and ssh's own message says so.")
    name = "pi-sw1-p5"
    lab.run(["docker", "pause", lab.BOARDS[name].container])
    r, s = ssh(name, "hostname")
    lab.run(["docker", "unpause", lab.BOARDS[name].container])
    rep.add("F-down", "a board that stopped answering (frozen)", r.rc == 255 and r.secs < 9, s, "ConnectTimeout 5 in the relay's ssh_config.")
    r, s = ssh("pi-sw2-p45", "hostname")
    rep.add("F-hostkey", "a board presenting a key that is not the fleet key", r.rc == 255 and "verification failed" in r.text.lower(), s,
            "The relay pins the fleet key (one known_hosts line, HostKeyAlias) and refuses anything else.")
    r, s = ssh("pi-sw2-p44", "hostname")
    rep.add("F-changed-pw", "a board whose root changed pi's password", r.rc == 255 and "Permission denied" in r.text, s,
            "The gateway's check passed, the board's did not. The message comes from the relay's ssh.")
    r, s = ssh(BOARD, "printf '\\033]0;title set by the board\\007\\033[2J'; head -c 300000 /dev/urandom | base64 | head -c 200000 | wc -c")
    rep.add("F-hostile-output", "a board that sends escape sequences and bulk output", r.rc == 0, s[:600] + "\n",
            "Passed through byte for byte to the visitor's terminal, as on a direct login: the visitor's own concern.")

    dexec(a, ["rm", "-f", "/root/askpass.log"])
    mark = lab.gw_log_mark()
    before = len(lab.board_log(BOARD))
    r, s = ssh(BOARD, "hostname", password="not-the-password")
    asked = lab.prompts()
    after = len(lab.board_log(BOARD))
    rep.add("F-wrong-pw", "a wrong password", r.rc == 255 and after == before,
            s + "# prompts shown: " + json.dumps(asked) + "\n# gateway log:\n" +
            interesting(lab.gw_log_since(mark), "Failed|maximum|Too many|penal") +
            f"\n# new lines in the board's sshd log during this: {after - before}\n",
            "Three prompts (the client's default), about 8 seconds, then the gateway closes. The board never hears of it.")

    out = ""
    asked_total = 0
    for user in (lab.ADMIN, "root", "nosuchuser"):
        dexec(a, ["rm", "-f", "/root/askpass.log"])
        r = visitor(["ssh", "-o", "PubkeyAuthentication=no", f"{user}@{SITE}", "true"], password="guess")
        asked = len(lab.prompts())
        asked_total += asked
        out += f"$ ssh -o PubkeyAuthentication=no {user}@{SITE}\n{clip(r.text)}\n[exit {r.rc}, {r.secs:.1f} s; password prompts: {asked}]\n"
    r = admin("b")
    out += f"$ ssh -i admin_key {lab.ADMIN}@{SITE} hostname      (the administrator, by key)\n{r.text}\n[exit {r.rc}, {r.secs:.1f} s]\n"
    res = gw_sh("sshd -T -C user=alice,host=x,addr=192.0.2.1 | grep -E '^(passwordauthentication|kbdinteractiveauthentication|authenticationmethods|permitemptypasswords|forcecommand|chrootdirectory) '")
    out += "gw# sshd -T -C user=alice ... (the administrator's effective settings)\n" + res.out
    rep.add("F-admin-names", "password guessing against administrators' and unknown names",
            r.out.strip() == "gw" and asked_total == 0 and "passwordauthentication no" in res.out, out,
            "The key-only rule is untouched: those names are offered `publickey` and nothing else; there is no prompt to guess at.")


# --------------------------------------------------------------------------
# 7. Abuse: can board-name logins lock administrators out?


def admin_watch(seconds: float, which: str, results: list[tuple[float, int, float]]) -> None:
    end = time.monotonic() + seconds
    start = time.monotonic()
    while time.monotonic() < end:
        r = admin(which, timeout=20)
        results.append((time.monotonic() - start, r.rc, r.secs))
        time.sleep(0.5)


def summarise(results: list[tuple[float, int, float]]) -> str:
    good = [r for r in results if r[1] == 0]
    slow = max((r[2] for r in good), default=0.0)
    return f"{len(good)} of {len(results)} key logins succeeded; slowest success {slow:.1f} s"


def flood_case(rep: Report, tid: str, title: str, startups: str, kind: str, sources: tuple[int, int] | None, finding: str,
               seconds: int = 30) -> None:
    lab.start_gateway(startups=startups)
    a = lab.client("a")
    first, last = sources if sources else (21, 21)
    mark = lab.gw_log_mark()
    res_b: list[tuple[float, int, float]] = []
    res_a: list[tuple[float, int, float]] = []
    holder: dict[str, lab.Result] = {}

    def attack() -> None:
        if kind == "password":
            holder["r"] = dexec(a, ["flood.sh", BOARD, str(seconds), lab.UP4, str(first), str(last)], timeout=seconds + 60,
                                env={**lab.ssh_env("wrong-guess"), "LAB_ASKPASS_LOG": "/dev/null"})
        else:
            extra = [lab.UP4, str(first), str(last)] if sources else []
            holder["r"] = dexec(a, ["holdopen.py", SITE, "22", "160", str(seconds), *extra], timeout=seconds + 60)

    t = threading.Thread(target=attack)
    t.start()
    time.sleep(3)
    watchers = [threading.Thread(target=admin_watch, args=(seconds - 6, "b", res_b))]
    if not sources:
        watchers.append(threading.Thread(target=admin_watch, args=(seconds - 6, "a", res_a)))
    for w in watchers:
        w.start()
    time.sleep((seconds - 6) / 2)
    listener = gw_sh("ps -eo args | grep '[l]istener'").out.strip()
    for w in watchers:
        w.join()
    t.join()
    log = lab.gw_log_since(mark)
    counts = {
        "connections accepted": sum("Connection from" in line for line in log),
        "dropped for a penalty": sum("penalty" in line and "drop connection" in line for line in log),
        "dropped past MaxStartups": sum("past Maxstartups" in line or "MaxStartups" in line and "drop" in line for line in log),
        "dropped past PerSourceMaxStartups": sum("per-source" in line.lower() and "drop" in line for line in log),
        "failed passwords": sum("Failed password" in line for line in log),
    }
    samples = sorted({re.sub(r"port \d+|#\d+|\d+ seconds|\[\S+\]:\d+", "*", line)[:150] for line in log
                      if re.search(r"drop|penal|exited MaxStartups|throttl", line)})[:8]
    src = f"{last - first + 1} source addresses" if sources else "one source address (the abuser's)"
    out = (f"# gateway sshd: {'defaults' if startups == 'default' else 'PerSourceMaxStartups 10, LoginGraceTime 30'}; "
           f"attack for {seconds} s from {src}\n")
    out += f"# attacker's view:\n{clip(holder['r'].text, 900)}\n"
    out += f"# sshd listener mid-attack: {listener}\n"
    out += f"# administrator from ANOTHER address: {summarise(res_b)}\n"
    if res_a:
        out += f"# administrator from the ABUSER'S address: {summarise(res_a)}\n"
    out += "# gateway log counts: " + json.dumps(counts) + "\n# gateway log samples:\n" + "\n".join(samples) + "\n"
    good_b = sum(1 for r in res_b if r[1] == 0)
    rep.add(tid, title, None, out, finding + f" Measured: administrator from another address, {summarise(res_b)}.")
    rep.rows[-1] = (tid, title, f"{good_b}/{len(res_b)} admin logins")


def g_abuse(rep: Report) -> None:
    rep.section("7. Abuse: can board-name logins lock administrators out?", """
The proxy's catch-all was dropped because wrong passwords through it held the
gateway sshd's unauthenticated slots (MaxStartups 10:30:100) until
administrators' key logins were dropped. Here the same sshd takes the board
names directly. Throughout each attack an administrator logs in by key every
half second from another address (client b), and where the attack has one
source also from the attacker's own address.
""")
    flood_case(rep, "X-guess-1", "wrong-password flood for a board name, one source, sshd defaults", "default", "password", None,
               "PerSourcePenalties (on by default since OpenSSH 9.8) stops it: after a few failures the source's new "
               "connections are dropped at accept, before they take a slot. Administrators elsewhere do not notice; "
               "an administrator behind the abuser's address is locked out with them while the penalty lasts.")
    flood_case(rep, "X-guess-60", "wrong-password flood for a board name, 60 sources at once, sshd defaults", "default", "password",
               (lab.MANY_FIRST, lab.MANY_LAST),
               "Each source is penalised separately, so a spread-out flood gets many more attempts in. Each attempt holds "
               "an unauthenticated slot for the two seconds PAM takes to refuse a password.")
    flood_case(rep, "X-hold-1", "160 silent connections held open, one source, sshd defaults", "default", "hold", None,
               "This attack needs no login name at all and works against any sshd, with or without board names: "
               "connections that never authenticate hold slots for LoginGraceTime (120 s).")
    flood_case(rep, "X-hold-1h", "160 silent connections held open, one source, PerSourceMaxStartups 10", "hardened", "hold", None,
               "With a per-source cap the single-source form of the attack is harmless.")
    flood_case(rep, "X-hold-60", "160 silent connections held open, 60 sources, sshd defaults", "default", "hold",
               (lab.MANY_FIRST, lab.MANY_LAST),
               "The spread-out form, for comparison with X-guess-60: it is the stronger attack and owes nothing to board names.")
    flood_case(rep, "X-hold-60h", "160 silent connections held open, 60 sources, PerSourceMaxStartups 10", "hardened", "hold",
               (lab.MANY_FIRST, lab.MANY_LAST),
               "A per-source cap does not stop many sources; a shorter LoginGraceTime shortens it. Nothing in this design changes that.")

    # Successful logins in bulk: resources and clean-up.
    lab.start_gateway(extra_env={"PER_BOARD": "8", "NPROC": "200"})
    a = lab.client("a")
    boards = [BOARD, BOARD2, "pi-sw2-p46"]

    def one(i: int) -> lab.Result:
        return visitor(["ssh", f"{boards[i % 3]}@{SITE}", "sleep 8; hostname"], timeout=60)

    with concurrent.futures.ThreadPoolExecutor(36) as pool:
        futures = [pool.submit(one, i) for i in range(36)]
        time.sleep(5)
        during = relay_proc_count()
        r_admin = admin("b")
        results = [f.result() for f in futures]
    good = sum(1 for r in results if r.rc == 0)
    msgs: dict[str, int] = {}
    for r in results:
        if r.rc != 0:
            key = re.sub(r"port \d+", "port N", clip(r.text).splitlines()[-1] if clip(r.text) else "?")
            msgs[key] = msgs.get(key, 0) + 1
    time.sleep(1)
    nft = gw(["nft", "list", "chain", "inet", "fpgas_board_relay", "board_relay"]).out
    rep.add("X-many", "36 logins at once to three boards (limit: 8 connections per board)", r_admin.rc == 0 and relay_proc_count() == 0,
            f"sessions that ran: {good} of 36\nrefused: {json.dumps(msgs, indent=1)}\nprocesses of uid 950 during: {during}\n"
            f"administrator's key login during: exit {r_admin.rc} in {r_admin.secs:.1f} s\nprocesses of uid 950 afterwards: {relay_proc_count()}\n{nft}",
            "Authenticated sessions do not count against MaxStartups, so they cannot crowd out administrators. The "
            "nftables `ct count` rule bounds connections per board; the surplus see `Connection refused`.")

    lab.start_gateway(extra_env={"PER_BOARD": "100", "NPROC": "30"})
    with concurrent.futures.ThreadPoolExecutor(30) as pool:
        futures = [pool.submit(one, i) for i in range(30)]
        time.sleep(5)
        during = relay_proc_count()
        r_admin = admin("b")
        results = [f.result() for f in futures]
    good = sum(1 for r in results if r.rc == 0)
    msgs = {}
    for r in results:
        if r.rc != 0:
            key = re.sub(r"port \d+", "port N", clip(r.text).splitlines()[-1] if clip(r.text) else "?")[:120]
            msgs[key] = msgs.get(key, 0) + 1
    rep.add("X-nproc", "30 logins at once with a process limit of 30 for the shared uid (pam_limits nproc)", r_admin.rc == 0,
            f"sessions that ran: {good} of 30\nrefused: {json.dumps(msgs, indent=1)}\nprocesses of uid 950 during: {during}\n"
            f"administrator's key login during: exit {r_admin.rc} in {r_admin.secs:.1f} s\n",
            "One shared uid means one process budget for all visitors together (two processes per session). When it is "
            "used up, further board logins fail; administrators are separate users and are unaffected.")

    lab.start_gateway()
    dexec(a, ["sh", "-c", f"SSH_ASKPASS=/usr/local/bin/askpass SSH_ASKPASS_REQUIRE=force LAB_PASSWORD={BOARD_PASSWORD} "
                          f"setsid ssh {BOARD}@{SITE} 'sleep 300' > /root/kill-test.log 2>&1 < /dev/null & sleep 3"])
    before = relay_procs()
    dexec(a, ["pkill", "-9", "-x", "ssh"])
    time.sleep(2)
    after = relay_procs()
    rep.add("X-kill", "the visitor's client dies (kill -9) in mid-session", relay_proc_count() == 0,
            f"# uid 950 processes on the gateway during the session:\n{before}\n# two seconds after the client was killed:\n{after}\n")


# --------------------------------------------------------------------------
# 8. Boards with OpenSSH 9.8 or later: PerSourcePenalties on the board


def g_board_penalties(rep: Report) -> None:
    rep.section("8. A future (trixie) board: its own PerSourcePenalties", """
Every relayed login reaches a board from the gateway's address. OpenSSH 9.8+
on a board penalises a source address after failed logins.
""")
    lab.start_gateway()
    out = ""
    ok = True
    for i in range(20):
        r = visitor(["ssh", f"pi-sw2-p46@{SITE}", "hostname"])
        ok = ok and r.out.strip() == "pi-sw2-p46"
    pen = interesting(lab.board_log("pi-sw2-p46"), "penal|drop")
    rep.add("B-good", "20 logins in a row to a trixie board", ok and not pen, f"all 20 succeeded: {ok}\nboard log lines about penalties: {pen or 'none'}\n",
            "The gateway has already checked the password, so the relay's login at the board does not fail and nothing is penalised.")
    for name, label in (("pi-sw2-p43", "no exemption"), ("pi-sw2-p42", f"PerSourcePenaltyExemptList {lab.GW_BOARDS}")):
        out = f"# board {name}: trixie, pi's password changed by its root, {label}\n"
        for i in range(8):
            r = visitor(["ssh", f"{name}@{SITE}", "hostname"])
            out += f"login {i + 1}: {clip(r.text).splitlines()[-1] if clip(r.text) else ''} [{r.secs:.1f} s]\n"
        out += "# board's sshd log:\n" + "\n".join(sorted({re.sub(r"port \d+|#\d+|:\d+ on|\d+ seconds", "*", line) for line in lab.board_log(name) if re.search("penal|drop", line)})) + "\n"
        rep.add(f"B-{name[-3:]}", f"a board whose password no longer matches, {label}", None, out)
    rep.parts.append("""
A visitor with root on a 9.8+ board can make every relayed login to THAT board
fail (change pi's password) and so get the gateway's address penalised on that
board: other visitors, the web terminal and anything else arriving from the
gateway are then dropped by that board until it is rebooted. It affects only
the board the visitor already controls. The exemption line (the same one the
proxy design needed) removes it; it must only be written on a root whose
OpenSSH knows the option.
""")


# --------------------------------------------------------------------------
# 9. Sandbox tools an unprivileged ForceCommand could start


def g_tools(rep: Report) -> None:
    rep.section("9. Sandbox tools a ForceCommand could start without privileges", "")
    probe = "/usr/lib/fpgas-board-relay/probe"
    as950 = ["setpriv", "--reuid", "950", "--regid", "950", "--clear-groups"]
    lab.start_gateway(sandbox="none")
    r, s = ssh(BOARD, "hostname")
    res = gw([*as950, probe, "--tcp", f"{lab.BOARDS[BOARD].ip}:22", "--tcp", f"{lab.BOARDS[BOARD].ip}:8000"])
    rep.add("T-landlock", "Landlock alone (no ChrootDirectory): the relay sandboxes itself", r.out.strip() == BOARD and "landlock=on" in res.out,
            s + "gw# setpriv --reuid 950 ... probe      (uid 950 on the gateway's real root, under the relay's Landlock rules)\n" + res.text + "\n",
            "Landlock needs no privilege, no setuid helper and no user namespace, and works from a ForceCommand as it is. "
            "The relay requires ABI 6 (trixie's kernel has it) and refuses to run without. Alone it hides everything but "
            "/usr and the relay's own files, and allows TCP to port 22 only; but /usr holds shells and interpreters, "
            "which stay executable. sshd's ChrootDirectory removes those, which is why both are used.")

    bw = ["bwrap", "--unshare-all", "--share-net", "--ro-bind", "/usr", "/usr", "--symlink", "usr/lib", "/lib",
          "--symlink", "usr/lib64", "/lib64", "--dev", "/dev", "--die-with-parent", "--cap-drop", "ALL", "/usr/bin/id"]
    res = gw([*as950, *bw])
    out = f"gw# (uid 950, Docker's default seccomp and AppArmor profiles) {shlex.join(bw)}\n{res.text}\n[exit {res.rc}]\n"
    lab.start_gateway(sandbox="chroot", security_opts=("seccomp=unconfined", "apparmor=unconfined"))
    res2 = gw([*as950, *bw])
    out += f"gw# (uid 950, container started with seccomp=unconfined and apparmor=unconfined) same command\n{res2.text}\n[exit {res2.rc}]\n"
    out += gw_sh("stat -c '%A %U %n' /usr/bin/bwrap; echo \"kernel.unprivileged_userns_clone = $(cat /proc/sys/kernel/unprivileged_userns_clone)\"").text + "\n"
    res3 = gw([*as950, probe, "--no-landlock"])
    res4 = gw(["chroot", "--userspec=950:950", "/srv/fpgas-board-relay", probe, "--no-landlock"])
    out += "gw# (uid 950, real root) probe: " + interesting(res3.text.splitlines(), "userns") + "\n"
    out += "gw# (uid 950, inside sshd's ChrootDirectory) probe: " + interesting(res4.text.splitlines(), "userns") + "\n"
    rep.add("T-bwrap", "bubblewrap from an unprivileged process", res2.rc == 0 and "Operation not permitted" in interesting(res4.text.splitlines(), "userns"), out,
            "bwrap on trixie is not setuid; it needs unprivileged user namespaces (Debian's default, "
            "kernel.unprivileged_userns_clone=1). Docker's default profiles forbid creating them, which is a property of "
            "the lab container and not of a trixie host: with them lifted it works. A process inside a chroot may not "
            "create a user namespace at all (user_namespaces(7)), so bwrap and sshd's ChrootDirectory cannot be combined: "
            "it is one or the other.")
    res = gw([*as950, "systemd-run", "--user", "--pipe", "--wait", "true"])
    res2 = gw_sh("grep -a -o -E 'user-light|background-light|user-early|user-incomplete' /usr/lib/x86_64-linux-gnu/security/pam_systemd.so | sort -u; "
                 "systemctl --version | head -1")
    rep.add("T-systemd-run", "systemd-run --user as the shared uid", None,
            f"gw# (uid 950) systemd-run --user --pipe --wait true\n{res.text}\n[exit {res.rc}]\n"
            f"gw# session classes pam_systemd knows; systemd version\n{res2.text}\n",
            "NOT a faithful test: the lab container runs no systemd, so this only shows what happens when the uid has no "
            "user manager. See README.md, 'What could not be verified'.")


# --------------------------------------------------------------------------
# The exact configuration, copied out of the running gateway


def g_config(rep: Report) -> None:
    rep.section("10. The prototype's configuration, as it ran", "Copied out of the gateway container (recommended variant).")
    lab.start_gateway()
    for path in ("/etc/ssh/sshd_config.d/00-pubkey-only.conf", "/etc/ssh/sshd_config.d/60-fpgas-board.conf",
                 "/etc/pam.d/sshd-fpgas-board", "/etc/security/limits.d/fpgas-board.conf", "/etc/nftables-fpgas-board-relay.nft",
                 "/srv/fpgas-board-relay/etc/board-relay/ssh_config", "/srv/fpgas-board-relay/etc/board-relay/site.conf",
                 "/srv/fpgas-board-relay/etc/passwd"):
        rep.parts.append(f"\n`{path}`\n\n```\n{gw(['cat', path]).out.rstrip()}\n```\n")
    res = gw_sh("grep -E '^(passwd|shadow|group)' /etc/nsswitch.conf; echo; head -3 /var/lib/extrausers/passwd; echo ...; "
                "wc -l < /var/lib/extrausers/passwd; echo; grep fpgas-board /etc/passwd /etc/group; echo; "
                "sed -E 's/^(fpgas-fleet ssh-ed25519 .{12}).*/\\1.../' /srv/fpgas-board-relay/etc/board-relay/known_hosts; echo; ls -la /srv/fpgas-board-relay/etc/board-relay")
    rep.parts.append(f"\nName lookup, the shared identity, the pinned fleet key:\n\n```\n{res.out.rstrip()}\n```\n")
    res = gw_sh("sshd -T -C user=pi-sw2-p47,host=x,addr=192.0.2.1 | sort > /root/board.txt; sshd -T -C user=alice,host=x,addr=192.0.2.1 | sort > /root/admin.txt; "
                "diff /root/admin.txt /root/board.txt | grep '^[<>]'")
    rep.parts.append("\nEvery effective sshd setting that differs between an administrator (`<`) and a board name (`>`), from `sshd -T -C user=...`:\n\n"
                     f"```\n{res.out.rstrip()}\n```\n")


GROUPS = {"lookup": g_lookup, "directives": g_directives, "visitor": g_visitor, "sandbox": g_sandbox, "variants": g_variants,
          "failures": g_failures, "abuse": g_abuse, "boards": g_board_penalties, "tools": g_tools, "config": g_config}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", nargs="?", default="run", choices=["run", "up", "down"])
    parser.add_argument("--only", default="", help="comma-separated groups: " + ", ".join(GROUPS))
    parser.add_argument("--lookup", default="extrausers", help="for `up`")
    parser.add_argument("--variant", default="password", help="for `up`")
    parser.add_argument("--sandbox", default="chroot", help="for `up`")
    parser.add_argument("--keep", action="store_true", help="leave containers and images in place afterwards")
    parser.add_argument("--reuse", action="store_true", help="use the lab that `up` left running")
    parser.add_argument("--out", default="", help="results file (default: tmp/sshd_native_routing/results.md)")
    args = parser.parse_args()

    if args.action == "down":
        lab.teardown()
        return 0
    if not args.reuse:
        lab.teardown(remove_images=False)
        lab.build_images()
        lab.start_lab()
    if args.action == "up":
        lab.start_gateway(lookup=args.lookup, variant=args.variant, sandbox=args.sandbox)
        print(f"lab is up: gateway {GW}, clients {lab.client('a')} {lab.client('b')}")
        return 0

    started = now()
    wanted = [g.strip() for g in args.only.split(",") if g.strip()] or list(GROUPS)
    rep = Report()
    try:
        lab.start_gateway()
        versions = {"gateway": gw_sh("sshd -V 2>&1; . /etc/os-release; echo \"$PRETTY_NAME\"; uname -r").text.replace("\n", "; ")}
        versions["board (bookworm)"] = dexec(lab.BOARDS[BOARD].container, ["sh", "-c", "sshd -V 2>&1 | tail -1"]).text
        versions["board (trixie)"] = dexec(lab.BOARDS["pi-sw2-p46"].container, ["sh", "-c", "sshd -V 2>&1 | tail -1"]).text
        versions["client"] = dexec(lab.client("a"), ["sh", "-c", "ssh -V 2>&1"]).text
        for name in wanted:
            prepare_clients()
            GROUPS[name](rep)
    finally:
        header = (f"# sshd-native board routing: lab results\n\nRun started {started}, finished {now()}.\n"
                  f"Groups run: {', '.join(wanted)}.\n\n" +
                  "".join(f"- {k}: {v}\n" for k, v in versions.items()) +
                  "\nBoards in the lab:\n\n" + "".join(f"- `{b.name}` at {b.ip}: {b.what}\n" for b in lab.BOARDS.values()) +
                  f"- `{lab.EMPTY_PORT}`: nothing plugged in\n")
        out = Path(args.out) if args.out else lab.WORK / "results.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rep.render(header))
        print(f"\nresults: {out}")
        if not args.keep:
            lab.teardown()
    return 1 if rep.failed else 0


if __name__ == "__main__":
    sys.exit(main())
