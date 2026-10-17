#!/bin/sh
# Lab gateway: set up ONE way of making board names exist and ONE hand-off
# variant, then run the distribution's sshd in the foreground.
#
#   LOOKUP    accounts | extrausers | nss | userdb     how getpwnam() finds a board name
#   VARIANT   password | none | prompt | jump | rawtcp what the Match block does
#   SANDBOX   chroot | none                            sshd ChrootDirectory or not
#   NFT       1 | 0                                    the uid-keyed output filter
#   STARTUPS  default | hardened                       PerSourceMaxStartups + LoginGraceTime
#   NET, SWITCH_PORTS, BOARD_PASSWORD                  the site's shape and the public password
#
# In production every file written here would be an Ansible template
# generated from the same port map that makes the VLANs and the DNS records.
set -eu
LOOKUP=${LOOKUP:-extrausers}
VARIANT=${VARIANT:-password}
SANDBOX=${SANDBOX:-chroot}
NFT=${NFT:-1}
STARTUPS=${STARTUPS:-default}
NET=${NET:-10.121}
SWITCH_PORTS=${SWITCH_PORTS:-48 48}
B_UID=950
B_GID=950
RELAY_DIR=/usr/lib/fpgas-board-relay
RELAY=$RELAY_DIR/relay
CHROOT=/srv/fpgas-board-relay

names() {
    sw=0
    for n in $SWITCH_PORTS; do
        sw=$((sw + 1))
        p=1
        while [ "$p" -le "$n" ]; do
            echo "pi-sw${sw}-p${p}"
            p=$((p + 1))
        done
    done
}

# --- the administrators' side: exactly roles/sshd's key-only drop-in -------
useradd -m -s /bin/bash alice
install -d -m 700 -o alice -g alice /home/alice/.ssh
install -m 600 -o alice -g alice /lab/keys/admin_key.pub /home/alice/.ssh/authorized_keys
cat > /etc/ssh/sshd_config.d/00-pubkey-only.conf <<'EOF'
PubkeyAuthentication yes
PasswordAuthentication no
KbdInteractiveAuthentication no
AuthenticationMethods publickey
PermitRootLogin prohibit-password
EOF
rm -f /etc/ssh/ssh_host_*
install -m 600 /lab/keys/gateway_key /etc/ssh/ssh_host_ed25519_key
install -m 644 /lab/keys/gateway_key.pub /etc/ssh/ssh_host_ed25519_key.pub
{
    echo "HostKey /etc/ssh/ssh_host_ed25519_key"
    echo "LogLevel VERBOSE"
    if [ "$STARTUPS" = hardened ]; then
        echo "PerSourceMaxStartups 5"
        echo "LoginGraceTime 30"
    fi
} > /etc/ssh/sshd_config.d/10-lab.conf

# --- the one shared unprivileged identity ----------------------------------
groupadd -g "$B_GID" fpgas-board
# A real, locked account with the shared uid, so that ps/ls/logs show a name
# for it. It is NOT in group fpgas-board, so the Match block never applies
# to it, and it has no password and no key.
useradd -u "$B_UID" -g nogroup -d / -M -s /usr/sbin/nologin -c "fpgas.online board relay" fpgas-board

case "$VARIANT" in
    none | prompt) HASH="" ;; # empty password: sshd's "none" method logs the name in
    *) HASH=$(openssl passwd -6 "$BOARD_PASSWORD") ;;
esac
case "$VARIANT" in
    rawtcp) SHELL_PATH=/bin/sh ;;
    *) SHELL_PATH=$RELAY ;;
esac

# --- LOOKUP: how a board name becomes a passwd entry ------------------------
case "$LOOKUP" in
    accounts)
        # One real account per port, all with the shared uid.
        for name in $(names); do
            useradd -o -u "$B_UID" -g fpgas-board -d / -M -s "$SHELL_PATH" -p "$HASH" "$name"
        done
        ;;
    extrausers)
        # One generated file beside /etc/passwd, read by libnss-extrausers.
        mkdir -p /var/lib/extrausers
        : > /var/lib/extrausers/group
        for name in $(names); do
            echo "${name}:x:${B_UID}:${B_GID}:fpgas.online board login:/:${SHELL_PATH}"
        done > /var/lib/extrausers/passwd
        for name in $(names); do
            echo "${name}:${HASH}:::::::"
        done > /var/lib/extrausers/shadow
        chown root:shadow /var/lib/extrausers/shadow
        chmod 640 /var/lib/extrausers/shadow
        sed -i -e 's/^passwd:.*/passwd: files extrausers/' -e 's/^shadow:.*/shadow: files extrausers/' /etc/nsswitch.conf
        ;;
    nss)
        # No list at all: the custom module answers by formula.
        mkdir -p /etc/fpgas-board
        echo "$B_UID $B_GID $SWITCH_PORTS" > /etc/fpgas-board/nss.conf
        echo "$HASH" > /etc/fpgas-board/shadow
        chown root:shadow /etc/fpgas-board/shadow
        chmod 640 /etc/fpgas-board/shadow
        sed -i -e 's/^passwd:.*/passwd: files fpgasboard/' -e 's/^shadow:.*/shadow: files fpgasboard/' /etc/nsswitch.conf
        ;;
    userdb)
        # systemd's drop-in user records, read through nss-systemd.
        mkdir -p /etc/userdb
        for name in $(names); do
            printf '{"userName":"%s","uid":%s,"gid":%s,"realName":"fpgas.online board login","homeDirectory":"/","shell":"%s","disposition":"system"}\n' \
                "$name" "$B_UID" "$B_GID" "$SHELL_PATH" > "/etc/userdb/${name}.user"
            printf '{"privileged":{"hashedPassword":["%s"]}}\n' "$HASH" > "/etc/userdb/${name}.user-privileged"
            chmod 600 "/etc/userdb/${name}.user-privileged"
        done
        sed -i -e 's/^passwd:.*/passwd: files systemd/' -e 's/^shadow:.*/shadow: files systemd/' /etc/nsswitch.conf
        ;;
    *)
        echo "unknown LOOKUP $LOOKUP" >&2
        exit 2
        ;;
