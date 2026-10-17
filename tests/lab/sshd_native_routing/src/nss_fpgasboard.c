/* libnss_fpgasboard.so.2: an NSS source that answers getpwnam() and
 * getspnam() for every name of the form pi-sw<S>-p<P> inside the site's
 * shape, with ONE shared uid. No list of names exists anywhere.
 *
 * Lab prototype (tests/lab/sshd_native_routing). It is loaded into every
 * process that looks up a user, including sshd's root monitor, so it does
 * as little as possible: no allocation, no network, two small files.
 *
 * /etc/fpgas-board/nss.conf   "<uid> <gid> <ports of sw1> <ports of sw2> ..."
 * /etc/fpgas-board/shadow     one line: the password hash (root:shadow 0640)
 *
 * nsswitch.conf:  passwd: files fpgasboard
 *                 shadow: files fpgasboard
 * "files" comes first, so a real account always wins and this module is
 * only asked about names that are not real accounts.
 */
#include <errno.h>
#include <nss.h>
#include <pwd.h>
#include <shadow.h>
#include <stdio.h>
#include <string.h>

#include "board_name.h"

#define CONF "/etc/fpgas-board/nss.conf"
#define HASH_FILE "/etc/fpgas-board/shadow"
#define RELAY "/usr/lib/fpgas-board-relay/relay"

static enum nss_status lookup(const char *name, unsigned *uid, unsigned *gid, int *errnop)
{
	struct board_site site;
	int sw, port;
	FILE *f;

	if (board_parse_name(name, &sw, &port) != 0) {
		*errnop = ENOENT;
		return NSS_STATUS_NOTFOUND;
	}
	f = fopen(CONF, "re");
	if (f == NULL) {
		*errnop = errno;
		return NSS_STATUS_UNAVAIL;
	}
	if (fscanf(f, "%u %u", uid, gid) != 2 || *uid == 0 || *gid == 0 ||
	    board_site_read_ports(f, &site) != 0) {
		fclose(f);
		*errnop = EINVAL;
		return NSS_STATUS_UNAVAIL;
	}
	fclose(f);
	if (!board_in_site(&site, sw, port)) {
		*errnop = ENOENT;
		return NSS_STATUS_NOTFOUND;
	}
	return NSS_STATUS_SUCCESS;
}

enum nss_status _nss_fpgasboard_getpwnam_r(const char *name, struct passwd *pw,
    char *buf, size_t buflen, int *errnop)
{
	unsigned uid, gid;
	size_t len;
	enum nss_status st = lookup(name, &uid, &gid, errnop);

	if (st != NSS_STATUS_SUCCESS)
		return st;
	len = strlen(name) + 1;
	if (buflen < len) {
		*errnop = ERANGE;
		return NSS_STATUS_TRYAGAIN;
	}
	memcpy(buf, name, len);
	pw->pw_name = buf;
	pw->pw_passwd = (char *)"x";
	pw->pw_uid = uid;
	pw->pw_gid = gid;
	pw->pw_gecos = (char *)"fpgas.online board login";
	pw->pw_dir = (char *)"/";
	pw->pw_shell = (char *)RELAY;
	return NSS_STATUS_SUCCESS;
}

enum nss_status _nss_fpgasboard_getspnam_r(const char *name, struct spwd *sp,
    char *buf, size_t buflen, int *errnop)
{
	unsigned uid, gid;
	size_t nlen, hlen;
	FILE *f;
	enum nss_status st = lookup(name, &uid, &gid, errnop);

	if (st != NSS_STATUS_SUCCESS)
		return st;
	nlen = strlen(name) + 1;
	if (buflen < nlen + 2) {
		*errnop = ERANGE;
		return NSS_STATUS_TRYAGAIN;
	}
	memcpy(buf, name, nlen);
	f = fopen(HASH_FILE, "re");
	if (f == NULL) {
		/* Not root (or not in group shadow): no shadow entry. */
		*errnop = errno;
		return NSS_STATUS_UNAVAIL;
	}
	if (fgets(buf + nlen, (int)(buflen - nlen), f) == NULL) {
		fclose(f);
		*errnop = EINVAL;
		return NSS_STATUS_UNAVAIL;
	}
	fclose(f);
	hlen = strlen(buf + nlen);
	if (hlen == 0 || buf[nlen + hlen - 1] != '\n') {
		/* Empty, or the hash did not fit: ask for a bigger buffer. */
		*errnop = ERANGE;
		return NSS_STATUS_TRYAGAIN;
	}
	buf[nlen + hlen - 1] = '\0';
	sp->sp_namp = buf;
	sp->sp_pwdp = buf + nlen;
	sp->sp_lstchg = -1;
	sp->sp_min = -1;
	sp->sp_max = -1;
	sp->sp_warn = -1;
	sp->sp_inact = -1;
	sp->sp_expire = -1;
	sp->sp_flag = ~0UL;
	return NSS_STATUS_SUCCESS;
}
