# The Pi NFS root

**You operate the welland fleet and want to know which accounts and keys every netbooted board has, where they come from, and why changing one reboots the fleet.**

Every netbooted board shares one root, so every board has the same accounts,
keys and host key.

| Account | uid:gid | sudo | Password | `authorized_keys` |
|---|---|---|---|---|
| `pi` | 1000:1000 | NOPASSWD (`/etc/sudoers.d/010_pi-nopasswd`) | from `pi_pw` (see below) | server-user key, controller key, operators' GitHub keys, jump account key |
| `root` | 0:0 | | | server-user key, controller key, operators' GitHub keys |
| `ansible` | 1001:1001 | NOPASSWD (`/etc/sudoers.d/010_ansible-nopasswd`) | locked | the controller key only (`key_sources: [controller]`) |

- The CI image build creates `pi` and `ansible` (`fixpi/tasks/netboot.yml`,
  `fixpi_image_build`). `ansible`'s uid and gid are pinned by
  `fixpi_ansible_uid` so every image agrees.
- **The `pi` password.** `pi_pw` (vaulted in host_vars) is the plaintext
  shared password. `fixpi/tasks/userconf.yml` writes its sha512-crypt hash
  into the root's `/etc/shadow`, on the gateway only: never in the CI image
  (`fixpi_image_build`). `roles/site` passes it,
  base64-encoded, to Django as `PI_PW`. The board page's web terminal (wssh)
  and the upload page log in to `pi@10.21.S.P` with it.
  `fixpi/files/etc/ssh/sshd_config.d/password.conf` keeps
  `PasswordAuthentication yes` on the Pis for that reason. This is
  intentional; it is only tweed that is key-only.
- **The ssh login banner.** The board pages say "password is in login
  banner", so the boards' sshd shows one before the password prompt (#215):
  `fixpi/tasks/userconf.yml` writes `/etc/ssh/sshd_banner` in the root from
  `fixpi/templates/etc/ssh/sshd_banner.j2`, with the lines `user: pi` and
  `password: <pi_pw>`, and the drop-in `sshd_config.d/banner.conf` sets
  `Banner` to it. Like the password itself it is written on the gateway
  only, never in the CI image. The console banner (`/etc/issue.d`) is a
  separate file in getty's own format and does not carry the password.
- **The keys** (`fixpi_authorized_keys_files`, built by
  [`fixpi/tasks/authorized_keys.yml`](../../ansible/roles/fixpi/tasks/authorized_keys.yml)):

  | Source | What | Variable |
  |---|---|---|
  | server | tweed's `/home/admin/.ssh/id_rsa.pub` | `fixpi_server_user_pubkey` |
  | controller | the `fpgas.online-ansible` public key | `ansible_ssh_private_key_file` + `.pub` ([`group_vars/all/controller.yml`](../../ansible/inventory/group_vars/all/controller.yml)) |
  | github | `https://github.com/<user>.keys` for the `gh:` ids in `operators_accounts` (mithro, CarlFK), each line tagged `gh:<user>` | `fixpi_github_key_users`, `fixpi_github_keys_base_url` |
  | jump | tweed's `/home/pi/.ssh/id_ed25519.pub`, `pi` only | `fixpi_jump_ssh_pubkey` |

  The root gets exactly the keys GitHub lists at the time of the converge.
  The download runs first in site.yml's "Update the Pi NFS root" play, before
  the update lock and the image extraction, so a failed download stops the
  run before the root is touched.
- **Host key.** Every board presents the same ED25519 host key,
  `SHA256:tL3Mm5hn0pSKtUhZxl9CuJTMh5fFpAYxPdFq5tGlRhI` (public key ending
  `…hN/k2`). It is the site's, not the image's. The img pull's rsync
  excludes `/etc/ssh/ssh_host_*`, so a new image keeps it (#131).

## Who owns `authorized_keys`, and why a key change reboots the fleet

The image ships no `authorized_keys`. The img pull
([`roles/img/tasks/pull.yml`](../../ansible/roles/img/tasks/pull.yml)) excludes
`/root/.ssh/authorized_keys` and `/home/*/.ssh/authorized_keys` from its
`rsync --delete`. fixpi builds the complete list and writes each file once
with `copy`, which leaves an identical file alone (#140). So a converge with
no key change touches none of them.

A real key change replaces the file, which gives it a new inode. The booted
boards' overlay over NFS answers that with ESTALE. The change also counts as a
root change for `nfsroot_generation`, which bumps the generation, and each
board's nfsroot-watchdog then reboots it in its stagger slot (up to about 33
minutes for a two-switch site, `onpi_nfsroot_watchdog_*`). **Adding or removing
a Pi key therefore reboots the whole fleet.** Changing `pi_pw` does too: the
rewritten `/etc/shadow` makes every booted board refuse SSH until it reboots.
