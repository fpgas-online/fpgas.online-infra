# Outbound, from the gateway

**You are setting up a site's upstream and need to know what the gateway must be able to reach on the internet.**

The gateway must be able to reach the internet generally: outbound https,
and http for the Debian and Raspberry Pi package hosts.
The hosts below are examples of what it fetches, and why.

| The gateway fetches | For |
|---|---|
| `deb.debian.org`, `archive.raspbian.org`, `archive.raspberrypi.com` (http), and `apt.fpgas.online` | Its own packages and the package cache it runs for the fleet |
| `ghcr.io` | The prebuilt fleet root file system |
| `github.com`: public ssh keys, release assets, `git+https` clones | Operators' ssh keys; the mediamtx tarball and the Tiny Tapeout commander releases; the site and PoE control packages |
| `raw.githubusercontent.com` | One service unit file fetched while preparing the fleet root |
| A Python package index | The site's `pip` installs |
| Let's Encrypt | Certificates |
| NTP servers, outbound udp 123 (the gateway runs chrony; `roles/pxe` adds only the Pi network's access to it) | Its clock, which the fleet takes from it |

An upstream package cache is **not** required. A site may point the gateway
at one (`apt_client_proxy`), as an optimisation only.
