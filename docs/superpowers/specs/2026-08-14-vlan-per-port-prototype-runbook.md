# VLAN-per-port network: hardware prototype runbook

Site: welland (tweed.welland.mithis.com + s3300 + s2/GSM7252PS).
Spec: `docs/superpowers/specs/2026-08-14-vlan-per-port-network-design.md`
(read the "Rollout" and "Risks to verify FIRST" sections before starting).
Lab evidence: `tests/lab/RESULTS.md` (dnsmasq mechanism + switch capacity,
both already verified against real hardware).

## Before you start

- **This runbook is executed by the operator (Tim) on hardware day.** It is
  not run by CI or automation — every command below needs physical access
  to cable switches/Pis and SSH access to tweed. Nothing in here should be
  run unattended.
- **The software is merged-ready.** The full software stack (switch
  provisioning CLI, gateway networkd/dnsmasq/firewall roles) is proven by
  the CI gate `.github/workflows/vm-test.yml` (`tests/vm/run_tests.py`),
  which converges the identical `site.yml` / `verify-server.yml` /
  `verify-pi.yml` against a QEMU VM with an emulated switch. This runbook
  is the hardware-parity check, not first-time debugging.
- **Vault communities must be added FIRST**, before Stage 3. The repo is
  public, so the SNMP write communities cannot live in plaintext. Add them
  now (see Stage 0 below) — every later stage assumes they exist.
- Record results directly in this file as you go (Stage 9). Deviations
  from what's written here feed spec/plan amendments, not silent fixes.

### Stage 0: add the vault SNMP write communities (do this first)

`ansible/inventory/host_vars/fpgas.online.yml` already references
`vault_switch1_snmp_rw_community` (s3300, index 1) and
`vault_switch2_snmp_rw_community` (gsm7252ps, index 2) — see the
`switches:` block and its comment. They don't exist yet. Known values
(read community `public` works on both; write differs per switch):

| Switch | Write community |
|---|---|
| s3300 (switch 1) | `<switch-1-write-community>` |
| s2 / GSM7252PS (switch 2) | `<switch-2-write-community>` |

Generate vault-encrypted strings (matches the existing `!vault \|` inline
style already used in this file for `switch.SNMP_SWITCH_*` and `pi_pw`):

```bash
ansible-vault encrypt_string '<switch-1-write-community>' --name 'vault_switch1_snmp_rw_community'
ansible-vault encrypt_string '<switch-2-write-community>' --name 'vault_switch2_snmp_rw_community'
```

Paste both blocks into `ansible/inventory/host_vars/fpgas.online.yml` (top
level, alongside `switches:`). Do not commit the plaintext community
strings anywhere — only the `ansible-vault encrypt_string` output.

Rollback: n/a (additive; no effect until Stage 3/4 read it).

---

## Stage 1: Preflight

Purpose: prove management-plane reachability and confirm physical cabling
before touching any config.

From tweed:

```bash
ssh root@tweed.welland.mithis.com
ping -c3 10.1.5.11    # s3300 (switch 1) mgmt
ping -c3 10.1.5.23    # s2/GSM7252PS (switch 2) mgmt
snmpget -v2c -c public 10.1.5.11 sysDescr.0
snmpget -v2c -c public 10.1.5.23 sysDescr.0
```

**Expected**: both pings succeed (0% loss); both `snmpget`s return a
sysDescr string identifying the switch model (proves reachability *and*
that the `public` read community works over VLAN 5 from tweed). Switch
management stays on VLAN 5 (`10.1.5.x`) throughout this whole prototype —
it is never carried on the fpgas trunk.

Physical: cable s3300 (switch 1) trunk port 49 → tweed `eth-local`; s2
(switch 2) port 49 → s3300 port 50. **Tim must confirm/correct the actual
trunk port numbers** — `gateway_trunk_port: 49` / `downstream_trunk_ports:
[50]` / `house_uplink_port: 52` in
`ansible/inventory/host_vars/fpgas.online.yml` are placeholders per the
file's own comment ("Trunk port numbers below are placeholders — Tim
confirms exact cabling at hardware prototype time (Task 14)"). If the real
cabling differs, edit that file before Stage 3.

**Rollback**: none — read-only stage.

---

