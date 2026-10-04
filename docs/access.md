# Accounts and logins (Welland)

Who can log in to the Welland gateway (tweed, inventory host `fpgas.online`)
and to the netbooted Pi fleet, with what, and which role and variable decide
it. Everything here is what `main` configures. It was deployed to tweed
(`main` 4de0b24) and checked live on 2026-09-29:

- a password-only login to tweed gets `Permission denied (publickey)`;
- `ansible` (automation key), and `admin` and `tim` (GitHub keys), log in and
  get `sudo -n` to root, and `carl` holds exactly his GitHub key;
- the jump account refuses commands, and its `authorized_keys` holds only Tim's
  and Carl's GitHub keys (the Launchpad lines are gone);
- through `-J pi@tweed`, `pi@` and `root@` a board work with Tim's key, and
  `ansible@` a board works with the automation key, including `sudo`;
- the jump account's own key logs in to `pi@` a board.

No secrets are recorded here. The only private material involved is the
`fpgas.online-ansible` automation key (vaulted as
`vault_ansible_ssh_private_key` in
[`host_vars/fpgas.online.yml`](../ansible/inventory/host_vars/fpgas.online.yml))
and the vaulted `pi_pw`. The `pi` password is public by design: the board
pages on fpgas.online publish it, and the boards are ephemeral and isolated
per port.

## At a glance

| Where | Login methods | Who |
|---|---|---|
| tweed | SSH public key only ([`roles/sshd`](../ansible/roles/sshd)) | `ansible` (automation), `admin` (site services), `tim` and `carl` (operators), `pi` (restricted jump), `root` (key only) |
| Pi NFS root, every board `pi-sw<S>-p<P>` = `10.21.<S>.<P>` | public key, plus a password for `pi` | `pi` (the web terminal and people), `root`, `ansible` (automation) |

The Pis are not routable from outside tweed, so every login to a board goes
through tweed.

## tweed

tweed is not publicly addressable over IPv4. `tweed.welland.mithis.com` is
split-horizon DNS (looked up 2026-09-29):

| Resolver | A | AAAA |
|---|---|---|
| public (ns1/ns2.rollernet.us) | `87.121.95.37`, which is **ten64** (PTR `ten64.welland.mithis.com`) | `2404:e80:a137:2100::1`, `2404:e80:a137:9921::2` (tweed) |
| inside the site | `10.99.21.2` (uplink), `10.21.0.1` (eth-local) | the same two |

Which path reaches tweed's sshd (checked 2026-09-29, after infra `main`
4de0b24 was deployed):

| From | Use | Why |
|---|---|---|
| inside the site, or over the wg route | `tweed.welland.mithis.com` | resolves to `10.21.0.1` / `10.99.21.2`, which reach tweed directly |
| the upstream router | `10.99.21.2` | the transit link |
| Ansible, from anywhere with IPv6 | `gw.welland.fpgas.online`, IPv6 only | the inventory's `ansible_host`; the name's AAAA record is tweed, its A record is not |
| outside, IPv6 | `2404:e80:a137:2100::1` | reaches tweed. `2404:e80:a137:9921::2` port 22 times out from outside, so use the address rather than the name, which also lists `9921::2` |
| outside, IPv4 only | `-J <you>@ten64.welland.mithis.com`, then `10.99.21.2`, if you have an account on ten64 | the public A record is ten64, not tweed |

tweed's own firewall accepts SSH on every interface
([`roles/firewall`](../ansible/roles/firewall/templates/nftables.conf.j2)).

