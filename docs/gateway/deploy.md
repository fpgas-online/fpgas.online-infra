# Deploying to a gateway

**You operate the fleet and want to deploy fpgas.online-infra's `main` to welland's gateway, tweed, and check
that it worked.** A deploy that changes the NFS root reboots every Pi ([Updating the NFS
root](../netboot/update-root.md)); visitors use the boards at any time, so say beforehand when you deploy.
Everything here is fpgas.online-infra main, read 2026-10-07 (`README.md`, `ansible.cfg`, `ansible/ssh.cfg`,
the playbooks and roles named).

## What you need

- A checkout of [fpgas.online-infra](https://github.com/fpgas-online/fpgas.online-infra) at `origin/main`, with
  `uv`.
- The automation key at `~/.ssh/fpgas.online-ansible` (mode 0600), with its public half beside it as
  `~/.ssh/fpgas.online-ansible.pub`. It is the one key the gateway's `ansible` account trusts; its private
  half is vaulted in `host_vars/fpgas.online.yml` (`ansible/ssh.cfg`'s comment says how).
- The vault password, in a file you point `ANSIBLE_VAULT_PASSWORD_FILE` at (the README's example path is
  `~/.config/fpgas-online/vault-pass`).
- An operator login on the gateway ([Accounts and logins](../access.md)), for the reads below.
- IPv6: Ansible reaches the gateway as `gw.welland.fpgas.online`. The first run learns the gateway's host key
  into `~/.config/fpgas-online/ansible_known_hosts`; compare its fingerprint with the gateway's own, read
  through your login: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`.

## 1. Before: read what is there

```console
$ cd fpgas.online-infra && git fetch origin && git switch --detach origin/main
$ export ANSIBLE_VAULT_PASSWORD_FILE=~/.config/fpgas-online/vault-pass
$ uv run ansible-galaxy collection install -r requirements.yml
$ # the gateway's checks now, to compare with afterwards
$ uv run ansible-playbook ansible/verify-server.yml --limit fpgas.online
$ # the NFS root image the gateway serves now
$ ssh <you>@gw.welland.fpgas.online cat /srv/nfs/rpi/bookworm/.image-digest
```

Pin that same digest in step 3 to keep the image the Pis run. The Pis still reboot if any file in the root
changes: the end of the run bumps the generation when a file changed (`roles/nfsroot_generation`), and the
`fixpi` role writes the site layer into the root on every run, so a change on `main` to that layer reboots
every Pi. You cannot see this beforehand: under `--check` the task that decides it is skipped. After the run, its last
task, "Show what changed in the Pi NFS root", says whether the generation was bumped and why. If the Pis must
not reboot (someone is using a board), set `-e nfsroot_generation_bump=never` and power-cycle later
(`roles/nfsroot_generation/README.md`): until then the running boards keep stale file handles on the changed
files, so they may fail on them until they are power-cycled. Any other
digest is a root update ([Updating the NFS root](../netboot/update-root.md)).

## 2. If the change touches the firewall: preview first

> [!WARNING]
> **A bad firewall file leaves the gateway with no firewall.** The `firewall` role restarts `nftables.service`
> on every run (`state: restarted` in `roles/firewall/tasks/main.yml`), and Debian's unit stops with
> `nft flush ruleset` (`ExecStop` of `nftables.service` in Debian 13's nftables 1.1.3-1, read 2026-10-07): if the new `/etc/nftables.conf` does not load, the old rules are already gone and the Pi
> network is open. Your SSH session would not tell you, because an empty ruleset blocks nothing.

Preview the run and read the rendered firewall file before the real one:

```console
$ uv run ansible-playbook ansible/site.yml --limit fpgas.online --check --diff \
    -e img_nfsroot_image=ghcr.io/fpgas-online/nfsroot@sha256:<digest>
```

The role's notify handler reloads (`state: reloaded`; Debian's unit has `ExecReload=/usr/sbin/nft -f /etc/nftables.conf`,
nftables 1.1.3-1, read 2026-10-07), and that load is
atomic: a parse error fails and the running ruleset stays up. It is the converge task `Enable nftables service` that
restarts the unit (`roles/firewall/tasks/main.yml`, main, read 2026-10-07). SSH surviving tells you nothing: both
templates accept it unconditionally on the input chain, and an empty ruleset accepts everything.

> [!NOTE]
> Open, in fpgas.online-infra: nothing yet stops a converge with a bad ruleset from flushing the old rules and
> loading nothing (`state: started`, a validation step before the restart, or both). The cause is the `firewall`
> role's `state: restarted` on `Enable nftables service` (still present on main, read 2026-10-07).

## 3. Deploy: the whole playbook

A deploy runs the whole playbook, scoped only with `--limit` (which host) and `-e` (a pinned value); never
`--tags` or `--skip-tags`. fpgas.online-infra issue #157 removed the tags on 2026-10-02, after a deploy that
used them left tweed out of step with `main`; an older recipe with `--tags` now runs nothing it names.

```console
$ uv run ansible-playbook ansible/site.yml --limit fpgas.online \
    -e img_nfsroot_image=ghcr.io/fpgas-online/nfsroot@sha256:<digest>
```

`ansible.cfg` supplies the inventory (`ansible/inventory`) and `become`. Without `-e img_nfsroot_image=…`
the run pulls the rolling tag, which may have moved, and so may update the root.

The web tier alone, without the server roles or the root:

```console
$ uv run ansible-playbook ansible/web.yml --limit fpgas.online
```

`web.yml` reinstalls the `fpgas-online-site` package every run (`state: forcereinstall`) and then restarts
gunicorn, daphne and uvicorn, so the new code is served. The host's `local_settings.py`, which holds the
production settings, is created once and never overwritten.

A new wheel means new code and templates that the running `gunicorn`, `daphne` and `uvicorn` keep serving the old
versions of until they are restarted, so the install task notifies a `restart django services` handler that
restarts all three. The way back is to pip an older `fpgas-online-site` reference into the Django venv by hand and
restart the three; `local_settings.py` survives both directions (from the earlier docs page, not re-checked; its
`--tags django` route is gone with the tags).

## 4. Check

```console
$ uv run ansible-playbook ansible/verify-server.yml --limit fpgas.online
```

`verify-server.yml` runs every server role's own checks, then the per-port checks where `switches:` is
defined, the web tier's, and the NFS root's contents. It fails while an NFS root update lock is held. Compare
it with step 1: a check that failed before will fail again.

`verify-pi.yml` checks the running Pis, named by address. The site inventory lists none, so give the
addresses as a second inventory, select them, and name the gateway they boot from:

```console
$ uv run ansible-playbook ansible/verify-pi.yml -i ansible/inventory -i 10.21.2.33,10.21.1.17, \
    -e verify_pi_hosts='10.21.*' -e verify_pi_via=fpgas.online
```

- Run it from the repository's root: ansible's ssh settings are `-F ansible/ssh.cfg`, a relative path.
- `-i ansible/inventory` loads the gateways. The fleet registration, the pi password, the Orange Pi rows and
  the jump account's hop are checked against the gateway's own settings.
- `-e verify_pi_via=fpgas.online` names that gateway (ps1's is `ps1.fpgas.online`). It also reaches each Pi
  through it, as the `ansible` account with the automation key: the Pi network, `10.21.0.0/16`, is routed only
  on the gateway. The hop to the gateway uses the gateway's own inventory settings: welland's log in as
  `ansible`; ps1's inventory sets no `ansible_user`, so that hop logs in as your own user name.
- The run fails before connecting:
  - with no gateway in the inventory;
  - with two gateways and none named;
  - when `ansible_ssh_common_args` ends in an option with no value. A `-e 'ansible_ssh_common_args=-o ProxyJump=…'`
    without inner quotes is parsed as key=value pairs and leaves a bare `-o`, which kills ansible's worker
    ("A worker was found in a dead state"); `verify_pi_via` replaces it.
- A run that selects no Pi, `--limit fpgas.online` for example, fails too.

The VM test runs `verify-pi.yml` this way as well, through its virtual gateway (`tests/vm/run_tests.py`).

`verify-server.yml` has three plays, one per group. The `nbp` play runs each server role's own `verify/` tasks
(`apt_client`, `automation_user`, `operators`, `jump`, `sshd`, `lldp`, `firewall`, `nfs`, `img`, `fixpi`, `nspawn_pi`,
`apt_cache`, `pxe`; `verify-server.yml` on main), then asserts the
per-port state on hosts with `switches:` (a `v*` interface in `networkctl list`, `dnsmasq --test` clean, the
`forward` chain at `policy drop`, `ports.conf` present) and finally that the NFS root really contains what the Pis
need. The `uhubctl` play runs that role's verify tasks. The `pig` play verifies the web tier: `site`, then `ttsite`
where `tt_boards` is defined, then `wssh` and `cam/stream-server` (from the earlier docs page, not re-checked).

Three roles have no verify tasks at all: `netif`, `vlan_ports` and `switch_vlans` ship no `tasks/verify/` directory
(checked on main, 2026-10-07). The per-port network is covered only by the inline assertions in the `nbp` play, and
nothing checks the NIC naming or the switch converge directly.

**A reinstalled host.** `ansible/ssh.cfg` gives the automation its own `known_hosts` with
`StrictHostKeyChecking accept-new` (a new host is learned, a changed key is refused) and `IdentityAgent none`
(a hung forwarded agent would otherwise stall every connection). After a deliberate reinstall, run
`refresh-known-hosts.yml` before the next `site.yml`: it removes the old key, scans the new one and pins it.
The file is separate from the host-wide one the site's network tooling generates. The
playbook re-scans with retries while the host finishes booting (plain `ssh-keyscan`, never `-H`, because hashed names
break the `known_hosts` module). It runs with `become: false` on purpose: it manages the control node's own files,
and a root-owned `known_hosts` stops the user's ssh appending anything later (rebuild record, P2-2 and P2-9). It
keyscans from the control node, so hosts on the Pi network, reachable only by jumping through the gateway, cannot be
rekeyed by it (from the earlier docs page, not re-checked).

## Testing without hardware

How CI builds the Pi root itself is on [How the root is built](../netboot/root.md#the-ci-inventory).

The whole gateway can be tested with no site at all. `tests/vm/` boots a Debian VM, applies the same
`site.yml`, PXE-boots a virtual Raspberry Pi from it with the patched QEMU of
[fpgas-online/rpi-qemu](https://github.com/fpgas-online/rpi-qemu), and runs both verify playbooks. CI runs it
on every pull request and every push to `main` (`.github/workflows/vm-test.yml`).

The patched QEMU adds BCM2838 GENET ethernet emulation to the `raspi4b` machine; only the inventory differs from
production. A full run takes roughly two hours under TCG, less when `/dev/kvm` is available, and CI uploads the
serial logs as an artifact whether it passed or failed (from the earlier docs page, not re-checked).

> [!NOTE]
> The 2026-04-04 QEMU testing design and plan
> ([design](https://github.com/fpgas-online/fpgas.online-infra/blob/main/docs/superpowers/specs/2026-04-04-qemu-vm-testing-design.md),
> [plan](https://github.com/fpgas-online/fpgas.online-infra/blob/main/docs/superpowers/plans/2026-04-04-qemu-vm-testing.md))
> describe a different arrangement: a generic `qemu-system-aarch64 -machine virt` guest booting through EDK2 UEFI
> firmware to `grubaa64.efi` and a TFTP `grub.cfg`, with the `pxe` role serving an architecture-tagged `dhcp-boot`
> beside the real Pi path. That is not what runs. The harness emulates a real Pi and drives the real `bootcode.bin`
> chain, so the test exercises the production boot path rather than a parallel one (from the earlier docs page, not re-checked).
