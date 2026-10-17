# sshd-native board routing lab

An exploration, with a tested prototype, of routing board login names
**inside the gateway's own sshd** instead of through a separate
username-routing proxy:

```
ssh pi-sw2-p47@<site>        # lands on that board
ssh <administrator>@<site>   # stays an ordinary key login to the gateway
```

Both on the gateway's normal sshd, port 22, one host key.

The question it answers (the owner's words, 2026-10-05):

> explore if ssh offers some type of plugin/extension system (maybe a custom
> nsswitch module?), that enables doing the normal flow for some users and
> enables mapping users onto a specific, unprivlidged and heavily
> sandbox/restricted process that initiaes the second connection part.

Status: an exploration. Nothing here is deployed, and no role uses it. The
on-hold proxy design it is compared with is
`docs/superpowers/specs/2026-10-03-ssh-username-proxy-design.md` on the
branch `ssh-proxy-spec-rev2` (tracking issue #191).

Results: [results-2026-10-05.md](results-2026-10-05.md). Test ids below
(V-scp, S-probe, ...) refer to it.

## Answer

OpenSSH has no plugin system. It has four standard seams, and together they
do exactly what was asked:

| Seam | What it decides | Used here for |
|---|---|---|
| NSS (`getpwnam`) | which login names exist | board names exist, with one shared unprivileged uid |
| PAM, and `PAMServiceName` in `Match` | how a name authenticates | the public board password, for board names only |
| `Match` blocks in `sshd_config` | per-name policy | password instead of keys, no forwarding, chroot |
| `ForceCommand` and the login shell | what runs after login | the relay, never a shell |

The design that was built and tested:

1. Board names exist through **one generated file** read by the standard
   `libnss-extrausers` package; all of them share **one unprivileged uid**.
   (A custom NSS module that answers by formula also works and was built;
   see the comparison below.)
2. One `Match Group` block gives those names, and only those, password
   login with the **public board password**. Administrators' settings do
   not change: keys only.
3. After login sshd **chroots** the session into a tree that holds an ssh
   client, the relay and its configuration files, and runs the **relay**: a
   small static program that is both the account's login shell and the
   forced command. There is no shell in the tree.
4. The relay takes the board from the login name sshd authenticated,
   computes the address by the site formula, **sandboxes itself with
   Landlock** (no files, TCP port 22 only), and starts the ssh client to
   that one address with a fixed argument list. The visitor's requested
   command travels as one opaque argument that only the board's shell reads.
5. An **nftables rule keyed on the shared uid** allows that uid's processes
   to connect to port 22 of board addresses and to nothing else.
6. The relay logs in to the board with the public password, so the visitor
   types **one password**, sees **one host key** (the gateway's own), and
   scp, sftp and rsync work.

It meets the sentence: normal flow for some users, and the others mapped
onto one unprivileged, sandboxed process that opens the second connection.

## How a login runs

```
visitor's ssh ──► gateway sshd (port 22, the gateway's host key)
                    │  getpwnam("pi-sw2-p47") ── NSS ──► uid 950, shell = relay
                    │  Match Group fpgas-board ──► password only, PAM stack sshd-fpgas-board
                    │  password = the public board password?  (pam_unix)
                    ▼
                  sshd-session [priv]      root: holds the pty and the PAM session
                    └─ sshd-session        uid 950, chrooted to /srv/fpgas-board-relay
                         └─ relay ──exec─► ssh -F /etc/board-relay/ssh_config -tt -- 10.21.2.47 [command]
                                             uid 950, chroot, Landlock, no_new_privs,
                                             nftables: only board addresses, port 22
                                               │  pinned fleet key, public password
                                               ▼
                                            board sshd ──► pi's shell
```

## What each piece is (the prototype's configuration)

The exact files as they ran are in section 10 of the results. In short:

**Names** (`/var/lib/extrausers/passwd`, one line per port, generated from
the port map; `passwd: files extrausers` in `nsswitch.conf`):

```
pi-sw2-p47:x:950:950:fpgas.online board login:/:/usr/lib/fpgas-board-relay/relay
```

with the hash of the public password in `/var/lib/extrausers/shadow`, a real
group `fpgas-board` (gid 950), and one real locked account `fpgas-board`
(uid 950, not in that group) so that `ps` and logs show a name for the uid.

**sshd** (`/etc/ssh/sshd_config.d/60-fpgas-board.conf`; the administrators'
key-only drop-in from `roles/sshd` is unchanged and sorts first):

```
Match Group fpgas-board
    PasswordAuthentication yes
    AuthenticationMethods password
    PermitEmptyPasswords no
    Banner /etc/ssh/fpgas-board-banner
    PubkeyAuthentication no
    KbdInteractiveAuthentication no
    AuthorizedKeysFile none
    MaxAuthTries 3
    ChrootDirectory /srv/fpgas-board-relay
    ForceCommand relay
    Subsystem sftp fpgas-board-sftp
    PermitTTY yes
    DisableForwarding yes
    AllowTcpForwarding no
    PermitOpen none
    AllowStreamLocalForwarding no
    AllowAgentForwarding no
    X11Forwarding no
    PermitTunnel no
    PermitListen none
    GatewayPorts no
    PermitUserRC no
    MaxSessions 4
    ClientAliveInterval 30
    ClientAliveCountMax 4
    PAMServiceName sshd-fpgas-board
```

`Match Group`, not `Match User pi-sw*-p*`: the block then applies to exactly
the names the lookup says are boards, and to nothing that merely looks like
one. `ForceCommand relay` and `Subsystem sftp fpgas-board-sftp` are words
the relay checks, not paths: the account's shell IS the relay, sshd runs
`<shell> -c <ForceCommand>`, and the relay refuses any other invocation.

**PAM** (`/etc/pam.d/sshd-fpgas-board`; administrators keep Debian's stock
`sshd` stack):

```
auth     required  pam_unix.so
account  required  pam_nologin.so
account  required  pam_unix.so
session  required  pam_limits.so
session  required  pam_unix.so
```

**The relay** (`src/board_relay.c`, `src/sandbox.h`, `src/board_name.h`):
static, no NSS, no allocation beyond one `strdup`. Its ssh client
configuration (`/etc/board-relay/ssh_config` inside the chroot) pins the
fleet key with one `known_hosts` line (`HostKeyAlias fpgas-fleet`,
`StrictHostKeyChecking yes`, `UpdateHostKeys no`) and turns off everything
a client could be talked into: escape characters, forwarding, agent,
multiplexing, local commands, proxy commands.

**The sandbox**, in layers, each tested on its own (S-probe, T-landlock):

| Layer | Who applies it | What it removes |
|---|---|---|
| sshd `ChrootDirectory` | sshd, as root, before dropping to uid 950 | the gateway's file system: no shell, no interpreter, no setuid program, no `/proc`, no `/etc` |
| Landlock (ABI 6) + `no_new_privs` + rlimits | the relay, on itself, inherited by ssh | all file access except read/execute under `/usr`, read of its own configuration files and `/dev/null`; all TCP except connect to port 22; signals and abstract unix sockets outside the sandbox |
| nftables, keyed on the uid | the kernel | every packet from uid 950 except TCP to port 22 of a board address: no loopback, no DNS, no other host, no UDP |
| `PR_SET_PDEATHSIG` | the relay | relay processes that outlive the visitor's session |
| pam_limits `nproc`, nftables `ct count` | PAM, the kernel | more than N processes for all visitors together; more than N connections per board |

**The nftables rule** (the board set is generated from the port map):

```
table inet fpgas_board_relay {
    set boards {
        type ipv4_addr
        flags interval
        elements = { 10.21.1.1-10.21.1.48, 10.21.2.1-10.21.2.48 }
    }
    set per_board {
        type ipv4_addr
        flags dynamic
    }
    chain output {
        type filter hook output priority filter; policy accept;
        meta skuid 950 jump board_relay
    }
    chain board_relay {
        ct state established,related accept
        ip daddr @boards tcp dport 22 ct state new add @per_board { ip daddr ct count over 8 } counter reject with tcp reset
        ip daddr @boards tcp dport 22 ct state new counter accept
        counter reject with icmpx admin-prohibited
    }
}
```

A trap found while building it: the obvious first line, `meta skuid != 950
accept`, is wrong. A packet with no socket (IPv6 neighbour discovery, a
kernel-generated reset) has no uid; the comparison does not match in either
direction, and such packets fell through to the final `reject`. Match the
uid positively and jump.

## Mechanisms evaluated

### Making the names exist (results section 1)

sshd rejects a name `getpwnam()` does not find ("Invalid user") before any
authentication, so the names must exist. All four ways below were run; with
each, `Match` sees the name the client typed, the log says `Accepted
password for pi-sw2-p47`, `$USER` in the session is that name, and every
name shares uid 950.

| Mechanism | Works | What it is | Cost |
|---|---|---|---|
| Real accounts, `useradd -o -u 950` per port | yes | lines in `/etc/passwd` and `/etc/shadow` | one account per port in the system's own files; a play that loops over every port |
| **`libnss-extrausers`** (recommended) | yes | one generated `passwd` and one `shadow` file under `/var/lib/extrausers`, a Debian package, two words in `nsswitch.conf` | a list still exists, as one templated file; it changes only when a switch is added |
| Custom NSS module (`src/nss_fpgasboard.c`) | yes | answers `getpwnam`/`getspnam` for any name matching the formula inside the site's shape; no list | our own C code loaded into every process that looks up a user, including sshd's root monitor, with the attacker's string as input; a package to build and maintain. `nsswitch` asks it only for names that are not real accounts |
| systemd userdb drop-ins (`/etc/userdb/*.user`) | yes | one JSON record per name (plus a `.user-privileged` file for the hash), read through `nss-systemd`, already in Debian's `nsswitch.conf` | two files per port; many records may share a uid; lookups by name work without systemd running. The uid's own name comes from elsewhere |
| A PAM module, or any sshd option | no | sshd needs the passwd entry before PAM runs; sshd has no "map this name" option and `ForceCommand` takes no `%u` (H-token) | |

The custom module is what the owner suggested and it does work. It is not
recommended only because the list it avoids is cheap: the nftables set and
the DNS records are generated from the same port map anyway, and the
generated file costs no code that runs as root.

Near-miss names (`pi-sw2-p49` on a 48-port switch, `pi-sw02-p47`,
`PI-SW2-P47`, a 300-character name) are invalid users under all four and
are offered `publickey` only (S-names). Debian's sshd cuts a login name at
the first `/` or `:` (SELinux role, style) before looking it up, so
`pi-sw2-p47/x` is `pi-sw2-p47`.

### Authenticating board names without touching administrators

| Option | Works | What the visitor sees | Notes |
|---|---|---|---|
| **Gateway checks the public password** (`PasswordAuthentication yes` in the `Match` only; `pam_unix` against the shared hash) | yes (V-prompts) | one password prompt, the same as a direct login | recommended. A wrong guess ends at the gateway and never reaches a board (F-wrong-pw) |
| No authentication (`PermitEmptyPasswords yes`, empty password, method `none`) | yes (A-none) | no prompt at all | the name alone is the credential. Honest about the password being public; every scanner that tries a board name gets a session on that board |
| Pass the visitor's password on to the board (gateway `none`, the relay's ssh prompts) | only on a terminal (A-prompt) | `pi@fpgas-fleet's password:` from the relay's ssh | scp, sftp, rsync and plain remote commands fail: there is no terminal to ask on. Needs `/dev/tty` inside the sandbox. Every wrong guess is a failed login at the board from the gateway's address |
| `KbdInteractiveAuthentication` | not needed | | adds a second code path through PAM for no gain |
| Keys for board names (`AuthorizedKeysCommand`) | not built | | possible later for operators; visitors have no keys on the gateway |

For administrators nothing changes, and it was checked three ways: `sshd -T
-C user=<administrator>` shows `passwordauthentication no` and
`authenticationmethods publickey`; a client that asks for password
authentication as an administrator, as root or as an unknown name is offered
`publickey` only and is never prompted; the administrator's key login works
(F-admin-names).

What must stay global, because `Match` cannot set it (H-match):
`MaxStartups`, `PerSourceMaxStartups`, `PerSourcePenalties`,
`LoginGraceTime`, `UsePAM`. These are the things board names share with
administrators; see "Abuse" below.

### The hand-off

| Option | Works | Visitor's command | Notes |
|---|---|---|---|
| **(a) `ForceCommand` runs an ssh client to the board** | yes | `ssh pi-sw2-p47@<site>`, unchanged | recommended; everything in "What works for a visitor" |
| (b) `ForceCommand` runs a TCP pipe (`nc`) | yes (J-rawtcp) | needs `-o ProxyCommand='ssh pi-sw2-p47@<site>' pi@...` | end-to-end ssh to the board; two passwords, two host keys |
| (c) sshd's own `direct-tcpip` with `PermitOpen` | yes (J-jump) | `ssh -J pi-sw2-p47@<site> pi@10.21.2.47` | as (b) with no process on the gateway. `PermitOpen` takes no `%u`: any board name may reach any board's port 22. Can be offered beside (a) by changing the forwarding lines of the Match block |
| `ChrootDirectory` | yes | | sshd does it as root; no helper. Costs three log lines per terminal login (no `/dev/pts` in the tree) and a tree to keep current |
| Landlock from the relay | yes (T-landlock) | | unprivileged, no helper, survives exec |
| bubblewrap from `ForceCommand` | yes on a trixie host, not inside the chroot (T-bwrap) | | not setuid on trixie; needs unprivileged user namespaces; a chrooted process cannot create one, so it is bwrap OR `ChrootDirectory` |
| `systemd-run --user` | no | | see "What could not be verified" |
| seccomp filter | not built | | a production relay could add one; Landlock, the chroot and nftables already bound what a compromised ssh client can do |

## What works for a visitor, compared with a direct login

| Thing | Through a board name | Test |
|---|---|---|
| Interactive shell, terminal size and resize, colours, `TERM` | works | V-prompts |
| Remote command, exit status, quoting | works | V-command, V-quoting |
| `ssh -t` with a command | works | V-tty-command |
| scp, both the sftp mode and `-O` | works | V-scp, V-scp-legacy |
| sftp | works (the board's sftp server answers) | V-sftp |
| rsync | works | V-rsync |
| Binary data through stdin and stdout | works | V-pipe |
| Several sessions over one connection (`ControlMaster`) | works, up to `MaxSessions` | V-mux |
| Over IPv6 to the gateway | works, same key | V-ipv6 |
| `-L`, `-R`, `-D` | **refused, deliberately** | V-L, V-R |
| `ssh -J <board-name>@<site>` | **refused** in the recommended design; a variant allows it | V-J, J-jump |
| X11 forwarding | **off** | V-X11 |
| Agent forwarding | **off**: a board's root could use a forwarded agent | V-agent |
| The visitor's own ssh key | **not accepted**: board names are password-only | |
| `LANG`, `LC_*` from the client | not passed on (the relay passes `TERM` only) | V-env |
| The address the board sees | the gateway's, not the visitor's | V-command |

Prompts, in order, on first contact (V-prompts): the host-key question for
the **gateway's own key** (one key for every board name and for
administrators; it is the key `SSHFP` for `<site>` would publish); the
banner with the public password; **one** password prompt, from the gateway.
The fleet key never reaches the visitor: the relay checks it. The board's
own banner is not shown.

Port forwarding to a board is refused on purpose. To offer it, the J-jump
variant gives visitors end-to-end ssh to the board (their own `-L`, their
own keys once added on the board) at the price of a longer command, two
passwords and a second host key; the gateway then still opens only port 22
of board addresses.

## Failure behaviour (results section 6)

| Situation | What the visitor sees | How fast |
|---|---|---|
| A port with nothing plugged in | `ssh: connect to host 10.21.2.40 port 22: No route to host` | about 3 s |
| A board that has stopped answering | `Connection timed out during banner exchange` | 5 s (`ConnectTimeout`) |
| A board presenting another host key | ssh's "REMOTE HOST IDENTIFICATION HAS CHANGED" text, from the relay | at once |
| A board whose root changed pi's password | `pi@10.21.2.44: Permission denied (publickey,password).` | 2 to 4 s |
| A wrong password | three prompts, then `Too many authentication failures` | about 8 s |
| A name that is not a board | `Permission denied (publickey).`, no prompt | at once |

Unlike the proxy, an empty port and a wrong password look different, and a
wrong password never reaches a board.

## Abuse: can board-name logins lock administrators out? (results section 7)

Measured: an administrator logging in by key every half second from another
address while the attack runs.

| Attack | sshd defaults | With `PerSourceMaxStartups 5` |
|---|---|---|
| Wrong passwords one after another from one address (the pattern that sank the proxy's catch-all) | no effect | |
| Wrong-password flood from one address, forty logins at a time | a brief dip while the first wave holds slots, then `PerSourcePenalties` shuts the address out | no effect |
| Wrong-password flood from sixty addresses | most administrator logins dropped (two thirds to six in seven, over three runs) | about the same |
| Silent connections held open from one address (needs no login name) | most dropped (about three in four) | no effect |
| Silent connections held open from sixty addresses (needs no login name) | most dropped (three in four to six in seven) | about the same |
| Many successful board logins at once | no effect | |

Reading:

- **The proxy's failure does not exist here.** A refused password frees its
  slot at once; no connection onward is made before the password is right;
  nothing leaks.
- **Board names do not create the exposure, but they share it.** Port 22's
  unauthenticated slots (`MaxStartups 10:30:100`) are one pool for
  administrators and visitors, and `Match` cannot split it. A spread-out
  flood drops administrators' logins. The same flood with silent
  connections, which needs no login name and works against the gateway's
  sshd as it is today, does at least as much.
- **`PerSourceMaxStartups` below `MaxStartups`' start value** (5 against 10)
  makes every single-source form harmless to others. It is one global line.
  It, and `PerSourcePenalties`, only work if sshd sees the visitors' real
  addresses.
- An administrator at the abuser's own address is shut out with the abuser
  while a penalty lasts.
- **The only complete separation is a second door**: an administrators-only
  sshd instance on another port has its own slot pool. Here that is a
  fallback for a flood, not the normal route (open question 3).

Per-visitor resources: two processes of the shared uid per session, and with
sshd's monitor roughly 28 MB resident as `ps` counts it (S-procs). One process budget
(`nproc`) and one connection limit per board (`ct count`) bound the total
(X-many, X-nproc). When the visitor's client dies, the relay's ssh dies with
it (X-kill).

## Boards with OpenSSH 9.8 or later (results section 8)

Relayed logins all arrive from the gateway's address. The gateway has
already checked the password, so they do not normally fail and nothing is
penalised (B-good). A visitor with root on such a board can change pi's
password; relayed logins to **that board** then fail, and after six of them
the board dropped the gateway's address (B-p43). With
`PerSourcePenaltyExemptList <gateway's board-side address>` it does not
(B-p42). So the exemption the proxy design needed is needed here too, with
the same rule: only on a root whose OpenSSH knows the option (9.2 refuses
to start with it).

## What the upstream gateway must do

Over IPv4 the site's public port 22 belongs to the upstream gateway.

- **A plain forward of a public TCP port to the site gateway's port 22 is
  enough.** Yes. The site gateway's real sshd answers, with its own host
  key; administrators' key logins pass, because nothing in the path
  terminates ssh. The upstream gateway holds no key and runs no proxy.
- **It must keep the client's source address** (destination NAT only). If
  it rewrote the source, every IPv4 visitor and administrator would be one
  address to sshd: one guesser's penalty would shut all of them out, and
  per-source limits would be useless.
- **For the command to have no `-p`, the forwarded port must be public port
  22.** The upstream gateway's own sshd then cannot also be on public port
  22: that is the same choice the proxy design faced, and the earlier
  decision was for the upstream gateway to keep port 22 with a routing
  proxy of its own (open question 2). The upstream gateway could use this
  very design on its own sshd (board names relayed to the site gateway),
  which keeps its administrators on port 22 too; that was not built.
- **Nothing depends on IPv6.** The visitor reaches the gateway over either
  family (V-ipv6); the hop to the board is IPv4 inside the site. The direct
  IPv6 path to a board is a separate thing and is unaffected.

## Compared with the on-hold proxy design

| | Username-routing proxy (sshpiper) | Inside the gateway's sshd |
|---|---|---|
| Administrators on port 22 | not possible (a key login cannot pass a terminating proxy): port 2223 | **yes, unchanged** |
| Extra daemon | sshpiperd, its plugins, its unit | none |
| Patched package | required (failed onward logins leaked) | none. A small program of our own (the relay) to package |
| Host keys for the site name | a separate site proxy key, shared with the upstream gateway's proxy | **the gateway's own key**, held only on the gateway |
| Upstream gateway | a second proxy holding the site proxy key | a plain port forward |
| Non-key authentication on the gateway's sshd | no | **yes, for board names** |
| A visitor's process on the gateway | no (one proxy process for all) | **yes**: two per session, unprivileged, chrooted, sandboxed |
| What ssh terminates the visitor's session | sshpiper's Go ssh library | OpenSSH's sshd, then OpenSSH's client |
| Board-name list | one pipe per port (generated) | one passwd line per port (generated), or none with the custom NSS module |
| Wrong password | relayed to the board; fails there | refused at the gateway; never reaches a board |
| Empty port | looks like a wrong password, after 3 s | says "No route to host", after 3 s |
| Failed-login limiting | the failtoban plugin | sshd's own `PerSourcePenalties` |
| Forwarding, agent, X11 through a board name | passed through untouched | refused |
| The visitor's own key towards a board | no | no |
| Slots shared between visitors and administrators | no (different ports) | **yes** (`MaxStartups`), see Abuse |
| Board changes | Ed25519-only, penalty exemption | none required; penalty exemption on 9.8+ roots |

## Open questions for the owner

1. **Should a board name ask for the public password at all?** (a) Yes, the
   gateway checks it: one prompt, like a direct login (built, recommended).
   (b) No prompt: the name alone logs in (tested, A-none). (c) Yes, and the
   board checks it: works only for interactive logins, breaks scp, sftp and
   rsync (tested, A-prompt).
2. **What answers public IPv4 port 22 at a site behind an upstream
   gateway?** (a) The upstream gateway forwards port 22 to the site gateway
   and moves its own sshd to another port. (b) The upstream gateway keeps
   port 22 and uses this same design on its own sshd to relay board names
   to the site gateway (not built). (c) Per-board forwarded ports and `-p`
   stay, and the name form works over IPv6 only.
3. **Do administrators get a second door?** (a) No: port 22 only, with
   `PerSourceMaxStartups`; a spread-out flood can delay administrators as
   it can today. (b) A second, administrators-only sshd instance on another
   port as a fallback during a flood. (c) Administrators' addresses
   exempted from penalties, which helps only against penalties, not against
   full slots.
4. **How do the names exist?** (a) One generated file read by
   `libnss-extrausers` (recommended). (b) The custom NSS module: no list,
   our own code in every user lookup. (c) systemd userdb drop-ins: two
   generated files per port, nothing to install.
5. **One uid for all board names, or one per board?** (a) One shared uid
   (built): simplest; the kernel then guarantees "boards' port 22 only",
   and the relay alone guarantees "this name, this board". (b) One uid per
   board: nftables can then also enforce the board per name, and limits and
   signals are per board; costs a uid per port.
6. **Should visitors get port forwarding to their board?** (a) No (built).
   (b) Yes, through `ssh -J <board-name>@<site> pi@<board address>` beside
   the plain command (tested, J-jump): two passwords, two host keys, any
   board name can reach any board's port 22.
7. **Where does the relay come from?** (a) A package in the fpgas.online
   apt repository. (b) Compiled on the gateway by the role (needs a
   compiler there). (c) A shell script instead of a C program: nothing to
   compile, but then a shell must exist where it runs, so no empty chroot.

## What a production version would need

- The relay as a package from the fpgas.online apt repository (it must be
  compiled; static, no dependencies), built with the hardening flags, and
  the site's shape and address prefix written by the role.
- The names file, the `Match` block, the PAM stack, the limits, the
  nftables set and the relay's files as templates of one role, all from
  `switches | port_vlan_map`; the password from `pi_pw` (with a fixed salt
  so the hash does not change on every converge); the fleet key pin from
  the NFS root's public key, with two lines during a rotation.
- The chroot tree kept current: it holds **copies** of the ssh client and
  its libraries, which go stale when the packages are updated. Either
  rebuild it from an apt hook and check it in verify, or bind-mount `/usr`
  read-only and nosuid into it (always current, but then shells exist
  inside, which Landlock still confines).
- The nftables chain inside `roles/firewall`'s own table.
- `PerSourceMaxStartups` in `roles/sshd`, and the lockout guard extended to
  assert that an administrator's effective settings are still key-only.
- A relay that requires Landlock ABI 6 fails closed on an older kernel:
  the gateway must run trixie's kernel or later.
- Optionally a seccomp filter in the relay, and `LANG`/`LC_*` passed on.
- The VM test: a board login end to end, a non-board name refused without
  a prompt, an administrator's key login, the probe.

## What could not be verified

- **`systemd-run --user` for the shared uid.** The lab container runs no
  systemd. What is known without a test: it needs a running per-user
  service manager, which exists only for a uid with a logind session;
  the board names' PAM stack has no `pam_systemd`, so there is none; inside
  sshd's `ChrootDirectory` the manager's socket is not reachable in any
  case; and a transient unit runs as a child of the manager, not of the
  ssh session. With Debian's stock PAM stack, systemd 257 would probably
  start a manager for uid 950 at each login (its `pam_systemd` has no
  light session class for system users); that was not observed.
- **bubblewrap on the real gateway.** Shown working unprivileged in a
  container with Docker's seccomp and AppArmor profiles lifted, and on the
  trixie machine that ran the lab; not on the gateway itself.
- **arm64 or any kernel other than the lab machine's** (6.12, amd64).
- **A real per-port VLAN.** The lab's boards share one bridge; "empty port"
  timing comes from ARP failing there, which should match a VLAN with no
  host, but was not measured on one.
- **Clients other than OpenSSH 10.0** (PuTTY, dropbear, older OpenSSH).
  Nothing in the design is client-specific: the visitor's client sees an
  ordinary sshd with password authentication.
- **A hostile board attacking the relay's ssh client at the protocol
  level.** Tested: a wrong host key, a changed password, escape sequences
  and bulk output. Not tested: a malicious ssh server implementation. The
  sandbox is what bounds that case.
- **Logs through journald.** The lab's sshd logs to stderr; PAM's own lines
  (`pam_unix(sshd-fpgas-board:session)`) go to syslog and were not captured.

## Running it

```
uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py
uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py --only visitor,sandbox
uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py up --lookup nss   # leave it running
uv run tests/lab/sshd_native_routing/sshd_native_routing_lab.py down
```

It needs Docker and builds four images from `amd64/debian:trixie` and
`amd64/debian:bookworm`. A full run takes about a quarter of an hour, most
of it the abuse measurements. It removes its containers, networks and
images afterwards (and the two base images if it pulled them); `--keep`
leaves them. Results go to `tmp/sshd_native_routing/results.md` unless
`--out` is given. Not collected by pytest and not run in CI.

The lab's board network is `10.121.0.0/16`, not a site's `10.21.0.0/16`,
because the machine running it may have a route to a real site.

| File | What |
|---|---|
| `sshd_native_routing_lab.py` | the tests and the report |
| `lab.py` | images, networks, containers |
| `files/gateway-entrypoint.sh` | every piece of gateway configuration, per lookup and variant |
| `src/board_relay.c`, `src/sandbox.h`, `src/board_name.h` | the relay |
| `src/nss_fpgasboard.c` | the custom NSS module |
| `src/probe.c` | lab only: what a compromised relay could reach |
| `files/ptyrun.py`, `files/askpass`, `files/holdopen.py`, `files/flood.sh` | the visitor's and the abuser's tools |
