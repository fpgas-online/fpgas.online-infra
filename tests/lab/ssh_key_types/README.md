# ssh "key-types trick" lab

A lab test, with real ssh clients and real sshpiper proxies, of a claim that
a design for `ssh pi-sw2-p47@welland.fpgas.online` rested on.

## The situation

One public name, port 22, is answered by two different sshpiper proxies with
two different host keys (they must not share a private key): one proxy
answers over IPv4 (on the site's upstream gateway), the other over IPv6 (on
the site's own gateway). A visitor reaches either one, depending on the
address family, on any day.

## The claim

> If proxy A presents only an ECDSA host key and proxy B only an Ed25519 host
> key, and neither forwards the `hostkeys-00@openssh.com` announcement
> (`sshpiperd --drop-hostkeys-message`), an OpenSSH client keeps both keys
> under the one name and never shows "REMOTE HOST IDENTIFICATION HAS
> CHANGED"; at worst it asks once per proxy to accept a new key.

## Result (2026-10-04): the claim DOES NOT HOLD

Every OpenSSH client tested (8.2, 8.9, 9.2, 9.6, 10.0, 10.5) **refuses the
second proxy with "REMOTE HOST IDENTIFICATION HAS CHANGED"** on first contact,
in either order, with `StrictHostKeyChecking` at `ask` (the default) and at
`accept-new`. It never offers to add the second key. What OpenSSH 10.0 prints
at the Ed25519 proxy after accepting the ECDSA one:

```
@    WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!     @
IT IS POSSIBLE THAT SOMEONE IS DOING SOMETHING NASTY!
...
The fingerprint for the ED25519 key sent by the remote host is
SHA256:iq+g/ye+...
Add correct host key in /root/.ssh/known_hosts to get rid of this message.
Offending ECDSA key in /root/.ssh/known_hosts:1
Host key for welland.fpgas.online has changed and you have requested strict checking.
Host key verification failed.
```

Why: OpenSSH's known_hosts check is not per key type. In
`check_hostkeys_by_key_or_type()` (`hostfile.c`), when the presented key
equals none of the keys stored for the name, **any** stored key for that
name, of whatever type, makes the result `HOST_CHANGED`. `HOST_NEW` (the
"accept this new key?" question) is only reached when the name has no key at
all. A different key type does not make a key "new".

The part of the claim about negotiation is true and is not enough: with only
an ECDSA key known the client moves ECDSA to the front of its proposal but
still offers Ed25519, the Ed25519-only proxy negotiates fine, and then the
key it presents is refused (scenarios 2a, 2b).

A visitor who follows the advice in the refusal (`ssh-keygen -R
welland.fpgas.online`) deletes the first proxy's key, accepts the second
proxy's, and is refused again the next time the first proxy answers
(scenario 1g). It never settles.

## What does work, and its conditions

**Both keys already in the visitor's known_hosts before the first visit**
(for example two published `known_hosts` lines the visitor pastes). Then
every client tested alternates between the proxies silently, with
`StrictHostKeyChecking` at `ask` or `yes` (6a, 6b). No key is ever learned at
connect time, so this is a documentation step for each visitor, not something
the servers can arrange. One key pasted is not enough (6l).

For that variant the lab shows these conditions matter:

| Condition | What happens otherwise (OpenSSH) | Scenario |
|---|---|---|
| Neither server may announce only its own key after login. | A server that announces (`hostkeys-00`, what plain sshd does) makes OpenSSH 8.5 and later, default config, silently delete the other proxy's key from known_hosts ("Deprecating obsolete hostkey", visible only with `-v`). The next visit to the other proxy is refused as CHANGED. OpenSSH 8.2 does the same once `UpdateHostKeys=yes` is set. | 6d, 6e |
| sshpiper runs with `--drop-hostkeys-message`. | Without it sshpiper forwards the *backend's* announcement (the Pi's own host keys). The client asks for proof, the proxy cannot give it, and the visitor sees `client_global_hostkeys_prove_confirm: server gave bad signature for RSA key 0` on every login. In this lab nothing was removed from known_hosts, because the backend's keys were not already known under the name; the flag is still required. With the flag: no announcement reaches the client and both keys stay, even with `UpdateHostKeys=yes` forced. | 6f, 6g, 6c |
| Each proxy offers exactly one key type, and the two types differ. | Same type, different keys: CHANGED (the control). If one proxy also offers the other's type, the outcome depends on the client's default algorithm order: OpenSSH 8.2 (ECDSA first) is refused, 8.9 and later (Ed25519 first) happen to work. | 4a, 6h |
| The visitor does not pin `HostKeyAlgorithms` to one type. | The proxy with the other type cannot be reached at all ("no matching host key type found"). | 6i |
| A visitor using `HostKeyAlias` stores both keys under the alias. | Keys stored under the real name are not consulted: prompt, then CHANGED. | 6j, 6k |

