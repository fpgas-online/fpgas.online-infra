/* Lab-only probe: stands where a compromised relay (or a compromised ssh
 * client inside it) would stand, and reports what it can reach. It applies
 * the relay's own sandbox (sandbox.h) unless --no-landlock is given, then
 * tries things. Never installed in production.
 *
 *   probe [--no-landlock] [--kill PID] [--tcp ADDR:PORT]... [--udp ADDR:PORT]...
 */
#define _GNU_SOURCE
#include <arpa/inet.h>
#include <dirent.h>
#include <netinet/in.h>
#include <poll.h>
#include <sched.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <sys/wait.h>

#include "sandbox.h"

static void result(const char *what, const char *arg, int ok)
{
	printf("%-10s %-28s %s\n", what, arg, ok ? "ALLOWED" : strerror(errno));
}

static void try_read(const char *path)
{
	int fd = open(path, O_RDONLY | O_CLOEXEC);

	result("read", path, fd >= 0);
	if (fd >= 0)
		close(fd);
}

static void try_write(const char *path)
{
	int fd = open(path, O_WRONLY | O_CREAT | O_CLOEXEC, 0600);

	result("write", path, fd >= 0);
	if (fd >= 0) {
		close(fd);
		unlink(path);
	}
}

static void try_list(const char *path)
{
	DIR *d = opendir(path);
	struct dirent *e;
	char buf[256] = "";

	if (d == NULL) {
		result("list", path, 0);
		return;
	}
	while ((e = readdir(d)) != NULL) {
		if (e->d_name[0] == '.')
			continue;
		if (strlen(buf) + strlen(e->d_name) + 2 < sizeof(buf)) {
			strcat(buf, e->d_name);
			strcat(buf, " ");
		}
	}
	closedir(d);
	printf("%-10s %-28s ALLOWED: %s\n", "list", path, buf);
}

static void try_exec(const char *path, const char *a1, const char *a2)
{
	pid_t pid = fork();
	int st;

	if (pid == 0) {
		execl(path, path, a1, a2, (char *)NULL);
		_exit(100 + (errno == ENOENT ? 1 : errno == EACCES ? 2 : 3));
	}
	waitpid(pid, &st, 0);
	if (WIFEXITED(st) && WEXITSTATUS(st) == 0) {
		errno = 0;
		result("exec", path, 1);
	} else {
		errno = WEXITSTATUS(st) == 101 ? ENOENT : WEXITSTATUS(st) == 102 ? EACCES : EIO;
		result("exec", path, 0);
	}
}

static void try_userns(void)
{
	pid_t pid = fork();
	int st;

	if (pid == 0)
		_exit(unshare(CLONE_NEWUSER) == 0 ? 0 : errno == EPERM ? 101 : errno == EACCES ? 102 : 103);
	waitpid(pid, &st, 0);
	errno = WEXITSTATUS(st) == 101 ? EPERM : WEXITSTATUS(st) == 102 ? EACCES : EIO;
	result("userns", "unshare(CLONE_NEWUSER)", WIFEXITED(st) && WEXITSTATUS(st) == 0);
}

static int parse_target(char *arg, struct sockaddr_in *sa)
{
	char *colon = strrchr(arg, ':');

	if (colon == NULL)
		return -1;
	*colon = '\0';
	memset(sa, 0, sizeof(*sa));
	sa->sin_family = AF_INET;
	sa->sin_port = htons((unsigned short)atoi(colon + 1));
	if (inet_pton(AF_INET, arg, &sa->sin_addr) != 1)
		return -1;
	*colon = ':';
	return 0;
}

static void try_tcp(char *arg)
{
	struct sockaddr_in sa;
	struct pollfd pfd;
	int fd, err = 0;
	socklen_t len = sizeof(err);

	if (parse_target(arg, &sa) != 0)
		return;
	fd = socket(AF_INET, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
	if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) != 0 && errno != EINPROGRESS) {
		result("tcp", arg, 0);
		close(fd);
		return;
	}
	pfd.fd = fd;
	pfd.events = POLLOUT;
	if (poll(&pfd, 1, 2000) != 1) {
		errno = ETIMEDOUT;
		result("tcp", arg, 0);
	} else {
		getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &len);
		errno = err;
		result("tcp", arg, err == 0);
	}
	close(fd);
}

static void try_udp(char *arg)
{
	struct sockaddr_in sa;
	int fd;

	if (parse_target(arg, &sa) != 0)
		return;
	fd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, 0);
	result("udp", arg, sendto(fd, "x", 1, 0, (struct sockaddr *)&sa, sizeof(sa)) == 1);
	close(fd);
}

int main(int argc, char **argv)
{
	int i, landlock = 1;

	setvbuf(stdout, NULL, _IOLBF, 0);
	for (i = 1; i < argc; i++)
		if (strcmp(argv[i], "--no-landlock") == 0)
			landlock = 0;
	if (landlock && sandbox_apply() != 0)
		return 1;
	printf("uid=%d landlock=%s\n", (int)getuid(), landlock ? "on" : "off");

	try_list("/");
	try_read("/etc/passwd");
	try_read("/etc/shadow");
	try_read("/etc/ssh/sshd_config");
	try_read("/etc/ssh/ssh_host_ed25519_key");
	try_read("/etc/board-relay/password");
	try_read("/home/alice/.ssh/authorized_keys");
	try_read("/proc/1/cmdline");
	try_write("/tmp/probe-file");
	try_write("/dev/shm/probe-file");
	try_write("/etc/board-relay/probe-file");
	try_exec("/bin/sh", "-c", "exit 0");
	try_exec("/usr/bin/sh", "-c", "exit 0");
	try_exec("/usr/bin/ssh", "-V", NULL);
	try_userns();
	for (i = 1; i < argc - 1; i++) {
		if (strcmp(argv[i], "--tcp") == 0)
			try_tcp(argv[++i]);
		else if (strcmp(argv[i], "--udp") == 0)
			try_udp(argv[++i]);
		else if (strcmp(argv[i], "--kill") == 0) {
			i++;
			result("signal", argv[i], kill((pid_t)atoi(argv[i]), 0) == 0);
		}
	}
	return 0;
}
