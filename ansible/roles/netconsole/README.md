# netconsole

Receiver for the Pis' kernel logs, on the site gateway.

Every Pi's kernel command line carries `netconsole=@/eth-uplink,@10.21.0.1/`
(`roles/fixpi`), so its kernel sends the log over UDP to the gateway's address on the
local network, port 6666 (netconsole's default). This role runs the receiver that stores
those datagrams.

- `files/netconsole-recv`: stdlib-only Python 3. Appends each received line to
  `<netconsole_dir>/<sender IPv4>.log` as the gateway's receive time (ISO 8601 with
  offset and microseconds), a space, then the kernel's text. Handles the plain and the
  extended (`<level>,<seq>,<ts>,<flags>;text`) formats and several lines per datagram.
  A file at `netconsole_max_file_bytes` stops growing until it is rotated.
- `netconsole-recv.service`: unprivileged `netconsole` user, `Restart=always`, sandboxed
  (read-write only in the log directory).
- `/etc/logrotate.d/netconsole`: daily, or at `netconsole_logrotate_size`; the receiver
  reopens its file for every datagram, so a rename is a complete rotation.

A boot is a burst of datagrams (the Pis log at systemd debug level), so the receiver
asks for a `netconsole_rcvbuf_bytes` receive buffer and the role raises `net.core.rmem_max` to match
(`/etc/sysctl.d/98-fpgas-netconsole.conf`) rather than giving the unit a capability for `SO_RCVBUFFORCE`; the
receiver refuses to start if the kernel grants less, and logs the size it got.

What is stored: the extended format's header (level, sequence, kernel timestamp) is dropped and
only the text kept; the kernel's `ncfrag` fragments of one long message are stored as separate
lines, not reassembled. The per-file size cap bounds one rotation period, not the week's disk.

The log directory is `netconsole_dir`, one group variable
(`inventory/group_vars/all/netconsole.yml`) because the `site` role hands the same path to the
Django site as `FPGAS_NETCONSOLE_DIR`. The directory is `0755` and the files `0644`: the
site reads them without being the `netconsole` user. The site selects a boot's lines by
receive time against the Pi's boot time, so the receiver does no boot detection.

No firewall rule is needed: the input chain accepts everything from the local network
(`internal_networks`). The receiver binds the local address only, never the uplink.

Tests: `tests/test_netconsole_recv.py` (parsing, writing, size cap, the role's wiring);
`tasks/verify/main.yml` (unit active and listening, a probe datagram stored) runs in
`verify-server.yml`.