esac

# --- the relay's files, inside sshd's chroot or on the real root -----------
install_relay_conf() {
    root=$1
    mkdir -p "$root/etc/board-relay"
    echo "$NET $SWITCH_PORTS" > "$root/etc/board-relay/site.conf"
    # One pinned line for every board: all boards present the fleet key.
    echo "fpgas-fleet $(cut -d' ' -f1,2 /lab/keys/fleet_key.pub)" > "$root/etc/board-relay/known_hosts"
    cat > "$root/etc/board-relay/ssh_config" <<'EOF'
Host *
    User pi
    AddressFamily inet
    HostKeyAlias fpgas-fleet
    UserKnownHostsFile /etc/board-relay/known_hosts
    GlobalKnownHostsFile /dev/null
    StrictHostKeyChecking yes
    UpdateHostKeys no
    CheckHostIP no
    VerifyHostKeyDNS no
    KnownHostsCommand none
    HostKeyAlgorithms ssh-ed25519
    PreferredAuthentications password
    PubkeyAuthentication no
    KbdInteractiveAuthentication no
    GSSAPIAuthentication no
    HostbasedAuthentication no
    IdentityAgent none
    IdentitiesOnly yes
    IdentityFile none
    NumberOfPasswordPrompts 1
    ForwardAgent no
    ForwardX11 no
    ClearAllForwardings yes
    Tunnel no
    PermitLocalCommand no
    EscapeChar none
    ControlMaster no
    ControlPath none
    ProxyCommand none
    ConnectTimeout 5
    ConnectionAttempts 1
    ServerAliveInterval 30
    ServerAliveCountMax 4
    LogLevel ERROR
EOF
    rm -f "$root/etc/board-relay/password"
    if [ "$VARIANT" != prompt ]; then
        echo "$BOARD_PASSWORD" > "$root/etc/board-relay/password"
        chgrp "$B_GID" "$root/etc/board-relay/password"
        chmod 640 "$root/etc/board-relay/password"
    fi
}

build_chroot() {
    root=$CHROOT
    mkdir -p "$root/usr/bin" "$root$RELAY_DIR" "$root/etc" "$root/dev"
    ln -s usr/lib "$root/lib"
    ln -s usr/lib64 "$root/lib64"
    cp /usr/bin/ssh "$root/usr/bin/ssh"
    ldd /usr/bin/ssh | awk '$2 == "=>" && $3 ~ /^\// {print $3} $1 ~ /^\// {print $1}' | while read -r lib; do
        dir=$(realpath "$(dirname "$lib")")
        mkdir -p "$root$dir"
        cp -L "$lib" "$root$dir/$(basename "$lib")"
    done
    cp "$RELAY" "$root$RELAY"
    ln "$root$RELAY" "$root$RELAY_DIR/askpass"
    cp "$RELAY_DIR/probe" "$root$RELAY_DIR/probe" # lab only
    # ssh refuses to run for a uid with no passwd entry.
    echo "fpgas-board:x:${B_UID}:${B_GID}::/:${RELAY}" > "$root/etc/passwd"
    install_relay_conf "$root"
    chown -R root:root "$root"
    if [ -e "$root/etc/board-relay/password" ]; then
        chgrp "$B_GID" "$root/etc/board-relay/password"
    fi
    chmod -R go-w "$root"
    mknod -m 666 "$root/dev/null" c 1 3
}

if [ "$SANDBOX" = chroot ]; then
    build_chroot
else
    install_relay_conf ""
fi

