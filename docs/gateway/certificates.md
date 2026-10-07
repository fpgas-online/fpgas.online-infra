# Certificates and the web at welland

**You operate welland's gateway, tweed, and want to check, renew or add a TLS certificate there without
breaking the site for IPv6 visitors.** You need a login with `sudo` on tweed. From fpgas.online-infra main,
read 2026-10-07: the `site` role's `tasks/certbot.yml` and its comment, and
`docs/superpowers/runbooks/2026-08-23-tweed-web-deploy.md`.

## How the names reach tweed

tweed has no public IPv4 address. The site's upstream gateway, which is managed separately and is not part of
fpgas.online, is the public IPv4 edge: it passes port 80 to tweed and port 443 through by name (SNI), so
**tweed terminates TLS itself**, with Let's Encrypt certificates got by the webroot method; the HTTP-01
challenges reach tweed through the upstream gateway's port 80. The names the upstream gateway sends to tweed
(from the runbook): `welland.fpgas.online`, `*.welland.fpgas.online` and `welland.fpgas.mithis.com`.
`tinytapeout.fpgas.online` is a CNAME to `welland.fpgas.online`; it had to be added to the upstream gateway's
list, and that gateway's proxy redeployed, before its certificate could be issued. A new name needs the same:
ask the upstream gateway's owner first. tweed also answers on IPv6 directly (its global address
`2404:e80:a137:2100::1`, `host_vars/fpgas.online.yml`).

## The rules

> **Warning:** - Get certificates with `certbot certonly --webroot` only. Never `certbot --nginx`.
> - Never install `python3-certbot-nginx`: it is the plugin that rewrites nginx configuration.
> - A certificate lineage first made by `certbot --nginx` must be switched to the webroot authenticator, or the
>   next renewal runs the nginx installer again and undoes the vhost Ansible owns.

Why: on 2026-08-23 a `certbot --nginx` vhost without an IPv6 `listen` line left the Tiny Tapeout vhost as the
only, and so the default, server on `[::]:443`, and every IPv6 visitor to `welland.fpgas.online` was handed
the wrong certificate (the role's comment; fixed 2026-08-30).

## Check

```console
$ sudo certbot certificates          # each lineage, its names, its expiry
$ sudo certbot renew --dry-run       # a renewal against the test server; changes nothing
```

tweed runs the snap build of certbot, and the role installs Debian's only when no `/usr/bin/certbot` exists,
because Debian's older one cannot read the snap's renewal files. The role writes no renewal timer of its own; renewal is left to the installed certbot.

## Renew or add a name

The `site` role gets the certificate for the gateway's own name, only when none exists, with:

```console
$ sudo certbot certonly --webroot -w /var/www/html -d welland.fpgas.online \
    --email <letsencrypt_account_email, group_vars/all/site.yml> --agree-tos --non-interactive
```

and the `ttsite` role does the same for `tinytapeout.fpgas.online`. To renew one now:
`sudo certbot renew --cert-name <name>`. A new name is more than a certificate: the vhosts serve only the gateway's `domain_name` and the Tiny Tapeout
site's `ttsite_domain`, so a third name needs a change to the `site` or `ttsite` role (a pull request to
fpgas.online-infra), after the upstream gateway routes the name (above). Deploying that change
([Deploying to a gateway](deploy.md)) then gets its certificate the same way. Check from outside over both IPv4 and IPv6 that each name
is served with its own certificate.
