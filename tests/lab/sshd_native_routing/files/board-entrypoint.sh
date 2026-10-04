#!/bin/sh
# Lab board. Environment:
#   PI_PASSWORD   password of user pi (the public one, unless the test says otherwise)
#   HOST_KEY      file under /lab/keys to use as the Ed25519 host key
#   EXEMPT        if set, written as PerSourcePenaltyExemptList (OpenSSH 9.8+ only)
set -eu
echo "pi:${PI_PASSWORD}" | chpasswd
install -m 600 "/lab/keys/${HOST_KEY:-fleet_key}" /etc/ssh/ssh_host_ed25519_key
{
    echo "HostKey /etc/ssh/ssh_host_ed25519_key"
    echo "PasswordAuthentication yes"
    echo "LogLevel VERBOSE"
    echo "Banner /etc/issue.net"
    if [ -n "${EXEMPT:-}" ]; then echo "PerSourcePenaltyExemptList ${EXEMPT}"; fi
} > /etc/ssh/sshd_config.d/lab.conf
echo "fpgas.online board $(hostname): login pi, password ${PI_PASSWORD}" > /etc/issue.net
echo "hello from $(hostname) port 8000" > /srv/www/index.html
busybox httpd -p 8000 -h /srv/www
exec /usr/sbin/sshd -D -e
