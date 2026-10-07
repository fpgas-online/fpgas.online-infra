# The gateway host

**You want to know what a site's gateway runs, which Ansible role puts each part there, and which page to use
to deploy, rebuild or certify one.** Everything here is fpgas.online-infra, main, read 2026-10-07
(`ansible/site.yml`, `ansible/web.yml`, `ansible/inventory/hosts`, the roles' `tasks/`).

Each site has one x86 gateway, and it is everything at the site that is not a Pi: it serves the boot chain,
exports the NFS root, is the firewall for the Pi network, and runs the web tier. welland's is tweed ([The
welland gateway](https://docs.fpgas.online/en/latest/sites/welland-gateway.html)); fpgas.online-infra deploys it. ps1's is Carl Karsten's own
install, which fpgas.online-infra does not deploy as it stands ([The ps1 gateway and
switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html)).

## The tasks

<a id="deploying"></a>
- [Deploying to a gateway](gateway/deploy.md): the whole playbook, and checking it after.

<a id="checking-and-reconnecting"></a>
- [Checking and reconnecting](gateway/deploy.md#4-check): `verify-server.yml`, `verify-pi.yml`, a
  reinstalled host's new key.

<a id="the-tags-do-not-match-the-role-names"></a>
- Tags: there are none any more. fpgas.online-infra issue #157 removed them on 2026-10-02; a recipe with
  `--tags` from before then runs nothing it names ([Deploying to a gateway](gateway/deploy.md)).

<a id="rebuilding-from-scratch"></a>
- [Rebuilding a gateway from bare metal](gateway/rebuild.md).

<a id="web-topology-at-welland"></a>
- [Certificates and the web at welland](gateway/certificates.md).

<a id="testing-without-hardware"></a>
- [Testing without hardware](gateway/deploy.md#testing-without-hardware): the VM test.


## Hosts

Ansible names the gateways after their public names: inventory host `fpgas.online` is tweed, and
`ps1.fpgas.online` is the ps1 gateway.

| Group | Members | Play |
|---|---|---|
| `nbp` | `fpgas.online`, `ps1.fpgas.online` | the server plays and the NFS root update, `site.yml` |
| `pig` | `fpgas.online`, `ps1.fpgas.online` | the web tier, `web.yml` (imported by `site.yml`) |
| `uhubctl` | none | USB hub power control; its only member stopped resolving in 2026-08 and was retired on 2026-09-04 |

## What runs on the gateway

In the order `site.yml` applies the roles.

**Network and logins** (`nbp`):

- `apt_client`, then `netif`: the two NICs named `eth-local` and `eth-uplink` by MAC-matched systemd `.link`
  files (one reboot if a NIC still has its installer name).
- `automation_user` (where enabled), `operators`, `jump`, `sshd`: the `ansible` account, the operators'
  accounts keyed from their GitHub keys, the restricted `pi` jump account, and key-only SSH
  ([Accounts and logins](https://docs.fpgas.online/en/latest/setup/access.html)).
- `lldp`: `lldpd`, so the cabling to the switches can be read rather than assumed.
- `firewall`: `nftables.service`, the whole Pi isolation policy, and IPv4 and IPv6 forwarding.
- `vlan_ports` and `switch_vlans`, only where `switches:` is defined (welland): one VLAN interface per switch
  port, and the switches' VLANs ([Converging the switches](network/switches.md)).

**Boot and storage** (`nbp`):

- `nfs`: `nfs-kernel-server`, exporting the root read-only to the Pi network.
- `apt_cache`: the site's package cache, where enabled.
- `pxe`: `dnsmasq` (DHCP, DNS, TFTP, with `bind-dynamic` and the lease file at an absolute path) and `chrony` (its task "Install chrony (LAN NTP server)")
  (NTP for the Pis, which have no route out) ([Netboot and the NFS root](netboot.md)).

**Web tier** (`pig`, `web.yml`):

- `server_user`: the account the site runs as.
- `site`: nginx, the Django site (`fpgas-online-site`) under gunicorn, daphne and uvicorn, redis, certbot,
  and the PoE control's switch settings ([Power-cycling a board](network/power-cycle.md)).
- `wssh`: the browser terminal. `stream_server`: nginx-rtmp, taking the Pis' camera streams and serving them
  as HLS.
- `mqtt` (where `fleet_broker`), `webrtc` (where `webrtc_additional_hosts`), `ttsite` (where `tt_boards`: the
  Tiny Tapeout site, [The Tiny Tapeout stack](https://docs.fpgas.online/en/latest/setup/tinytapeout.html)).

**The NFS root update** (`nbp`, the last play): `ssh_key_fetch` (the operators' GitHub keys for the root),
`nfsroot_generation` (the update lock), `img` (the CI-built root image), `apt_cache`, `fixpi` (the site layer),
then `nfsroot_generation` again (publish the new generation) ([Updating the NFS
root](netboot/update-root.md)).

The Django application is [The web application](https://docs.fpgas.online/en/latest/setup/webapp.html).