OpenSSH 8.2 additionally prints, once per proxy address, `Warning:
Permanently added the ECDSA host key for IP address ...` (`CheckHostIP`
defaulted to yes before 8.5). It connects.

## Results table (run of 2026-10-04, 23:11 to 23:14 ACDT)

"A" is the ECDSA-only proxy reached over IPv4, "B" the Ed25519-only proxy
reached over IPv6, both sshpiper with `--drop-hostkeys-message` unless the
row says otherwise. The six OpenSSH clients behaved identically except where
a version is named.

| Scenario | What the visitor sees |
|---|---|
| 1a/1b default config, nothing known, A B A B (and B A B A) | first proxy: asked to accept the key; second proxy: **CHANGED refusal**; first again: silent; second again: CHANGED refusal |
| 1c/1d `StrictHostKeyChecking=accept-new` | first proxy: key added without asking; second proxy: **CHANGED refusal**, every time |
| 1e `StrictHostKeyChecking=yes`, 1f `BatchMode=yes`, nothing known | both proxies refused (no known key), as for any new host |
| 1g visitor runs `ssh-keygen -R` after each refusal | asked again, connects, then refused at the other proxy; repeats for ever |
| 2a/2b one type known, other proxy answers | negotiation succeeds (the proposal is reordered, not restricted), then CHANGED refusal |
| 3a plain sshd pair, nothing known | same as 1a |
| 3b sshpiper pair without `--drop-hostkeys-message`, nothing known | same as 1a; the client receives one forwarded announcement per login |
| 4a control: both proxies ECDSA, different keys | CHANGED refusal at the second |
| 4b/4c A ECDSA only, B offers ECDSA and Ed25519, nothing known | CHANGED refusal at the second proxy in either order |
| 4d/4e `HostKeyAlgorithms` pinned to one type | the other proxy fails with "no matching host key type found" |
| 4f `HostKeyAlias`, nothing known | same as 1a |
| 6a/6b both keys pre-seeded | silent at both proxies, any order, repeatedly |
| 6c both pre-seeded, `UpdateHostKeys=yes` forced | silent; both keys stay |
| 6d both pre-seeded, plain sshd pair (each announces its own key), default config | 8.5 and later: A silent and B's key is deleted, then B CHANGED refusal. 8.2: silent, keys stay (UpdateHostKeys off by default) |
| 6e as 6d with `UpdateHostKeys=yes` forced | all versions: other key deleted, then CHANGED refusal |
| 6f/6g both pre-seeded, sshpiper without `--drop-hostkeys-message` | connects and keys stay, but a "server gave bad signature" error is printed on each login where UpdateHostKeys is on |
| 6h pre-seeded A-ECDSA and B-Ed25519, B also offers an ECDSA key | 8.2: CHANGED refusal at B. 8.9 and later: silent |
| 6i both pre-seeded, client pinned to Ed25519 | A unreachable, B silent |
| 6j/6k `HostKeyAlias` | keys under the name: prompt then CHANGED; keys under the alias: silent |
| 6l only A's key pre-seeded | A silent, B CHANGED refusal |
| 5a/5b dropbear `dbclient` 2025.89 | **works as the claim said**: asks once per proxy (or adds silently with `-y`), then silent; it matches known_hosts lines by key type |
| 5c dbclient control, same type | "host key mismatch", refused |
| 5d PuTTY `plink` 0.83 | **works as the claim said**: "host key is not cached" question once per proxy, then silent, also with `-batch`; it caches one key per type per host and port |
| 5e/5f plink `-batch` first contact; control, same type | refused ("Cannot confirm a host key in batch mode"); "POTENTIAL SECURITY BREACH", refused |

