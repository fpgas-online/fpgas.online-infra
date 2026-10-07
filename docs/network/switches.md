# Converging the switches

**You want welland's two switches to carry the VLANs the inventory says (after adding a switch, changing a
port range, or replacing a switch), and to know the converge did what it should.** ps1 has no per-port
VLANs and nothing on this page applies to it: its switch, a Netgear FS728TPv2, is a Plus-series unit
that the per-port design put out of scope ([`docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md`](../superpowers/specs/2026-08-14-vlan-per-port-network-design.md),
lines 6 and 25).

## What the converge does

`fpgas-switch-setup`, the command-line tool in [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe),
reads the `switches:` list from `/etc/fpgas/switches.yml` on the gateway, reads the switch's state over SNMP,
and writes only the difference: it creates the VLANs, sets the trunk ports' memberships, sets each access
port's VLAN, and takes the access ports out of VLAN 1. Two rules bound what it can break (fpgas.online-poe
`README.md`, main fcb4a2a):

- It only ever writes VLANs **2101 to 2348** and the membership of the ports it owns, so VLAN 5 (switch
  management, on the house network) and the house uplink cannot be touched, even by a run that dies half-way.
- It writes the trunks before the access ports, so a Pi's port is never put in a VLAN that cannot yet reach
  the gateway.

| Switch | Model | Management address | Access ports | Gateway side |
|---|---|---|---|---|
| 1 | Netgear GSM7252PS | 10.1.5.23 | 1 to 40 | port 47 to tweed's `eth-local`; port 50 to switch 2 |
| 2 | Netgear S3300-52X-PoE+ | 10.1.5.11 | 1 to 48 | port 51 (`1/xg51`) to switch 1 |

(`ansible/inventory/host_vars/fpgas.online.yml` in fpgas.online-infra, main, read 2026-10-07; cabling
confirmed from the switches' own LLDP on 2026-08-22.)

Both are 52-port units. Both are managed over the house network's VLAN 5, a separate path from the fpgas trunk:
nothing management-related rides the trunk, and the provisioning tool never touches the house-facing port. Which
board is on which port is on the [Welland page](https://docs.fpgas.online/en/latest/sites/welland.html).

The trunk ports differ from the design spec of 2026-08-14, which reserves ports 49-52 for trunks and sketches
`gateway_trunk_port: 49`. The real cabling, confirmed from the switches' own LLDP on 2026-08-22, is tweed's `eth-local`
into GSM 1/0/47 and GSM 1/0/50 into S3300 1/xg51. The spec's `access_ports: 48` on both switches is also not what
shipped: switch 1 carries 40 access ports, switch 2 carries 48 (`host_vars/fpgas.online.yml`, main, read 2026-10-07).

## The normal way: the whole playbook

The `switch_vlans` role of fpgas.online-infra installs the tool into `/opt/fpgas-switch/venv`, renders
`/etc/fpgas/switches.yml` and runs the converge once per switch, with each switch's write community from the
vault. It runs as part of a deploy of the welland gateway, which is always the whole playbook, scoped only
with `--limit` (fpgas.online-infra issue #157 removed the tags on 2 October 2026):

```console
$ uv run ansible-playbook ansible/site.yml --limit fpgas.online
```

[Deploying to a gateway](https://docs.fpgas.online/en/latest/setup/gateway.html#deploying) has the whole procedure (vault password, pinned root, what
to check after).

## By hand, on the gateway

To see what the converge would do without the playbook, run the tool on the gateway as root. Check mode is
the default and writes nothing; `--apply` writes.

```console
$ # the write community of THIS switch, typed in, never in a file or a commit
$ export FPGAS_SWITCH_COMMUNITY='<switch-write-community>'
$ # check mode: prints the pending actions, writes nothing
$ /opt/fpgas-switch/venv/bin/fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 1
$ /opt/fpgas-switch/venv/bin/fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 1 --apply
$ # check again: expect no output and exit 0
$ /opt/fpgas-switch/venv/bin/fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 1
```

The two switches have different write communities. Exit codes: `0` in step or applied, `2` check mode found
differences, `1` an error.

Two things look like faults and are not (the prototype runbook in fpgas.online-infra,
`docs/superpowers/specs/2026-08-14-vlan-per-port-prototype-runbook.md`): the switches sometimes answer a VLAN
write with `Error in packet` or `commitFailed` although it applied (run again; only what is missing is
written), and each SNMP operation takes about two seconds, so a 48-port converge runs for several minutes.

## Afterwards

Check isolation: [Checking isolation](isolation.md).
