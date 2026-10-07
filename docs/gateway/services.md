# What runs on a gateway, service by service

**You operate a site's gateway and want to know which service does what, which Ansible role installs it and which setting matters, so that you can find the cause of a fault or the place for a change.** The list of roles in the order `site.yml` applies them is on [The gateway host](../gateway.md#what-runs-on-the-gateway); this page has the detail. From fpgas.online-infra main, read 2026-10-07, unless a line says "(from the earlier docs page, not re-checked)".

## Boot and storage

- **dnsmasq** (`pxe`, `dnsmasq.service`) provides DHCP, TFTP and DNS on the Pi network. It runs `no-resolv` with an explicit upstream, so it is the network's resolver (from the earlier docs page, not re-checked). It binds with `bind-dynamic` rather than `bind-interfaces`, so it survives the per-port VLAN interfaces that mostly have no carrier (welland's gateway had 88 on 6 October 2026, [Netboot and the NFS root](../netboot.md#the-boot-chain)), and its lease database is pinned to an absolute path. The `pxe` role writes its files in `/etc/dnsmasq.d/`:
  `rpi.conf` (the Raspberry Pi boot options) on every gateway; `ports.conf` on a per-port gateway (one with a
  `switches:` list); `pibs.conf` and `switch.conf` on a legacy MAC-table one. On a per-port gateway it removes
  `pibs.conf`, `switch.conf` and `local.conf`, the hand-written config of the flat scheme before the VLANs:
  `local.conf` carries `bind-interfaces`, and dnsmasq refuses to start with both that and `bind-dynamic`. If
  dnsmasq will not start, look in the directory for a file the role did not write
  (`roles/pxe/tasks/main.yml`, read 2026-10-07).
- **nfs-kernel-server and rpcbind** (`nfs`) export the read-only NFS roots, with `host=` in `/etc/default/nfs-kernel-server` set to the local NIC address (`eth_local_address`, `roles/nfs/tasks/main.yml`) so the export is not offered on the uplink.
- **chrony** (`pxe`, `chrony.service`) serves NTP to the Pi LAN: `/etc/chrony/conf.d/fpgas-lan.conf` allows `<pib_network>.0.0/16` (welland: `10.21.0.0/16`). dnsmasq advertises the gateway as the time source with `dhcp-option ntp-server`. The Pis have no route to the internet, so without a LAN time source their clocks never leave the `fake-hwclock` date (rebuild record, entry C2-2).
- **The image pipeline.** `img` pulls the CI-built root image into the NFS root, and `fixpi` writes the site layer into it: [What is in the NFS root](../netboot/root.md).

## Network and logins

- **netif** (`systemd-networkd.service`) gives the two NICs their `eth-local` and `eth-uplink` names via MAC-matched systemd `.link` files, moves the uplink from `ifupdown` to systemd-networkd, and reboots once if a NIC still carries its installer-era name.
- **nftables** (`firewall`, `nftables.service`) carries the whole isolation policy; the role also installs `nmap` for probing it (`roles/firewall/tasks/main.yml`). See [Checking isolation](../network/isolation.md).
- **lldpd** (`lldp`, `lldpd.service`) advertises the gateway on every attached link and records what the switches advertise back, so the cabling can be confirmed rather than assumed (from the earlier docs page, not re-checked).
- **The per-port VLAN interfaces** (`vlan_ports`) are systemd-networkd `.netdev` and `.network` files, one pair per switch port, on the `eth-local` trunk; stale `40-v*` files for ports no longer in the plan are removed. The role runs only on hosts that define `switches:` (from the earlier docs page, not re-checked, apart from the last point).
- **switch_vlans** installs the `fpgas-switch-setup` CLI into its own venv, renders `/etc/fpgas/switches.yml` and converges each switch: [Converging the switches](../network/switches.md).
- **The login accounts**, all described in [Accounts and logins](../access.md): `automation_user` keeps the `ansible` account trusting only the automation key (tweed only), `operators` creates the human operator accounts with passwordless sudo, keyed from their GitHub accounts (`https://github.com/<user>.keys`, through the `ssh_key_fetch` role, which stops the converge when a download yields no keys), `jump` builds the restricted `pi` jump account, and `sshd` makes login public-key only (tweed only). The web tier's `server_user` role manages the account the site runs as (`admin` on tweed). The `ssh_key_fetch` and tweed-only statements are from the earlier docs page, not re-checked.

## Web tier

- **nginx** (`site`, from `nginx-extras`, `nginx.service`) is installed first in the play, because the later web roles write into `/etc/nginx`. The role owns the port-80 catch-all vhost that serves ACME challenges and redirects everything else to HTTPS, plus the location includes that route each Django app.
- **gunicorn** with the uvicorn worker class behind `/run/gunicorn.sock` (`gunicorn.socket`, `gunicorn.service`), **daphne** for the status WebSocket (`daphne.socket`, `daphne.service`), and **uvicorn** (`uvicorn.service`), all pip-installed into the Django venv and run as systemd units (`site`).
- **redis** (`site`, `redis-server.service`; the role installs the Debian `redis` package without naming a unit, and its own verify tasks assert `redis-server.service`) backs the `channels_redis` layer the live Pi status page uses.
- **certbot** (`site`; the role writes no unit of its own, renewal is whatever the installed certbot ships, and on tweed that is the snap build) obtains the TLS certificate: [Certificates and the web at welland](certificates.md).
- **webssh** (`wssh`, `wssh.socket`, `wssh.service`) runs in its own venv behind a systemd socket, with an nginx include that publishes the browser terminal.
- **nginx-rtmp and fancyindex** (`cam/stream-server`, modules inside `nginx.service`) take the RTMP feeds the Pis push and republish them as HLS from a tmpfs, with the front-end nginx include beside it.
- **The Tiny Tapeout site** (`ttsite`, only where `tt_boards` is defined) loads the board catalogue into Django, pins the Commander embed bundle by version and SHA-256, and renders the `tinytapeout.fpgas.online` vhost with one WebSocket proxy per live board.

The Django application itself is [The web application](https://docs.fpgas.online/en/latest/setup/webapp.html); the Tiny Tapeout catalogue and daemon are [The Tiny Tapeout stack](https://docs.fpgas.online/en/latest/setup/tinytapeout.html). The web-tier bullets are from the earlier docs page, not re-checked beyond the role names in `web.yml`.

## USB hub power

**uhubctl** plus a udev rule for the D-Link DUB-H7 (USB ID `2001:f103`), which sets the hub's device node to mode 0666 and chmods its per-port `disable` attributes so an unprivileged user can cut power to one port (`roles/uhubctl/templates/udev/52-uhubctl.rules.j2`). This is the USB-side counterpart to [PoE power control](../network/power-cycle.md). On the current inventory the `uhubctl` group is empty, so it applies to no host.