The complete output of that run is in `results-2026-10-04.md`.

## Not tested

- **SSHFP / `VerifyHostKeyDNS`.** Needs a DNSSEC-validating resolver, which
  the lab does not build. From the OpenSSH 10.0 source (`verify_host_key()`
  in `sshconnect.c`): with `VerifyHostKeyDNS yes`, a matching SSHFP record
  and a DNSSEC-secure answer, the key is accepted before known_hosts is
  looked at, and nothing is written to known_hosts; so two proxies with two
  published fingerprints would both be accepted silently. With
  `VerifyHostKeyDNS ask`, or a matching record that is not DNSSEC-secure,
  the client falls through to the normal known_hosts check, so the second
  proxy is still refused as CHANGED once the first key is stored.
  `VerifyHostKeyDNS` defaults to `no`, and "secure" needs the client's
  resolver to pass on the validating resolver's AD bit (with glibc,
  `options trust-ad` in `resolv.conf`). This is client-side configuration,
  not something a default visitor has.
- Windows and macOS builds of OpenSSH (same code; not run), and PuTTY's GUI
  (only `plink` was run).
- The `ask` prompt was driven through `SSH_ASKPASS` (which answered "yes"
  and recorded each question), not through a terminal.
- A host certificate signed by one CA on both proxies. That is the standard
  way to have several keys under one name, but it needs a
  `@cert-authority` line in each visitor's known_hosts.

## How it is built

- Private Docker network (`--internal`, IPv4 and IPv6). No ssh leaves it.
- Backend: plain sshd with the login `pi-sw2-p47` (password auth).
- Proxies: `sshpiperd` built from
  [fpgas-online/sshpiper](https://github.com/fpgas-online/sshpiper) at the
  commit pinned in the script, with the `fixed` plugin routing every login to
  the backend; one container per variant (ECDSA only, Ed25519 only, with and
  without `--drop-hostkeys-message`, a second ECDSA key, both types). Two
  plain sshd containers with one host key each stand in for "a server that
  announces its own keys".
- Clients: one container per distribution, each with the distribution's own
  `ssh_config` and a throwaway `/root` so that known_hosts is at its default
  path (`UpdateHostKeys` defaults to yes only for the default path, with
  `VerifyHostKeyDNS` off, from OpenSSH 8.5). The name is pointed at one
  proxy's IPv4 address or the other's IPv6 address in `/etc/hosts` before
  each connection; the command is always
  `ssh pi-sw2-p47@welland.fpgas.online`.

## Running it

```bash
uv run tests/lab/ssh_key_types/ssh_key_types_lab.py            # all clients
uv run tests/lab/ssh_key_types/ssh_key_types_lab.py --clients debian-trixie
uv run tests/lab/ssh_key_types/ssh_key_types_lab.py --keep     # leave the lab up to poke at
```

Needs Docker, Go (to build sshpiper), `ssh-keygen`, and network access for
the image builds. Takes a few minutes. It is not collected by pytest and is
not part of CI. Work files (sshpiper checkout, keys, the full transcript
with every client's `-vv` output, `results.json`) go to `tmp/ssh_key_types/`
at the repository root, which is git-ignored. At the end it removes its
containers, its network, its own images and any base image it pulled.

The exit status is 0 when every connection behaved as recorded above, 1 when
any differs (for example with a new OpenSSH release).