## Stage 2: Capacity re-confirmation

Purpose: risk #1 (S3300 max concurrent VLANs) was already verified and
retired — this is a quick re-confirmation, not a discovery. Both switches
already proved `created=150 verified_present=150` (RESULTS.md,
2026-08-14). Only re-run if you want fresh evidence on hardware-day itself
or suspect switch firmware/config drift since then.

From a host that can reach VLAN 5 (tweed or your laptop with the poe repo
checked out):

```bash
cd fpgas.online-poe
uv run scripts/vlan_capacity_probe.py --host 10.1.5.11 --model s3300 \
  --community pib --count 150
uv run scripts/vlan_capacity_probe.py --host 10.1.5.23 --model gsm7252ps \
  --community private --count 150
```

**Expected**: both runs print `created=150 verified_present=150` and exit
0. VLAN IDs used are 3000–3149 (outside the production 2101–2348 block),
deleted by the script after the check — no VLAN ever left behind.

**Rollback**: none needed in the success case (script cleans up its own
probe VLANs). If a run is killed mid-probe, an orphan VLAN in the
3000–3149 range may be left on the switch; delete it manually via the
switch's own management UI/SNMP, or simply re-run the probe (RESULTS.md
notes this pattern was already hit and is harmless — it only touches its
own 3000-range, never 2101–2348).

---

## Stage 3: Switch converge

Purpose: provision the per-port VLAN plan onto both switches via
`fpgas-switch-setup` (installed from `fpgas.online-poe`, entry point
`switch_setup.cli:main`). Verified exit codes (from
`fpgas.online-poe/src/switch_setup/cli.py`): **0** = in sync / apply
succeeded, **2** = check mode found pending actions (drift), **1** = error
(e.g. missing SNMP community).

On tweed (or wherever `/etc/fpgas/switches.yml` will live — the
`switch-vlans` Ansible role installs it there from host_vars, but you can
run the CLI ad hoc against a local copy of `switches.yml` first if you
want to preview before Stage 4 touches the real host):

```bash
export FPGAS_SWITCH_COMMUNITY_1=pib      # s3300, do not commit
export FPGAS_SWITCH_COMMUNITY_2=private  # s2/GSM7252PS, do not commit

# --- switch 1 (s3300) ---
FPGAS_SWITCH_COMMUNITY="$FPGAS_SWITCH_COMMUNITY_1" \
  fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 1
# review the printed action list (check mode; no --apply). Expect exit 2
# (pending actions) on first run, since the switch starts empty.
FPGAS_SWITCH_COMMUNITY="$FPGAS_SWITCH_COMMUNITY_1" \
  fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 1 --apply
# expect exit 0
FPGAS_SWITCH_COMMUNITY="$FPGAS_SWITCH_COMMUNITY_1" \
  fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 1
# re-check: expect exit 0, no actions printed (idempotent)

# --- switch 2 (s2/GSM7252PS) --- same sequence, --switch 2
FPGAS_SWITCH_COMMUNITY="$FPGAS_SWITCH_COMMUNITY_2" \
  fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 2
FPGAS_SWITCH_COMMUNITY="$FPGAS_SWITCH_COMMUNITY_2" \
  fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 2 --apply
FPGAS_SWITCH_COMMUNITY="$FPGAS_SWITCH_COMMUNITY_2" \
  fpgas-switch-setup --config /etc/fpgas/switches.yml --switch 2
```

`/etc/fpgas/switches.yml` doesn't exist until the `switch-vlans` Ansible
role's `install switches config` task writes it from
`ansible/roles/switch-vlans/templates/switches.yml.j2` — if you're running
the CLI standalone before Stage 4, hand-write an equivalent YAML (same
shape as the `switches:` list in host_vars) or just let Stage 4's Ansible
run install the file first, then use these same commands to double-check
before/after.

