#!/bin/sh
# Take this board's per-port IPv6 address from the gateway: run a DHCPv6
# client on the interface the NFS root is mounted over. Started by
# fpgas-board-ipv6.service.
#
# The gateway offers the address only by DHCPv6 (roles/pxe ports.conf.j2).
# IPv4 comes from the kernel's ip=dhcp before the root is mounted, and
# nothing else in the root starts a DHCPv6 client, so without this a board
# holds no global IPv6 address (fpgas.online-infra issue 222).
#
# The interface is found, not named: its name differs between board types
# sharing this root. It is the one the kernel routes to the NFS server
# through, which is the one ip=dhcp configured.
set -eu

CONF=/etc/fpgas-board-ipv6.conf
MOUNTS=${FPGAS_BOARD_IPV6_MOUNTS:-/proc/mounts}

# The address of the server the root is mounted from: the NFS mount at /,
# or under overlayroot its read-only lower layer at /media/root-ro.
server=$(awk '
	($2 == "/" || $2 == "/media/root-ro") && ($3 == "nfs" || $3 == "nfs4") {
		n = split($4, opt, ",")
		for (i = 1; i <= n; i++)
			if (opt[i] ~ /^addr=/) {
				print substr(opt[i], 6)
				exit
			}
	}' "$MOUNTS")
if [ -z "$server" ]; then
	echo "fpgas-board-ipv6: no NFS mount with an addr= option at / or /media/root-ro in $MOUNTS: the root is not netbooted, so there is no interface to run DHCPv6 on" >&2
	exit 1
fi

iface=$(ip -o route get "$server" | sed -n 's/.* dev \([^ ]*\).*/\1/p')
if [ -z "$iface" ]; then
	echo "fpgas-board-ipv6: no route to the NFS server $server: cannot tell which interface the root is mounted over" >&2
	exit 1
fi

echo "fpgas-board-ipv6: DHCPv6 on $iface (the route to the NFS server $server)"
# -B: stay in the foreground, under systemd. -f: only our settings.
exec dhcpcd -B -f "$CONF" "$iface"
