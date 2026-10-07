# The PoE scripts in fpgas.online-poe

**You operate the fleet and found `poe.sh`, `allpoe.sh` or `allpoeoff.sh` and want to know what each does and where it works.** They work only on a gateway whose settings the `site` role's `snmp.yml` writes, which is the ps1 pattern (a legacy MAC-table gateway). At welland they do not run: use [Power-cycling a board](power-cycle.md). The scripts are in [fpgas.online-poe](https://github.com/fpgas-online/fpgas.online-poe) (`scripts/`). The line numbers and the ps1 statements are from the earlier docs page, not re-checked; the points marked "main" were read on 2026-10-07.

## What ships

The scripted path ships in `fpgas.online-poe` and runs **on the gateway**, which is where the SNMP credentials live and the only host with a route to the switch:

```console
$ # read the current PoE state of port 20
$ poe.sh 20
$ # 1 is on, 2 is off
$ poe.sh 20 2
$ poe.sh 20 1
$ # every port in pi_ports, one second apart
$ allpoe.sh 1 1
```

The three scripts do not share a mechanism, and the differences matter:

- **`poe.sh <port> [1|2]`** reads the port, or sets it when given a value, with `1` on and `2` off. It sources `/etc/environment.export` (line 39), but its live path is line 57: it shells out to `snmp_switch/utils.py` inside the Django venv at `/srv/www/pib/venv`, which is what actually reads `SNMP_SWITCH_*` from the environment. Everything below the script's `exit` (the hand-written `snmpget.py` / `snmpset.py` invocations with the credentials spelled out as flags) is dead code kept as a reference.
- **`allpoe.sh <1|2> [sleep]`** sources the same file (line 8) purely for `pi_ports`, then loops `poe.sh $p $1` over it with a sleep between calls, defaulting to one second.
- **`allpoeoff.sh [sleep]`** does **not** source anything. It hardcodes `pi_ports=$(seq 1 48)` (line 7) and the legacy `ip_base=10.21.0` / `o_base=100` (lines 8-9), then powers every one of those 48 ports off. Its single argument is the sleep time, not an on/off value, and the active loop never uses it; the loop that would have issued a clean shutdown first is commented out, on the grounds that an overlayrooted Pi has nothing to flush. (`allpoeoff.sh` read in fpgas.online-poe main, 2026-10-07.)

## Why they do not run at welland

`SNMP_SWITCH_*` and `pi_ports` are written by the `site` role's `snmp.yml`, which is guarded by `when: switch.mpi_port is defined`, a field the per-port scheme removed; `pi_ports` would come from `switch.nos`, which a per-port host does not have either. The task's own comment says the whole task is skipped on those hosts, by design (`roles/site/tasks/snmp.yml`, main, read 2026-10-07). The `switch:` block still in Welland's `host_vars` points at `10.21.0.200` (`host_vars/fpgas.online.yml`, main), an address the per-port firewall no longer DNATs to, since switch management moved to the house network.

> [!NOTE]
> The two halves also disagree about a filename. `snmp.yml` writes `/etc/environment` and `/etc/environment.exports` (plural, main, read 2026-10-07); `poe.sh` sources `/etc/environment.export`, singular, and nothing in fpgas.online-infra creates that name (the script side is from the earlier docs page, not re-checked). The ps1 procedure on [The ps1 gateway and switch](https://docs.fpgas.online/en/latest/sites/ps1-gateway.html#power-control) sources the singular name and works, so the file exists on the ps1 gateway (called val2 in the site notes), but it is not one the roles put there, and a rebuilt gateway would not have it.

## The replacement

The replacement for all of this is the gateway service in [fpgas.online-gw](https://github.com/fpgas-online/fpgas.online-gw), which is to expose PoE control per board slug over its HTTP API, with SNMP staying on the gateway so the web tier never holds a credential or a route to the Pi network. Its `main` README describes that much and says the full API documentation lands with the implementation on the `impl-api` branch (read 2026-10-07), so treat the whole interface as planned rather than deployed, and take the request shape from the repository rather than from here.
