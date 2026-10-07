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

## 4. Check

```console
$ uv run ansible-playbook ansible/verify-server.yml --limit fpgas.online
```

`verify-server.yml` runs every server role's own checks, then the per-port checks where `switches:` is
defined, the web tier's, and the NFS root's contents. It fails while an NFS root update lock is held. Compare
it with step 1: a check that failed before will fail again.

`verify-pi.yml` checks the running Pis, named by address (`-i 10.21.2.33, -e verify_pi_hosts=all`; the
inventory lists none, and a run that selects no Pi, `--limit fpgas.online` for example, fails). It needs a
route to the Pi network, `10.21.0.0/16`, which only the gateway has: `ansible/ssh.cfg` sets no jump, so run it
where that network is routed, or add a `ProxyJump` through the gateway to your own SSH configuration.

**A reinstalled host.** `ansible/ssh.cfg` gives the automation its own `known_hosts` with
`StrictHostKeyChecking accept-new` (a new host is learned, a changed key is refused) and `IdentityAgent none`
(a hung forwarded agent would otherwise stall every connection). After a deliberate reinstall, run
`refresh-known-hosts.yml` before the next `site.yml`: it removes the old key, scans the new one and pins it.

## Testing without hardware

The whole gateway can be tested with no site at all. `tests/vm/` boots a Debian VM, applies the same
`site.yml`, PXE-boots a virtual Raspberry Pi from it with the patched QEMU of
[fpgas-online/rpi-qemu](https://github.com/fpgas-online/rpi-qemu), and runs both verify playbooks. CI runs it
on every pull request and every push to `main` (`.github/workflows/vm-test.yml`).