**Expected outcome**: each switch converges to have VLANs 2101–2148
(switch 1) / 2201–2248 (switch 2), gateway trunk port 49 tagged with the
full 145–146-VLAN block it must carry, downstream trunk (switch 1 port 50
→ switch 2's block) tagged appropriately, access ports untagged/PVID'd
into their own VLAN and removed from VLAN 1. A second check-mode run
prints nothing and exits 0.

**Known quirk (verified, not a bug)**: the switches' net-snmp CLI
transport intermittently returns `Error in packet` / `commitFailed` on a
VLAN create/delete SNMP write, even though the write actually applied.
`fpgas-switch-setup`'s diff-based convergence absorbs this — just re-run
the same command and it completes (a re-run only issues actions for what's
still not converged). Each SNMP op is ~2s, so a full 48-port converge
takes several minutes; this is expected, not a hang.

**Rollback**: the tool only ever creates/deletes VLANs in the
2101–2348 range (`OWN_RANGE` in `switch_setup/plan.py`) plus the
membership/PVID/VLAN-1-exclusion state of the access ports it owns. VLAN
5 (management) and the house-uplink port are never in its write set — a
mid-run crash or a full rollback cannot touch switch management
connectivity. To roll back a switch's config entirely: edit
`/etc/fpgas/switches.yml` to remove that switch's access ports (or set
`access_ports: 0`) and re-run with `--apply` — the diff engine deletes the
now-undesired VLANs in `OWN_RANGE` (same mechanism proven safe by the
"killed probe leaves an orphan VLAN, re-running the diff tool cleans it
up" behaviour recorded in `tests/lab/RESULTS.md`).

---

## Stage 4: Gateway deploy

Purpose: converge tweed's networkd VLAN interfaces (`vlan-ports` role),
dnsmasq per-port config (`pxe` role), the switch converge from Stage 3
driven by Ansible instead of ad hoc (`switch-vlans` role), and the
isolation firewall (`firewall` role).

**Verified tag caveat** — read before running: on this branch, the tasks
that actually matter for the per-port scheme are NOT all tagged the way
you'd guess from their role name:

- `vlan-ports` role: every task tagged `vlan-ports`. Clean.
- `switch-vlans` role: every task tagged `switch-vlans`. Clean.
- `pxe` role (`ansible/roles/pxe/tasks/main.yml`): the tasks that write
  `/etc/dnsmasq.d/ports.conf` and remove the legacy `pibs.conf`/
  `switch.conf` are tagged `pibs` (a historical name carried over from
  the MAC-table scheme), **not** `pxe`. The "remove legacy MAC-table
  configs" task itself has **no tag at all**, so it only runs in a fully
  untagged play.
- `firewall` role (`ansible/roles/firewall/tasks/main.yml`): the ruleset
  write is tagged `nftables`, **not** `firewall` — there is no `firewall`
  tag in this repo.

So the tag list that actually deploys everything is:

```bash
uv run ansible-playbook ansible/site.yml --limit fpgas.online \
  --tags vlan-ports,pxe,pibs,switch-vlans,nftables
```

`fpgas.online` is in both the `nbp` and `pig` inventory groups (see
`ansible/inventory/hosts`) — this tag list is scoped enough that it will
not touch the `pig` play's `site`/`wssh`/`cam` roles, since those tasks
carry none of the tags above and get skipped.

**The one gap**: "remove legacy MAC-table configs" (no tag) will be
skipped by the tag-restricted run above. If tweed currently has
`/etc/dnsmasq.d/pibs.conf` or `/etc/dnsmasq.d/switch.conf` from a prior
non-per-port deploy, they will keep coexisting with the new
`ports.conf` after this run. Check for and remove them by hand if
present:

```bash
ls /etc/dnsmasq.d/pibs.conf /etc/dnsmasq.d/switch.conf 2>/dev/null
# if either exists:
rm -f /etc/dnsmasq.d/pibs.conf /etc/dnsmasq.d/switch.conf
systemctl restart dnsmasq
```

(This is low-risk here specifically because `eth-local` is currently
dark/offline — no live Pi traffic depends on the old files during the
prototype.)

**Firewall reload safety**: nft's ruleset load is atomic — if the
rendered `/etc/nftables.conf` has a syntax error, `systemctl reload
nftables.service` (the role's notify handler) fails and the *previous*
ruleset stays active; it does not partially apply. The input chain
accepts SSH (`tcp dport ssh`) unconditionally in both the per-port and
legacy templates, so a bad reload cannot lock your SSH session out
either way. As an extra pre-flight, preview the rendered diff before
applying for real:

```bash
uv run ansible-playbook ansible/site.yml --limit fpgas.online \
  --tags vlan-ports,pxe,pibs,switch-vlans,nftables --check --diff
```

Then apply for real:

```bash
uv run ansible-playbook ansible/site.yml --limit fpgas.online \
  --tags vlan-ports,pxe,pibs,switch-vlans,nftables
```

Then verify:

```bash
uv run ansible-playbook ansible/verify-server.yml --limit fpgas.online
```

**Expected**: the apply run completes with no failed tasks (the
`switch-vlans` role's converge task is `changed_when:
switch_vlans_converge_result.stdout | length > 0` — expect `changed` on
first apply if Stage 3 wasn't already run, `ok` if it was). A second
`--check --diff` run of the same tag list shows no changes.
`verify-server.yml` (hosts: `nbp`) passes, including its firewall checks
(`chain input`, `chain internal_networks`, `masquerade` present in `nft
list ruleset`).

**Rollback**: re-run the equivalent Ansible against the previous
host_vars (revert the `switches:` block / `pib_network` values via git,
or simply `git stash`/checkout the pre-branch host_vars) and re-apply the
same tag list — every role here is fully declarative/idempotent
(networkd `.netdev`/`.network` files, `/etc/dnsmasq.d/ports.conf`,
`/etc/nftables.conf` are each regenerated wholesale from host_vars, not
patched incrementally). `eth-local` being dark today means a bad gateway
config has no live Pi traffic to disrupt while you iterate.

---

## Stage 5: Boot test

Purpose: confirm one Pi per switch gets the expected port-derived
identity purely from which port it's plugged into.

1. Plug a Pi into **s3300 (switch 1) port 1**. Power it on (PXE boot).
2. Plug a second Pi into **s2 (switch 2) port 1**. Power it on.

Watch leases and confirm boot:

```bash
tail -f /var/lib/misc/dnsmasq.leases
# or once both have booted:
cat /var/lib/misc/dnsmasq.leases
```

**Expected**:

| | switch 1 port 1 | switch 2 port 1 |
|---|---|---|
| IPv4 | `10.21.1.1` | `10.21.2.1` |
| IPv6 | `2404:e80:a137:2101::1` | `2404:e80:a137:2102::1` |
| Hostname | `pi-sw1-p1` | `pi-sw2-p1` |

Confirm full PXE/NFS boot to login (not just a DHCP lease):

```bash
ssh pi-sw1-p1.fpgas.welland.mithis.com   # or the DNAT/console path you use
ssh pi-sw2-p1.fpgas.welland.mithis.com
```

Both should reach a normal login shell. This is exactly what
`ansible/verify-pi.yml` already asserts in CI for switch 1 port 1
(`Assert 10.21.1.1 address present`, `Assert hostname is pi-sw1-p1`) —
hardware should match the VM test's expectations exactly.

**Rollback**: unplug the Pi(s); no state changes on tweed/switches from
this stage. A stuck/bad lease can be cleared by deleting its line from
`/var/lib/misc/dnsmasq.leases` and restarting dnsmasq if needed, though
normally letting the 12h lease expire is fine.

---

## Stage 6: Isolation matrix

Purpose: prove the core isolation goal — a Pi can reach tweed and the
internet but never a Pi on another port, for both IPv4 and IPv6.

From the switch-1-port-1 Pi (`10.21.1.1` / `2404:e80:a137:2101::1`):

```bash
ping -c3 10.21.2.1                         # switch-2-port-1 Pi, v4 -> FAIL
ping6 -c3 2404:e80:a137:2102::1            # switch-2-port-1 Pi, v6 -> FAIL
ping -c3 10.21.0.1                         # tweed, v4 -> OK
ping6 -c3 2404:e80:a137:2100::1            # tweed, v6 -> OK (if assigned)
ssh root@10.21.0.1                          # tweed -> OK
ping -c3 8.8.8.8                            # internet -> OK
```

On the switch-2-port-1 Pi, run `tcpdump -ni any icmp` while the ping
above runs — it should show **no** ICMP traffic from `10.21.1.1` at all
(the drop happens in tweed's `forward` chain, `v* <-> v*` policy drop; it
never reaches the second switch/Pi).

From tweed itself:

```bash
ping -c3 10.21.1.1     # OK
ping -c3 10.21.2.1     # OK
```

**Expected**: Pi→Pi (both address families) fails outright (no reply, no
port-unreachable — silent drop per the `forward` chain's default `policy
drop` and the explicit comment "v* <-> v* (Pi to Pi) intentionally falls
through to policy drop" in
`ansible/roles/firewall/templates/nftables.conf.j2`). Pi→tweed, Pi→
internet, and tweed→Pi (either switch) all succeed.

**Rollback**: none — read-only stage. If isolation *doesn't* hold, do not
proceed to Stage 7/8; stop and re-check the Stage 4 firewall apply
(`nft list ruleset` on tweed for the `forward` chain's policy and the
`v* -> eth-uplink` accept rule) before continuing.

---

## Stage 7: Port-identity test

Purpose: prove identity follows the port, not the Pi's MAC or SD
card/NFS-root state.

1. Take the Pi currently on s3300 (switch 1) port 1 and move it to s3300
   port 3 (same switch, different port).
2. Power-cycle it (or force a DHCP renew: `dhclient -r && dhclient` for
   v4, and reboot to pick up the new DHCPv6/RA state cleanly — a reboot
   is simplest and matches how this will actually happen in production).

**Expected**: the Pi comes back as `pi-sw1-p3` / `10.21.1.3` /
`2404:e80:a137:2101::3` — purely because port 3's PVID and dnsmasq
tag-selected range differ from port 1's, with **zero** MAC-based
configuration anywhere (`ports.conf.j2`'s own comment: "No MAC addresses
appear anywhere in this file by design"). The old `pi-sw1-p1` lease
simply ages out.

**Rollback**: move the Pi back to port 1 and reboot; it reverts to
`pi-sw1-p1` the same way.

---

## Stage 8: Quarantine test

Purpose: prove an unconfigured/unassigned port doesn't silently join a
per-port network.

Plug a laptop (or any DHCP client) into a switch port that is **not**
one of the provisioned access ports (e.g. an access port beyond
`access_ports: 48`, or intentionally leave one port un-provisioned by
excluding it from `switches:` for this test — do not use a trunk port
49/50/52). Since it isn't a member of any `2101–2348` VLAN, it stays on
untagged VLAN 1 (quarantine) on `eth-local`.

```bash
tail -f /var/lib/misc/dnsmasq.leases
```

**Expected**: the laptop gets a lease in the quarantine pool,
`10.21.0.128`–`10.21.0.150` (from `ports.conf.j2`'s `interface=eth-local`
/ `dhcp-range=tag:eth-local,10.21.0.128,10.21.0.150,255.255.255.0,1h`
stanza) — visibly distinct from any `pi-sw<s>-p<p>` entry, and it has no
`host-record` (no hostname), so it shows up as an unnamed lease. Confirm
it can reach tweed but (per the same forward-chain policy as Stage 6)
cannot reach any per-port Pi.

**Rollback**: unplug the laptop; the lease ages out after 1h (note the
short lease time on the quarantine pool vs. 12h for provisioned ports —
this is intentional, quarantine entries should clear fast).

---

## Stage 9: Record results

Fill in the table below during the hardware-day run. Anything that
deviates from "Expected" above is a deviation — capture what actually
happened, not just pass/fail, and feed it back into the spec/plan (open a
follow-up task rather than silently patching around it here).

| Stage | Date/time | Result | Notes / deviations |
|---|---|---|---|
| 0 — vault communities added | | | |
| 1 — Preflight | | | |
| 2 — Capacity re-confirm | | | |
| 3 — Switch converge (sw1) | | | |
| 3 — Switch converge (sw2) | | | |
| 4 — Gateway deploy | | | |
| 5 — Boot test (sw1 p1) | | | |
| 5 — Boot test (sw2 p1) | | | |
| 6 — Isolation matrix | | | |
| 7 — Port-identity test | | | |
| 8 — Quarantine test | | | |

### Deviations log

(Free-form: anything that didn't match this runbook's "Expected" —
trunk port numbers that differed from the placeholders, unexpected SNMP
errors beyond the known `commitFailed` quirk, lease/timing surprises,
etc. Each entry should say whether it needs a spec/plan amendment or was
a one-off environmental issue.)

-
