# Rebuilding a gateway from bare metal

**You are reinstalling a site's gateway from bare metal and want it to come back right the first time.** The
lessons are from welland's three rebuilds of tweed on 2026-08-25 and 2026-08-26, recorded entry by entry in
`docs/rebuilds/2026-08-25-tweed-rebuild.md` in fpgas.online-infra (the entry is named after each item).

The operating-system install itself is served by the site's upstream network over PXE (the record's setup
lines), and is run by whoever manages that network: ask them before a rebuild. Its menus and preseed are not in
fpgas.online-infra. What follows is what
fpgas.online's side needs, in the order you meet it.

## 1. Firmware settings (in the BIOS)

- **Enable the PXE option ROM on the uplink NIC, and disable it on the others** (A1-1). The first attempt never
  reached the installer: the only enabled option ROM was on the local NIC, which has no DHCP server while the
  gateway is down. No test catches this; it is a setting on the machine.

## 2. The installer's command line

- **The serial console last, and only UARTs that exist** (A1-2, A1-3). The console named last is the primary
  one; with `console=tty0` last the installer drew on the VGA screen and a remote operator saw nothing. Naming
  UARTs the machine does not have made two kernel series hang at the same point about nine seconds into boot.
  On tweed the line that worked named one console, `console=ttyS2,115200n8`.
- **Pin the installer's network interface by MAC** (A1-4). Left to choose, it picks the first NIC with a link
  and does not try another; on tweed that was the local NIC. The working line carried
  `interface=0c:c4:7a:16:3b:4b`, tweed's uplink MAC (also `eth_uplink_mac_address` in `host_vars`).

## 3. Before the first converge

- **Run `refresh-known-hosts.yml`.** A reinstalled host has new SSH keys, and the old ones pinned in the
  automation's `known_hosts` make it unreachable ([Deploying to a gateway](deploy.md#4-check)).
- **The vault password file that decrypts the inventory** (B1-2). A converge was lost to three candidate
  password files; on 2026-08-25 the one that decrypted was not the README's documented path. CI has no vaulted
  variables, so only a production run finds this. The record suggests a decrypt test first; this one (run on 2026-10-07, it printed a length) prints
  only the length of one vaulted value:

  ```console
  $ uv run ansible localhost -i ansible/inventory -m ansible.builtin.debug \
      -a 'msg={{ hostvars["fpgas.online"].vault_switch1_snmp_rw_community | length }}'
  ```

## 4. The converge

- Run the whole playbook as on [Deploying to a gateway](deploy.md). A cold converge of tweed took
  about three hours on 2026-08-26.
- **The kernel and the initramfs served to the Pis must be a matching pair** (C1-3). A new initramfs beside an
  old `kernel8.img` gave Pi boots that sometimes worked and sometimes panicked. The `fixpi` role now copies the
  whole firmware payload ([Updating the NFS root](../netboot/update-root.md#how-the-root-is-built)).
- **The NFS exports must be loaded** (C1-4). After a failed first run, `exportfs -ra` (a handler) never ran,
  the kernel's export table stayed empty, and every Pi's root mount failed; the `nfs` role now flushes its
  handlers (fpgas.online-infra commit 7c353ec).

## 5. Afterwards

Run `verify-server.yml`, and `verify-pi.yml` on the Pis that come up ([Deploying to a
gateway](deploy.md#4-check)). The third rebuild, on 2026-08-26, passed both with no step done by hand.

Rebuild #3, on the evening of 2026-08-26 and run from merged `main`, was a full pass with **zero manual
interventions**: 7m41s from power cycle to login, a cold converge of 2h57m with no failures on either the server or
the NFS root play, `verify-server.yml` at `ok=104 failed=0`, `verify-pi.yml` at `ok=21 failed=0` including the camera
and FPGA assertions (rebuild record, 2026-08-26). That is the bar a rebuild is expected to clear.
