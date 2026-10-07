# Accounts and logins (Welland)

<a id="accounts-and-logins"></a>

**You look after access to the welland gateway and fleet: who can log in, how, and how to change it.** Each task has its own page, listed under [The tasks](#the-tasks).

Who can log in to the Welland gateway (tweed, inventory host `fpgas.online`)
and to the netbooted Pi fleet, with what, and which role and variable decide
it. Everything here is what `main` configures. It was deployed to tweed
(`main` 4de0b24) and checked live on 2026-09-29:

- a password-only login to tweed gets `Permission denied (publickey)`;
- `ansible` (automation key), and `admin` and `tim` (GitHub keys), log in and
  get `sudo -n` to root, and `carl` holds exactly his GitHub key;
- the jump account refuses commands, and its `authorized_keys` holds only Tim's
  and Carl's GitHub keys (the Launchpad lines are gone);
- through `-J pi@tweed`, `pi@` and `root@` a board work with Tim's key, and
  `ansible@` a board works with the automation key, including `sudo`;
- the jump account's own key logs in to `pi@` a board.

No secrets are recorded here. The only private material involved is the
`fpgas.online-ansible` automation key (vaulted as
`vault_ansible_ssh_private_key` in
[`host_vars/fpgas.online.yml`](../ansible/inventory/host_vars/fpgas.online.yml))
and the vaulted `pi_pw`. The `pi` password is public by design: the board
pages on fpgas.online publish it, and the boards are ephemeral and isolated
per port.

## At a glance

| Where | Login methods | Who |
|---|---|---|
| tweed | SSH public key only ([`roles/sshd`](../ansible/roles/sshd)) | `ansible` (automation), `admin` (site services), `tim` and `carl` (operators), `pi` (restricted jump), `root` (key only) |
| Pi NFS root, every board `pi-sw<S>-p<P>` = `10.21.<S>.<P>` | public key, plus a password for `pi` | `pi` (the web terminal and people), `root`, `ansible` (automation) |

The Pis are not routable from outside tweed, so every login to a board goes
through tweed.

## The tasks

<a id="the-gateway-tweed"></a>
<a id="tweed"></a>
<a id="sshd"></a>
- [tweed](access/tweed.md): its accounts, keys, the jump account and sshd.

<a id="the-pis"></a>
<a id="the-pi-nfs-root"></a>
<a id="who-owns-authorized_keys-and-why-a-key-change-reboots-the-fleet"></a>
- [The Pi NFS root](access/pi-root.md): the boards' accounts, the `pi` password, the keys and the host key, and
  why a key change reboots the fleet.

<a id="logging-in"></a>
- [Logging in](access/logging-in.md): the command for each account, from inside the site, over IPv6 or through
  the upstream router.

<a id="changing-who-has-access"></a>
<a id="adding-or-removing-a-person"></a>
- [Adding or removing a person](access/people.md), converging the change, and rotating the automation key.

<a id="where-the-keys-come-from-and-when-github-is-down"></a>
<a id="where-the-keys-come-from"></a>
- [Where the keys come from](access/keys.md), and what a converge does when GitHub is down.

<a id="verifying"></a>
- [Verifying](access/verifying.md): which checks in `verify-server.yml` and `verify-pi.yml` cover access.