| Account | uid | sudo | `authorized_keys` | Managed by |
|---|---|---|---|---|
| `ansible` | 1000 | NOPASSWD (`/etc/sudoers.d/ansible`) | exactly the `fpgas.online-ansible` key, `SHA256:D/6/i3vPET3EeQKtO0kv0y0YfukEVg7/ZIF7oW3U6yA` (ED25519). Written `exclusive`, so a key added by hand is removed at the next converge | [`roles/automation_user`](../ansible/roles/automation_user), only where `automation_user_manage: true` (tweed and the CI VM). The Debian preseed creates the account on a fresh install |
| `admin` | 1001 | NOPASSWD (`server_user_sudo: true`) | exactly the keys published at `https://github.com/mithro.keys` and `https://github.com/CarlFK.keys`. Written `exclusive`, after every id has downloaded (see [Where the keys come from](#where-the-keys-come-from)) | [`roles/server_user`](../ansible/roles/server_user) (`user_name: admin`, `server_user_ssh_import_ids` = the operators' ids). The account was `videoteam` until #141 renamed it in place (`server_user_rename_from`) |
| `tim`, `carl` | | NOPASSWD (`/etc/sudoers.d/<name>`) | the GitHub keys of `operators_accounts[].ssh_import_ids` (`gh:mithro`, `gh:CarlFK`), added (not exclusive), minus `ssh_public_keys_revoked` | [`roles/operators`](../ansible/roles/operators) |
| `pi` | | none (the role deletes any `/etc/sudoers.d/pi`) | `ssh_public_keys` (static) plus the GitHub keys of `ssh_imports` (`gh:CarlFK`, `gh:mithro`), added (not exclusive), minus `ssh_imports_revoked` and `ssh_public_keys_revoked` | [`roles/jump`](../ansible/roles/jump) |
| `root` | 0 | | not managed by any role. Password locked (observed 2026-09-27) | sshd: `PermitRootLogin prohibit-password` |

`admin` runs the site: gunicorn, daphne, uvicorn and the fleet consumer are
`User={{ user_name }}` ([`roles/site`](../ansible/roles/site/templates)). Its
`~/.ssh/id_rsa` keypair is the **server-user key** that every Pi trusts. It is
generated by `fixpi/tasks/userconf.yml` ("Generate ssh keys for server user",
never overwritten) and read from `fixpi_server_user_pubkey`.

The `pi` jump account:

- Its login shell is `/bin/rbash` with `PATH` locked to `~/bin`, which holds
  only `ssh` and `ssh-keyscan` (`jump_commands`).
- `/etc/ssh/sshd_config.d/50-jump.conf` does `Match User pi` →
  `ForceCommand jump-shell` (`/usr/local/bin/jump-shell`), which refuses
  every command except `ssh` and `ssh-keyscan`. It also sets
  `AllowTcpForwarding yes`, `AllowAgentForwarding no`, `X11Forwarding no`
  and `PermitTunnel no`.
- It has its own keypair, `/home/pi/.ssh/id_ed25519` (`jump_ssh_key`, #140).
  fixpi authorizes the public half for the Pis' `pi` user, so
  `ssh pi@10.21.S.P` from inside the jump shell works without a password.
  `~/.ssh/config` accepts new Pi host keys.

Retired accounts: `piroot` (the old chroot-shell provisioning login) is
deleted by `operators_retired_accounts`, together with its sudoers file and
`/usr/local/bin/chroot-shell` (#149). `videoteam` is now `admin`.

### sshd

`sshd_pubkey_only: true` in tweed's host_vars makes
[`roles/sshd`](../ansible/roles/sshd) write
`/etc/ssh/sshd_config.d/00-pubkey-only.conf` (#139):

```
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
PermitRootLogin prohibit-password
```

It sorts first, so it wins over `50-jump.conf` and the main file. Before it
writes the file, a **lockout guard** checks that the account Ansible is
connected as, every operator and the jump account (`sshd_pubkey_only_key_users`)
each have at least one key that `ssh-keygen -l` can parse. If any does not,
the converge fails. The connecting account is found with `id -un` with become
switched off through the `ansible_become` variable, because tweed's host_vars
set `ansible_become: true`. It is then asserted to equal `ansible_user` (#162;
before that fix the probe ran as root and the guard checked root's keys). After
writing, the role checks `sshd -t` and the effective `sshd -T` values for
`ansible`, `pi` and `root`.

ps1.fpgas.online runs the same `operators`, `jump` and `sshd` roles. It does
not run `automation_user` (`automation_user_manage` is false). Its
`sshd_pubkey_only` is false, so `sshd` only makes sure the drop-in is absent,
and its `user_name` is still `videoteam`.

## The Pi NFS root

Every netbooted board shares one root, so every board has the same accounts,
keys and host key.

| Account | uid:gid | sudo | Password | `authorized_keys` |
|---|---|---|---|---|
| `pi` | 1000:1000 | NOPASSWD (`/etc/sudoers.d/010_pi-nopasswd`) | from `pi_pw` (see below) | server-user key, controller key, operators' GitHub keys, jump account key |
| `root` | 0:0 | | | server-user key, controller key, operators' GitHub keys |
| `ansible` | 1001:1001 | NOPASSWD (`/etc/sudoers.d/010_ansible-nopasswd`) | locked | the controller key only (`key_sources: [controller]`) |

- The CI image build creates `pi` and `ansible` (`fixpi/tasks/netboot.yml`,
  `fixpi_image_build`). `ansible`'s uid and gid are pinned by
  `fixpi_ansible_uid` so every image agrees.
- **The `pi` password.** `pi_pw` (vaulted in host_vars) is the plaintext
  shared password. `fixpi/tasks/userconf.yml` writes its sha512-crypt hash
  into the root's `/etc/shadow`, on the gateway only: never in the CI image
  (`fixpi_image_build`). `roles/site` passes it,
  base64-encoded, to Django as `PI_PW`. The board page's web terminal (wssh)
  and the upload page log in to `pi@10.21.S.P` with it.
  `fixpi/files/etc/ssh/sshd_config.d/password.conf` keeps
  `PasswordAuthentication yes` on the Pis for that reason. This is
  intentional; it is only tweed that is key-only.
- **The keys** (`fixpi_authorized_keys_files`, built by
  [`fixpi/tasks/authorized_keys.yml`](../ansible/roles/fixpi/tasks/authorized_keys.yml)):

  | Source | What | Variable |
  |---|---|---|
  | server | tweed's `/home/admin/.ssh/id_rsa.pub` | `fixpi_server_user_pubkey` |
  | controller | the `fpgas.online-ansible` public key | `ansible_ssh_private_key_file` + `.pub` ([`group_vars/all/controller.yml`](../ansible/inventory/group_vars/all/controller.yml)) |
  | github | `https://github.com/<user>.keys` for the `gh:` ids in `operators_accounts` (mithro, CarlFK), each line tagged `gh:<user>` | `fixpi_github_key_users`, `fixpi_github_keys_base_url` |
  | jump | tweed's `/home/pi/.ssh/id_ed25519.pub`, `pi` only | `fixpi_jump_ssh_pubkey` |

  The root gets exactly the keys GitHub lists at the time of the converge.
  The download runs first in site.yml's "Update the Pi NFS root" play, before
  the update lock and the image extraction, so a failed download stops the
  run before the root is touched.
- **Host key.** Every board presents the same ED25519 host key,
  `SHA256:tL3Mm5hn0pSKtUhZxl9CuJTMh5fFpAYxPdFq5tGlRhI` (public key ending
  `…hN/k2`). It is the site's, not the image's. The img pull's rsync
  excludes `/etc/ssh/ssh_host_*`, so a new image keeps it (#131).

### Who owns `authorized_keys`, and why a key change reboots the fleet

The image ships no `authorized_keys`. The img pull
([`roles/img/tasks/pull.yml`](../ansible/roles/img/tasks/pull.yml)) excludes
`/root/.ssh/authorized_keys` and `/home/*/.ssh/authorized_keys` from its
`rsync --delete`. fixpi builds the complete list and writes each file once
with `copy`, which leaves an identical file alone (#140). So a converge with
no key change touches none of them.

A real key change replaces the file, which gives it a new inode. The booted
boards' overlay over NFS answers that with ESTALE. The change also counts as a
root change for `nfsroot_generation`, which bumps the generation, and each
board's nfsroot-watchdog then reboots it in its stagger slot (up to about 33
minutes for a two-switch site, `onpi_nfsroot_watchdog_*`). **Adding or removing
a Pi key therefore reboots the whole fleet.** Changing `pi_pw` does too: the
rewritten `/etc/shadow` makes every booted board refuse SSH until it reboots.

## Logging in

| To | Command | Authenticates with |
|---|---|---|
| tweed, as yourself | inside the site, over wg or over IPv6: `ssh <you>@tweed.welland.mithis.com`; from ten64: `ssh <you>@10.99.21.2` | your GitHub key |
| tweed, as the automation account | `ssh -6 -i ~/.ssh/fpgas.online-ansible -o IdentitiesOnly=yes ansible@gw.welland.fpgas.online` | the automation key |
| a board, through the jump account | `ssh -J pi@tweed.welland.mithis.com pi@10.21.2.29` | your key at both hops (see the note below) |
| a board, hopping from the jump shell | `ssh pi@tweed.welland.mithis.com`, then `ssh pi@10.21.2.29` | the jump account's own key at the board |
| a board, as the automation account (from ten64) | `ssh -i ~/.ssh/fpgas.online-ansible -o IdentitiesOnly=yes -J <you>@10.99.21.2 ansible@10.21.S.P` | your own key at tweed, the automation key at the board |
| a board, as root | `ssh -J <you>@tweed.welland.mithis.com root@10.21.S.P` | your GitHub key at both hops |
| a board, with the password | the board page's terminal on welland.fpgas.online, or `ssh pi@10.21.S.P` from tweed | `pi_pw` |

Every `tweed.welland.mithis.com` in this table assumes you are inside the site
or on the wg route. From ten64 use `10.99.21.2`, and from outside use
`2404:e80:a137:2100::1` (see [tweed](#tweed)).

With `-J`, your own key must be trusted at both ends. The jump account trusts
`ssh_public_keys` and `ssh_imports`. The boards trust only the GitHub keys of
`operators_accounts`. Someone in `ssh_imports` who is not an operator can
still reach a board: they hop from the jump shell, or they use the password.

To get the automation key on a controller, extract `vault_ansible_ssh_private_key`
to `~/.ssh/fpgas.online-ansible` (mode 0600), and put the public half beside it
as `~/.ssh/fpgas.online-ansible.pub`. `automation_user` derives the public key
from the private one and refuses to converge if they differ.
[`ansible/ssh.cfg`](../ansible/ssh.cfg) makes Ansible offer only that key
(`IdentityFile`, and `IdentityAgent none` so no agent keys are offered) and
gives it its own known_hosts file (`~/.config/fpgas-online/ansible_known_hosts`).

### A board from outside the site: `ssh -p <port> pi@<site>`

This is the command the board pages print. It needs no account on the
gateway: the port number selects the board, and the login is the board's
own (`pi` and the published password, or a key the board trusts).

```
ssh -p 24622 pi@welland.fpgas.online      # the board on switch 2, port 46
```

- **The port is `<switch><pp>22`**: the switch's index, the access port as
  two digits, then `22`. As a number: `10000 × switch + 100 × port + 22`.
  There is one for every access port of every switch in the site's
  `switches:` (`access_ports`), whether or not a board is plugged in.
- **On the gateway**
  ([`roles/firewall`](../ansible/roles/firewall/templates/nftables.conf.j2))
  each such port, arriving on the uplink, is forwarded to port 22 of that
  access port's board: over IPv4 when addressed to the gateway's transit
  address (`eth_uplink_static_address`), to `10.21.<S>.<P>`; over IPv6 when
  addressed to any of the gateway's own addresses, to the board's routed
  address `<pib_network6_base><SS>::<P>`. The board sees the client's own
  address on both families. A connection that arrives from a board is not
  forwarded, so one board cannot reach another this way.
- **What the site's upstream gateway must do**, at a site whose gateway is
  behind one:
  - IPv4: forward the public TCP ports `10000 × switch + 100 × port + 22`,
    for every access port, to the site gateway's transit address at the
    same port number, keeping the client's source address.
  - IPv6: let the same TCP ports reach the address in the site name's AAAA
    record (the site gateway itself). Nothing is translated. If the
    upstream gateway drops them, an IPv6 client still hangs before it
    falls back to IPv4.
  - For Welland's `switches:` today (40 access ports on switch 1, 48 on
    switch 2) that is 88 ports: `10122, 10222, … 14022` and
    `20122, 20222, … 24822`. A forward of the two ranges 10122-14022 and
    20122-24822 covers them; the gateway forwards only the ports ending
    in `22` (and, over IPv4, the aux ports ending in `44`) and drops the
    rest. The list follows the inventory: generate it from `switches:`
    rather than copying it from here.
- A port with no board answers after about three seconds with "No route to
  host".
- `verify-server.yml` checks the loaded rules, port by port, on both
  families. `verify-pi.yml` checks that a board holds its IPv6 address and
  that the gateway reaches the board's sshd on it.

## Adding or removing a person

All the lists are in
[`inventory/group_vars/all/ssh_keys.yml`](../ansible/inventory/group_vars/all/ssh_keys.yml).

| To | Edit | Effect after the converge |
|---|---|---|
| give someone an operator account (tweed login with sudo, `admin`, and the boards' `pi` and `root`) | add `{name: <unix name>, ssh_import_ids: [gh:<github user>]}` to `operators_accounts` | tweed account and sudoers file (`operators`); their keys on `admin` (`server_user`); their keys in the Pi root (`fixpi`), **which reboots the fleet** |
| let someone use the jump account only | add `gh:<user>` to `ssh_imports`, or their key to `ssh_public_keys` | keys on tweed's `pi` |
| remove an operator | take them out of `operators_accounts` | their keys leave `admin` and the Pi root (**fleet reboot**). Their tweed account stays: also add the name to `operators_retired_accounts` in [`roles/operators/defaults`](../ansible/roles/operators/defaults/main.yml) to delete it, its home and its sudoers file |
| remove someone from the jump account | take the id out of `ssh_imports` **and** add it to `ssh_imports_revoked`. For a static key, move it from `ssh_public_keys` to `ssh_public_keys_revoked` | the keys are deleted from `pi`. Revoked static keys are also deleted from every operator account |
| revoke one key that has left someone's GitHub account | add it to `ssh_public_keys_revoked` | deleted from the jump and operator accounts. `admin` and the Pis already follow GitHub |

`operators` and `jump` only ever add keys, which is why the revoked lists
exist. Keep an entry in a revoked list until every host has converged without
the key. Imported lines carry a `# ssh-import-id <id>` comment (the format the
old `ssh-import-id` runs used), and that comment is how `ssh_imports_revoked`
finds an id's lines to delete. `fixpi` uses only `gh:` ids, so give operators
`gh:` ids.

### Where the keys come from

[`roles/ssh_key_fetch`](../ansible/roles/ssh_key_fetch) is the one place the
server downloads keys. `operators`, `jump`, `server_user` (and its verify) and
`fixpi` all use it (#161). It reads `https://github.com/<user>.keys` for a
`gh:<user>` id and never the rate-limited GitHub API. It reads Launchpad only
for `lp:` or bare ids, and none are fetched now. `ssh-import-id` is no longer
used.

Each download is retried `ssh_key_fetch_retries` (3) times,
`ssh_key_fetch_delay` (5) seconds apart, until it gets a 200 holding at least
one key line. After that **the play fails at the download**, naming the
account, the id, the URL and the answer. There is no fallback. So if GitHub is
down or returns nothing:

- the converge stops at the first role that needs keys, and no
  `authorized_keys` is written empty or from stale data;
- `server_user` fails before its exclusive write, so `admin` keeps its current
  keys;
- for the Pi root it stops before the update lock is taken and before the image
  is extracted, so the fleet and its root are left as they were.

Re-run once GitHub answers again.

Converge from ten64. tweed's host_vars hold vaulted values, so give Ansible
the vault password as the README's Deploy section does
(`ANSIBLE_VAULT_PASSWORD_FILE`, or `--vault-password-file`). Always the
whole playbook, never a `--tags` subset (issue #157):

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.config/fpgas-online/vault-pass
uv run ansible-playbook ansible/site.yml --limit fpgas.online
```

The automation key itself is never in these lists. Rotating it means changing
the vaulted private key, `~/.ssh/fpgas.online-ansible{,.pub}` on every
controller, and the preseed's copy, and then converging. `automation_user`
writes the new key exclusively on tweed and fixpi writes it into the Pi root
(fleet reboot).

## Verifying

Both playbooks run in full; the table lists the checks in them that cover
access.

| Playbook, role | Checks |
|---|---|
| [`verify-server.yml`](../ansible/verify-server.yml): `automation_user` | `ansible` exists, its sudoers file is valid, its `authorized_keys` is exactly the automation key, and a fresh login as it gets `sudo -n` to root |
| `operators` | each operator exists, has a valid sudoers file and a non-empty `authorized_keys` without revoked keys; retired accounts, their groups and their files are gone |
| `jump` | the rbash shell, `~/bin` exactly `ssh` and `ssh-keyscan`, the keypair, no sudoers file, no revoked keys, `sshd -T` shows the ForceCommand, and the wrapper refuses other commands |
| `sshd` | the drop-in exists, and `sshd -T` for `ansible`, `pi` and `root` gives key-only login and `PermitRootLogin prohibit-password` |
| `server_user` | `admin` exists with home `/home/admin`, `videoteam` is gone, no unit runs as it, `authorized_keys` equals the published GitHub keys exactly, and `sudo -n` works |
| `fixpi` | among its other NFS root checks, the root's `ansible` account: uid and gid 1001, locked password, valid sudoers file, and exactly the automation key, owned by 1001 and mode 0600 |
| [`verify-pi.yml`](../ansible/verify-pi.yml) | on a booted board: `pi`'s password is usable (`passwd -S` = `P`), and `ansible` logs in with the automation key, gets `sudo -n` to root and has a locked password |
| `verify-pi.yml` | tweed's jump account reaches the board as `pi` with its own key (`BatchMode`) |

```bash
uv run ansible-playbook ansible/verify-server.yml --limit fpgas.online
```

The VM test ([`vm-test.yml`](../.github/workflows/vm-test.yml)) runs both
playbooks on every pull request against a test server that is configured like
tweed (`sshd_pubkey_only` and `automation_user_manage` both on, `user_name:
admin`).
