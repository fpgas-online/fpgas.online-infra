# Single-owner netboot config.txt — design

Status: implemented (this PR). Issue: #42.

## Problem

The netboot `config.txt` served to the Pis (`{{ nfs_root }}/boot/config.txt`,
which is the TFTP root — `tftp-root=/srv/nfs/rpi/bookworm/boot`) was the stock
RpiOS `config.txt` with lines **appended in place** by four independent tasks in
`fixpi/tasks/tweeks.yml` (radios+uart, eeprom, pi5 header uart, OTG dwc2), using a
mix of `lineinfile` and `blockinfile`. Consequences: the final file is never
visible in one place; independent writers to one file collide (PRs #38/#39 did);
repeated section headers accrete; and a vestigial `config.txt.j2` template (dead
since 2026-03-29) looked authoritative but rendered nothing.

## Goal

One component owns the netboot `config.txt`; nothing else modifies it; the final
result is reviewable in one place; and upstream stock changes are tracked, not
silently lost.

## Design

**Single owner.** `fixpi/templates/boot/config.txt.j2` is the only thing that
writes the netboot `config.txt`. It is:

```
{{ lookup('file', 'boot/config.txt.stock') }}   # stock RpiOS snapshot, verbatim
# fpgas.online managed settings ...             # our overlays/params, sectioned
```

- `fixpi/files/boot/config.txt.stock` is the pristine stock `config.txt` from the
  pinned RpiOS image (`2024-07-04-raspios-bookworm-armhf-lite`), tracked verbatim.
- The managed region carries every setting the four old tasks added, under the
  native `[all]`/`[pi4]`/`[pi5]` conditional sections, with the rationale comments
  carried over from the old tasks.
- Bootloader-handled settings (`uart_2ndstage`, `eeprom_write_protect`) live in
  this file directly, satisfying the RpiOS rule that they only take effect in
  `config.txt` itself (not an `include`).

**Rendering (netboot.yml).** In order: (1) the existing "save original" task now
also moves the extracted stock `config.txt` → `config.txt.org` (idempotent,
`creates:`-guarded — the pristine baseline); (2) the stock-drift gate; (3) the
`template` task renders `config.txt.j2` → `config.txt` (added to the loop that
already renders `cmdline.txt`). The four `tweeks.yml` append tasks are deleted.

**Upstream tracking (hard gate).** After saving `config.txt.org`, an `assert`
compares it (slurped from the built nfs_root) against the tracked snapshot
(`lookup('file', ...)` on the controller), trailing-newline-normalised. Any
difference **fails the build**, with a message telling the operator to reconcile
the template and refresh the snapshot. Because the RpiOS image is pinned, this
fires only when the pin is bumped or a new model image is used — exactly when a
reconcile is wanted. Runbook:
`docs/superpowers/runbooks/2026-09-02-config-txt-single-owner.md`.

**Verification (verify-server.yml).** One `slurp` + `assert` confirms the rendered
`config.txt` preserves stock (sentinels `camera_auto_detect=1`, `arm_boost=1`) and
carries every managed setting; a second asserts `config.txt.org` exists.

## Parity

The render is semantically identical to the previous append output: the same
`(section, directive)` multiset (proven by a normalized comparison — 11 stock + 9
managed directives, identical sets). No behavioural change to the deployed file.

## Idempotency

`config.txt.org` is created once (guard); the gate reads that stable pristine copy;
the render is deterministic. Re-converge is a no-op.

## Scope

Netboot fleet image only. Out of scope: the server-side `/boot/firmware/config.txt`
task (a different file), the Orange-Pi-boot SD image, and `cmdline.txt` (already
templated). Open issue #30 (i2c) should add its lines to the template rather than a
new writer.

## Migration note

On an existing nfs_root already built by the old append tasks, `config.txt` is
stock+appends and there is no `config.txt.org`. On first converge the save task
would capture that polluted file as `config.txt.org`, and the gate would then fail.
Fresh builds (CI, and any rebuilt nfs_root) are clean. For an in-place deploy,
delete `{{ nfs_root }}/boot/config.txt` and `config.txt.org` before converging so
the image re-extracts a pristine stock file (or drop the tracked snapshot in as
`config.txt.org`). See the runbook.
