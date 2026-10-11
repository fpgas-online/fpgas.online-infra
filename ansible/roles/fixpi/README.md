Start with a stock RaspiOS file system (what img2files.sh produced)

### netboot.yml
fiddle with tftp paths because bootcode.bin is dumb and doesn't GET files from where you tell it to
config.txt - um... disable bt and enable serial?
cmdline.txt - root=nfs
etc/fstab / and /boot/firmware nfs

### userconf.yml
hash the pi user's password (sha512-crypt, salt derived from it)
check whether the NFS root already carries that hash
set the pi user's password in the NFS root
reboot every Pi booted from this root (their sshd cannot read the replaced shadow)
remove userconf.txt and the password marker
enable sshd
create issue.d dir
display IP, pw and things on console
# get fixed sshswitch

sshd password settings
Generate ssh keys for server user
create .ssh dirs
Generate ssh keys for pi users pi and root
Write the NFS root's authorized_keys (authorized_keys.yml): the complete
key list for root and pi (server user, controller, operators' GitHub keys,
for pi the gateway jump account, and for pi the static keys of
`fixpi_static_public_keys`), and for the ansible automation
account the controller key alone, written once and only when it changed
(ansible-home.yml, before userconf.yml, checks the account is in the image
and makes its .ssh dir). The GitHub keys come from
https://github.com/<user>.keys via roles/ssh_key_fetch: a download that
fails or holds no key, after a few retries, fails the run there, before
any file is written (no keep-on-outage, no drop-on-404)
set perms

Static keys (`fixpi_static_public_keys`, default `[]`, set in the inventory's
group_vars/all/ssh_keys.yml): a list of full public key lines, source name
`static` in authorized_keys.yml. Like the jump key they go to home/pi only,
unless a file's `key_sources` in `fixpi_authorized_keys_files` lists `static`
(then that file gets them too); root and the ansible account (`[controller]`)
do not. Each line must be "<type> <base64 key> [comment]" or the play fails
before any file is written. verify checks each key is in home/pi and in no
file that does not list `static`. The first use: the label station's key, so
`ssh -J pi@<gateway jump account> pi@10.21.S.P` works with the caller's own key.

A change to any authorized_keys file is a root change, so the booted fleet
reboots at the converge that lands it (nfsroot_generation; see the comment at
the top of tasks/authorized_keys.yml).


### tweeks.yml

remove etc/hostname (makes dhclient use hostname provided by dhcp)

pi etc/hosts

let tio connect to the tty and see pi boot messages
enable /dev/serial0
disable (mask) getty systemd service
 don't let getty mess up boot messages over the serial console
tweeks.yml:    name: serial-getty@ttyAMA0.service

don't resize the root fs
don't manage a swap file

apt remove brltty
brltty greedily grabs serial ports, ftdi_sio loses connection

avoid [FAILED] Failed to start Set console font and keymap.
avoid Wi-Fi is currently blocked by rfkill.
tweeks.yml:# - name: avoid [FAILED] Failed to start Hostname Service.
set pistat_host in /etc/environment
etc overrides
resolve.conf
add time to bash prompt
is server pi
install packages on server
tweeks.yml:    name: "{{ user_name }}"
Git checkout this repo for safer 2 hour onpi



### manage.yml
create scripts dir
install scripts to manage pi states
put the pi boot system into maintenance mode
