# Runbook: single-owner netboot config.txt

The netboot `config.txt` is rendered from one owned template. Nothing else may
write it. Design: `docs/superpowers/specs/2026-09-02-config-txt-single-owner-design.md`.

## The pieces

- `ansible/roles/fixpi/templates/boot/config.txt.j2` — the single owner. Stock
  snapshot (via `lookup('file', 'boot/config.txt.stock')`) + fpgas.online managed
  settings.
- `ansible/roles/fixpi/files/boot/config.txt.stock` — pristine stock `config.txt`
  from the pinned RpiOS image, tracked verbatim.
- `ansible/roles/fixpi/tasks/netboot.yml` — saves the extracted stock file as
  `config.txt.org`, runs the drift gate, then renders `config.txt.j2`.

## Add or change a config.txt setting

Edit `config.txt.j2` — add the line under the right `[all]`/`[pi4]`/`[pi5]`
section in the managed region. Add a `verify-server.yml` assertion for it. Do
**not** add a task that edits `config.txt` anywhere else.

## The stock-drift gate fired (build failed)

Message: "RpiOS stock config.txt differs from fixpi/files/boot/config.txt.stock."
This means the pinned RpiOS image's `config.txt` changed — almost always because
the image pin (`dir_date` in `group_vars/all/srv.yml`) was bumped, or a new model
image is in use. Reconcile:

1. On the build host, diff the freshly-extracted stock against the tracked snapshot:

   ```
   diff -u ansible/roles/fixpi/files/boot/config.txt.stock \
           <nfs_root>/boot/config.txt.org
   ```

2. Decide, per changed line, whether it is a new upstream default we want (most
   are) and whether it interacts with our managed settings.
3. Refresh the snapshot to the new stock:

   ```
   cp <nfs_root>/boot/config.txt.org \
      ansible/roles/fixpi/files/boot/config.txt.stock
   ```

4. If any upstream change affects our managed settings (e.g. a new `[pi6]` section,
   or upstream now sets something we override), update the managed region of
   `config.txt.j2` accordingly.
5. Re-run the build; the gate should pass. Commit both the refreshed snapshot and
   any template change together.

## Migrating an existing nfs_root (in-place deploy)

An nfs_root built by the old append tasks has a polluted `config.txt` and no
`config.txt.org`; the save task would capture the polluted file and the gate would
fail. Before converging this change onto such a host:

```
rm -f <nfs_root>/boot/config.txt <nfs_root>/boot/config.txt.org
```

The next converge re-extracts a pristine stock `config.txt` (img role, guarded by
`creates:`), saves a clean `.org`, passes the gate, and renders. Fresh builds (CI,
or a rebuilt image) need nothing. Deploys are gated on Tim.
