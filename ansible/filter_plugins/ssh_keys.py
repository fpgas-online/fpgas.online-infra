"""Pick the parts out of an authorized_keys line: options, key type, key, comment.

A line is `[options] key-type base64 [comment]`. Reading the base64 as
`line.split()[1]` is only right for a line without options; with
`restrict,port-forwarding,permitopen="10.21.2.48:22"` in front it is the key
TYPE. These filters find the key type first, as ansible.posix.authorized_key
does (whitespace-split, first field that is a known key type), so the roles
that dedupe, label, verify or revoke by key agree with the module on which
field is the key. Like the module, they cannot read an option value that
holds whitespace.
"""

from ansible.errors import AnsibleFilterError

# The key types ansible.posix.authorized_key's parsekey knows.
KEY_TYPES = frozenset((
    "sk-ecdsa-sha2-nistp256@openssh.com",
    "sk-ecdsa-sha2-nistp256-cert-v01@openssh.com",
    "webauthn-sk-ecdsa-sha2-nistp256@openssh.com",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp256-cert-v01@openssh.com",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp384-cert-v01@openssh.com",
    "ecdsa-sha2-nistp521",
    "ecdsa-sha2-nistp521-cert-v01@openssh.com",
    "sk-ssh-ed25519@openssh.com",
    "sk-ssh-ed25519-cert-v01@openssh.com",
    "ssh-ed25519",
    "ssh-ed25519-cert-v01@openssh.com",
    "ssh-dss",
    "ssh-rsa",
    "ssh-xmss@openssh.com",
    "ssh-xmss-cert-v01@openssh.com",
    "rsa-sha2-256",
    "rsa-sha2-512",
    "ssh-rsa-cert-v01@openssh.com",
    "rsa-sha2-256-cert-v01@openssh.com",
    "rsa-sha2-512-cert-v01@openssh.com",
    "ssh-dss-cert-v01@openssh.com",
))


def _split(line):
    """(fields, index of the key type); fails loud on a line without a key."""
    fields = str(line).split()
    for i, field in enumerate(fields):
        if field in KEY_TYPES:
            if i + 1 >= len(fields):
                raise AnsibleFilterError(f"ssh key line has a key type but no key: {line!r}")
            return fields, i
    raise AnsibleFilterError(f"no ssh key type in the line: {line!r}")


def ssh_key_blob(line):
    """The base64 key field, whatever options precede it."""
    fields, i = _split(line)
    return fields[i + 1]


def ssh_key_options(line):
    """The options prefix as written ('' for a line without one)."""
    fields, i = _split(line)
    return " ".join(fields[:i])


def ssh_key_comment(line):
    """Everything after the key field."""
    fields, i = _split(line)
    return " ".join(fields[i + 2:])


class FilterModule:
    def filters(self):
        return {
            "ssh_key_blob": ssh_key_blob,
            "ssh_key_options": ssh_key_options,
            "ssh_key_comment": ssh_key_comment,
        }