# --- the Match block ---------------------------------------------------------
{
    echo "Match Group fpgas-board"
    case "$VARIANT" in
        none | prompt)
            echo "    PasswordAuthentication yes"
            echo "    PermitEmptyPasswords yes"
            echo "    AuthenticationMethods any"
            ;;
        *)
            echo "    PasswordAuthentication yes"
            echo "    AuthenticationMethods password"
            echo "    PermitEmptyPasswords no"
            echo "    Banner /etc/ssh/fpgas-board-banner"
            ;;
    esac
    echo "    PubkeyAuthentication no"
    echo "    KbdInteractiveAuthentication no"
    echo "    AuthorizedKeysFile none"
    echo "    MaxAuthTries 3"
    if [ "$SANDBOX" = chroot ] && [ "$VARIANT" != rawtcp ]; then
        echo "    ChrootDirectory $CHROOT"
    fi
    if [ "$VARIANT" = rawtcp ]; then
        echo "    ForceCommand /usr/local/bin/raw-relay"
        echo "    PermitTTY no"
    else
        echo "    ForceCommand relay"
        echo "    Subsystem sftp fpgas-board-sftp"
        echo "    PermitTTY yes"
    fi
    if [ "$VARIANT" = jump ]; then
        # sshd itself opens the TCP connection (ssh -J / -W); board port 22 only.
        echo "    AllowTcpForwarding local"
        printf '    PermitOpen'
        for name in $(names); do
            rest=${name#pi-sw}
            printf ' %s.%s.%s:22' "$NET" "${rest%%-*}" "${rest##*-p}"
        done
        echo
    else
        echo "    DisableForwarding yes"
        echo "    AllowTcpForwarding no"
        echo "    PermitOpen none"
    fi
    echo "    AllowStreamLocalForwarding no"
    echo "    AllowAgentForwarding no"
    echo "    X11Forwarding no"
    echo "    PermitTunnel no"
    echo "    PermitListen none"
    echo "    GatewayPorts no"
    echo "    PermitUserRC no"
    echo "    MaxSessions 4"
    echo "    ClientAliveInterval 30"
    echo "    ClientAliveCountMax 4"
} > /etc/ssh/sshd_config.d/60-fpgas-board.conf
echo "fpgas.online board login. The password is public: ${BOARD_PASSWORD}" > /etc/ssh/fpgas-board-banner

cat > /usr/local/bin/raw-relay <<EOF
#!/bin/sh
# Evaluation only (variant rawtcp): a plain TCP pipe to the board's sshd.
case "\$USER" in
    pi-sw[1-9]-p[1-9] | pi-sw[1-9]-p[1-9][0-9]) ;;
    *) exit 126 ;;
esac
rest=\${USER#pi-sw}
exec nc "${NET}.\${rest%%-*}.\${rest##*-p}" 22
EOF
chmod 755 /usr/local/bin/raw-relay

# --- limits for the shared uid (pam_limits) ---------------------------------
cat > /etc/security/limits.d/fpgas-board.conf <<EOF
@fpgas-board hard nproc ${NPROC:-200}
@fpgas-board hard core 0
EOF

# --- PAM=dedicated: a PAM stack of their own for board names ----------------
# sshd's PAMServiceName may be set inside a Match block (OpenSSH 9.8+). The
# dedicated stack checks the password with pam_unix and applies the limits;
# it has no pam_systemd (no logind session, no per-user service manager for
# visitors), no motd, no mail. Administrators keep Debian's stock stack.
if [ "${PAM:-dedicated}" = dedicated ]; then
    case "$VARIANT" in
        none | prompt) NULLOK=" nullok" ;;
        *) NULLOK="" ;;
    esac
    cat > /etc/pam.d/sshd-fpgas-board <<EOF
auth     required  pam_unix.so${NULLOK}
account  required  pam_nologin.so
account  required  pam_unix.so
session  required  pam_limits.so
session  required  pam_unix.so
EOF
    echo "    PAMServiceName sshd-fpgas-board" >> /etc/ssh/sshd_config.d/60-fpgas-board.conf
fi

# --- the uid-keyed output filter ---------------------------------------------
if [ "$NFT" = 1 ]; then
    elements=""
    sw=0
    for n in $SWITCH_PORTS; do
        sw=$((sw + 1))
        elements="${elements}${elements:+, }${NET}.${sw}.1-${NET}.${sw}.${n}"
    done
    cat > /etc/nftables-fpgas-board-relay.nft <<EOF
table inet fpgas_board_relay {
    set boards {
        type ipv4_addr
        flags interval
        elements = { ${elements} }
    }
    set per_board {
        type ipv4_addr
        flags dynamic
    }
    chain output {
        type filter hook output priority filter; policy accept;
        # Positive match only. "meta skuid != ${B_UID} accept" would be wrong:
        # a packet with no socket (neighbour discovery, a kernel reset) has
        # no uid, matches neither form, and would fall through to the reject.
        meta skuid ${B_UID} jump board_relay
    }
    chain board_relay {
        ct state established,related accept
        ip daddr @boards tcp dport 22 ct state new add @per_board { ip daddr ct count over ${PER_BOARD:-8} } counter reject with tcp reset
        ip daddr @boards tcp dport 22 ct state new counter accept
        counter reject with icmpx admin-prohibited
    }
}
EOF
    nft -f /etc/nftables-fpgas-board-relay.nft
fi

/usr/sbin/sshd -t
exec /usr/sbin/sshd -D -e
