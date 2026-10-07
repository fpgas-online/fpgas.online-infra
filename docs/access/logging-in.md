# Logging in

**You want to log in to tweed or to a board, and need the command for where you are.** Which accounts exist: [tweed](tweed.md) and [The Pi NFS root](pi-root.md).

A board's address is `10.21.S.P`, for the board on switch S, port P: `pi-sw2-p29` is `10.21.2.29` ([At a glance](../access.md#at-a-glance)).

| To | Command | Authenticates with |
|---|---|---|
| tweed, as yourself | inside the site, over wg or over IPv6: `ssh <you>@tweed.welland.mithis.com`; from the upstream router: `ssh <you>@10.99.21.2` | your GitHub key |
| tweed, as the automation account | `ssh -6 -i ~/.ssh/fpgas.online-ansible -o IdentitiesOnly=yes ansible@gw.welland.fpgas.online` | the automation key |
| a board, through the jump account | `ssh -J pi@tweed.welland.mithis.com pi@10.21.2.29` | your key at both hops (see the note below) |
| a board, hopping from the jump shell | `ssh pi@tweed.welland.mithis.com`, then `ssh pi@10.21.2.29` | the jump account's own key at the board |
| a board, as the automation account (from the upstream router) | `ssh -i ~/.ssh/fpgas.online-ansible -o IdentitiesOnly=yes -J <you>@10.99.21.2 ansible@10.21.S.P` | your own key at tweed, the automation key at the board |
| a board, as root | `ssh -J <you>@tweed.welland.mithis.com root@10.21.S.P` | your GitHub key at both hops |
| a board, with the password | the board page's terminal on welland.fpgas.online, or `ssh pi@10.21.S.P` from tweed | `pi_pw` |

Every `tweed.welland.mithis.com` in this table assumes you are inside the site
or on the wg route. From the upstream router use `10.99.21.2`, and from outside use
`2404:e80:a137:2100::1` (see [tweed](tweed.md)).

With `-J`, your own key must be trusted at both ends. The jump account trusts
`ssh_public_keys` and `ssh_imports`. The boards trust only the GitHub keys of
`operators_accounts`. Someone in `ssh_imports` who is not an operator can
still reach a board: they hop from the jump shell, or they use the password.

To get the automation key on a controller, extract `vault_ansible_ssh_private_key`
to `~/.ssh/fpgas.online-ansible` (mode 0600), and put the public half beside it
as `~/.ssh/fpgas.online-ansible.pub`. `automation_user` derives the public key
from the private one and refuses to converge if they differ.
[`ansible/ssh.cfg`](../../ansible/ssh.cfg) makes Ansible offer only that key
(`IdentityFile`, and `IdentityAgent none` so no agent keys are offered) and
gives it its own known_hosts file (`~/.config/fpgas-online/ansible_known_hosts`).
