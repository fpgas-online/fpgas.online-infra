#!/bin/sh
# Ensure this host's own (DHCP-assigned) name resolves locally, so sudo's
# per-invocation getaddrinfo() of the machine name is instant instead of
# stalling on DNS. Run from fpgas-hostname-hosts.service on boot. Kept as a
# script (not inline in the unit's ExecStart) because systemd expands $ and %
# there -- notably %s means "the service user's shell", which silently
# corrupted an earlier inline version into "127.0.1.1 /bin/bash".
set -eu

H=$(hostname)
if ! grep -qw "$H" /etc/hosts; then
	printf '127.0.1.1\t%s\n' "$H" >> /etc/hosts
fi
