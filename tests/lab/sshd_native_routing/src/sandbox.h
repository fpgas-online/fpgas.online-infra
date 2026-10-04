/* The relay's self-applied sandbox: no_new_privs, resource limits and a
 * Landlock domain. Everything here is available to an unprivileged process
 * on Debian trixie's kernel (Landlock ABI 6) with no setuid helper and no
 * user namespace. It is inherited by the ssh client the relay execs and by
 * anything that client might be tricked into running.
 *
 * Lab prototype (tests/lab/sshd_native_routing).
 */
#ifndef SANDBOX_H
#define SANDBOX_H

#include <errno.h>
#include <fcntl.h>
#include <linux/landlock.h>
#include <stdio.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

#define SANDBOX_MIN_ABI 6

#define SB_FILE_READ (LANDLOCK_ACCESS_FS_READ_FILE)
#define SB_FILE_RX (LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_EXECUTE)
#define SB_DIR_READ (LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR)
#define SB_DIR_RX (SB_DIR_READ | LANDLOCK_ACCESS_FS_EXECUTE)
#define SB_FILE_RW (LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_WRITE_FILE)

#define SB_FS_ALL \
	(LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_WRITE_FILE | \
	 LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_READ_DIR | \
	 LANDLOCK_ACCESS_FS_REMOVE_DIR | LANDLOCK_ACCESS_FS_REMOVE_FILE | \
	 LANDLOCK_ACCESS_FS_MAKE_CHAR | LANDLOCK_ACCESS_FS_MAKE_DIR | \
	 LANDLOCK_ACCESS_FS_MAKE_REG | LANDLOCK_ACCESS_FS_MAKE_SOCK | \
	 LANDLOCK_ACCESS_FS_MAKE_FIFO | LANDLOCK_ACCESS_FS_MAKE_BLOCK | \
	 LANDLOCK_ACCESS_FS_MAKE_SYM | LANDLOCK_ACCESS_FS_REFER | \
	 LANDLOCK_ACCESS_FS_TRUNCATE | LANDLOCK_ACCESS_FS_IOCTL_DEV)

/* What the relay and the ssh client it execs may touch. `required` paths
 * must exist; the others are allowed when present (they exist only when
 * the relay runs outside sshd's ChrootDirectory). */
static const struct sb_path {
	const char *path;
	__u64 access;
	int required;
} sb_paths[] = {
	{ "/usr", SB_DIR_RX, 1 },		 /* ssh, its libraries, the relay */
	{ "/etc/board-relay", SB_DIR_READ, 1 },	 /* site.conf ssh_config known_hosts password */
	{ "/etc/passwd", SB_FILE_READ, 1 },	 /* ssh needs getpwuid() to succeed */
	{ "/dev/null", SB_FILE_RW, 1 },
	{ "/etc/ld.so.cache", SB_FILE_READ, 0 },
	{ "/etc/nsswitch.conf", SB_FILE_READ, 0 },
};

static int sb_fail(const char *what)
{
	fprintf(stderr, "board relay: sandbox: %s: %s\n", what, strerror(errno));
	return -1;
}

static int sandbox_limits(void)
{
	static const struct {
		int res;
		rlim_t val;
	} lim[] = {
		{ RLIMIT_CORE, 0 },    /* no core files */
		{ RLIMIT_FSIZE, 0 },   /* cannot grow any file */
		{ RLIMIT_NOFILE, 64 },
		{ RLIMIT_AS, 512UL << 20 },
	};
	size_t i;

	for (i = 0; i < sizeof(lim) / sizeof(lim[0]); i++) {
		struct rlimit r = { lim[i].val, lim[i].val };

		if (setrlimit(lim[i].res, &r) != 0)
			return sb_fail("setrlimit");
	}
	return 0;
}

static int sandbox_landlock(void)
{
	struct landlock_ruleset_attr attr = {
		.handled_access_fs = SB_FS_ALL,
		.handled_access_net = LANDLOCK_ACCESS_NET_BIND_TCP | LANDLOCK_ACCESS_NET_CONNECT_TCP,
		.scoped = LANDLOCK_SCOPE_ABSTRACT_UNIX_SOCKET | LANDLOCK_SCOPE_SIGNAL,
	};
	struct landlock_net_port_attr ssh_port = {
		.allowed_access = LANDLOCK_ACCESS_NET_CONNECT_TCP,
		.port = 22,
	};
	long abi;
	int fd;
	size_t i;

	abi = syscall(SYS_landlock_create_ruleset, NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
	if (abi < 0)
		return sb_fail("Landlock is not available");
	if (abi < SANDBOX_MIN_ABI) {
		fprintf(stderr, "board relay: sandbox: Landlock ABI %ld, need %d\n", abi, SANDBOX_MIN_ABI);
		return -1;
	}
	fd = (int)syscall(SYS_landlock_create_ruleset, &attr, sizeof(attr), 0);
	if (fd < 0)
		return sb_fail("landlock_create_ruleset");
	for (i = 0; i < sizeof(sb_paths) / sizeof(sb_paths[0]); i++) {
		struct landlock_path_beneath_attr pb = { .allowed_access = sb_paths[i].access };

		pb.parent_fd = open(sb_paths[i].path, O_PATH | O_CLOEXEC);
		if (pb.parent_fd < 0) {
			if (!sb_paths[i].required && errno == ENOENT)
				continue;
			return sb_fail(sb_paths[i].path);
		}
		if (syscall(SYS_landlock_add_rule, fd, LANDLOCK_RULE_PATH_BENEATH, &pb, 0) != 0)
			return sb_fail(sb_paths[i].path);
		close(pb.parent_fd);
	}
	if (syscall(SYS_landlock_add_rule, fd, LANDLOCK_RULE_NET_PORT, &ssh_port, 0) != 0)
		return sb_fail("landlock net rule");
	if (syscall(SYS_landlock_restrict_self, fd, 0) != 0)
		return sb_fail("landlock_restrict_self");
	close(fd);
	return 0;
}

/* Fail closed: the caller exits when this returns non-zero. */
static int sandbox_apply(void)
{
	umask(077);
	if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0)
		return sb_fail("no_new_privs");
	if (sandbox_limits() != 0)
		return -1;
	return sandbox_landlock();
}

#endif
