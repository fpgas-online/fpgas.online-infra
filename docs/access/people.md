# Adding or removing a person

**You want to give someone access, or take it away, and converge the change.** Adding or removing a key on the Pis reboots the whole fleet ([why](pi-root.md#who-owns-authorized_keys-and-why-a-key-change-reboots-the-fleet)).

All the lists are in
[`inventory/group_vars/all/ssh_keys.yml`](../../ansible/inventory/group_vars/all/ssh_keys.yml).

| To | Edit | Effect after the converge |
|---|---|---|
| give someone an operator account (tweed login with sudo, `admin`, and the boards' `pi` and `root`) | add `{name: <unix name>, ssh_import_ids: [gh:<github user>]}` to `operators_accounts` | tweed account and sudoers file (`operators`); their keys on `admin` (`server_user`); their keys in the Pi root (`fixpi`), **which reboots the fleet** |
| let someone use the jump account only | add `gh:<user>` to `ssh_imports`, or their key to `ssh_public_keys` | keys on tweed's `pi` |
| remove an operator | take them out of `operators_accounts` | their keys leave `admin` and the Pi root (**fleet reboot**). Their tweed account stays: also add the name to `operators_retired_accounts` in [`roles/operators/defaults`](../../ansible/roles/operators/defaults/main.yml) to delete it, its home and its sudoers file |
| remove someone from the jump account | take the id out of `ssh_imports` **and** add it to `ssh_imports_revoked`. For a static key, move it from `ssh_public_keys` to `ssh_public_keys_revoked` | the keys are deleted from `pi`. Revoked static keys are also deleted from every operator account |
| revoke one key that has left someone's GitHub account | add it to `ssh_public_keys_revoked` | deleted from the jump and operator accounts. `admin` and the Pis already follow GitHub |

`operators` and `jump` only ever add keys, which is why the revoked lists
exist. Keep an entry in a revoked list until every host has converged without
the key. Imported lines carry a `# ssh-import-id <id>` comment (the format the
old `ssh-import-id` runs used), and that comment is how `ssh_imports_revoked`
finds an id's lines to delete. `fixpi` uses only `gh:` ids, so give operators
`gh:` ids.
## Converging the change

Converge from any controller that reaches `gw.welland.fpgas.online` over IPv6: it is the inventory's
`ansible_host` ([tweed](tweed.md)). tweed's host_vars hold vaulted values, so give Ansible
the vault password as the README's Deploy section does
(`ANSIBLE_VAULT_PASSWORD_FILE`, or `--vault-password-file`). Always the
whole playbook, never a `--tags` subset (issue #157):

```bash
export ANSIBLE_VAULT_PASSWORD_FILE=~/.config/fpgas-online/vault-pass
uv run ansible-playbook ansible/site.yml --limit fpgas.online
```

## Rotating the automation key

The automation key itself is never in these lists. Rotating it means changing
the vaulted private key, `~/.ssh/fpgas.online-ansible{,.pub}` on every
controller, and the preseed's copy, and then converging. `automation_user`
writes the new key exclusively on tweed and fixpi writes it into the Pi root
(fleet reboot).
