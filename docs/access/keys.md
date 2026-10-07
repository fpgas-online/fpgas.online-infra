# Where the keys come from

**You want to know where the servers get people's public keys, and what a converge does when GitHub is down.**

[`roles/ssh_key_fetch`](../../ansible/roles/ssh_key_fetch) is the one place the
server downloads keys. `operators`, `jump`, `server_user` (and its verify) and
`fixpi` all use it (#161). It reads `https://github.com/<user>.keys` for a
`gh:<user>` id and never the rate-limited GitHub API. It reads Launchpad only
for `lp:` or bare ids, and none are fetched now. `ssh-import-id` is no longer
used.

Each download is retried `ssh_key_fetch_retries` (3) times,
`ssh_key_fetch_delay` (5) seconds apart, until it gets a 200 holding at least
one key line. After that **the play fails at the download**, naming the
account, the id, the URL and the answer. There is no fallback. So if GitHub is
down or returns nothing:

- the converge stops at the first role that needs keys, and no
  `authorized_keys` is written empty or from stale data;
- `server_user` fails before its exclusive write, so `admin` keeps its current
  keys;
- for the Pi root it stops before the update lock is taken and before the image
  is extracted, so the fleet and its root are left as they were.

Re-run once GitHub answers again.
