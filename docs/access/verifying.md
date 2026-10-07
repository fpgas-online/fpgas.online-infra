# Verifying

**You changed access, or deployed, and want to check that logins are as configured.**

Both playbooks run in full; the table lists the checks in them that cover
access.

| Playbook, role | Checks |
|---|---|
| [`verify-server.yml`](../../ansible/verify-server.yml): `automation_user` | `ansible` exists, its sudoers file is valid, its `authorized_keys` is exactly the automation key, and a fresh login as it gets `sudo -n` to root |
| `operators` | each operator exists, has a valid sudoers file and a non-empty `authorized_keys` without revoked keys; retired accounts, their groups and their files are gone |
| `jump` | the rbash shell, `~/bin` exactly `ssh` and `ssh-keyscan`, the keypair, no sudoers file, no revoked keys, `sshd -T` shows the ForceCommand, and the wrapper refuses other commands |
| `sshd` | the drop-in exists, and `sshd -T` for `ansible`, `pi` and `root` gives key-only login and `PermitRootLogin prohibit-password` |
| `server_user` | `admin` exists with home `/home/admin`, `videoteam` is gone, no unit runs as it, `authorized_keys` equals the published GitHub keys exactly, and `sudo -n` works |
| `fixpi` | among its other NFS root checks, the root's `ansible` account: uid and gid 1001, locked password, valid sudoers file, and exactly the automation key, owned by 1001 and mode 0600 |
| [`verify-pi.yml`](../../ansible/verify-pi.yml) | on a booted board: `pi`'s password is usable (`passwd -S` = `P`), and `ansible` logs in with the automation key, gets `sudo -n` to root and has a locked password |
| `verify-pi.yml` | tweed's jump account reaches the board as `pi` with its own key (`BatchMode`) |

```bash
uv run ansible-playbook ansible/verify-server.yml --limit fpgas.online
```

The VM test ([`vm-test.yml`](../../.github/workflows/vm-test.yml)) runs both
playbooks on every pull request against a test server that is configured like
tweed (`sshd_pubkey_only` and `automation_user_manage` both on, `user_name:
admin`).
