/* The board relay: the login shell AND the ForceCommand of every board
 * login name on the gateway. It never runs a shell and never interprets
 * anything the visitor sent. It
 *
 *   1. takes the board from $USER, which sshd sets from the account it
 *      authenticated (never from the client), and accepts only
 *      pi-sw<S>-p<P> inside the site's shape;
 *   2. computes the board's address from the name by the site formula;
 *   3. drops its environment, sandboxes itself (sandbox.h);
 *   4. execs the OpenSSH client with a fixed argument list to that one
 *      address, handing the visitor's requested command to the BOARD as one
 *      opaque argument after "--".
 *
 * Installed twice: as .../relay and as .../askpass (same binary). Under the
 * second name it is the ssh client's SSH_ASKPASS helper and prints the
 * boards' public password.
 *
 * Lab prototype (tests/lab/sshd_native_routing). Static binary, no NSS.
 */
#define _GNU_SOURCE
#include <signal.h>
#include <stdlib.h>

#include "board_name.h"
#include "sandbox.h"

#define CONF_DIR "/etc/board-relay"
#define SITE_CONF CONF_DIR "/site.conf" /* "<a>.<b> <ports of sw1> <ports of sw2> ..." */
#define SSH_CONFIG CONF_DIR "/ssh_config"
#define PASSWORD CONF_DIR "/password"
#define SELF_DIR "/usr/lib/fpgas-board-relay"
#define SSH "/usr/bin/ssh"
/* The value of `ForceCommand` and of `Subsystem sftp` in the Match block. */
#define FORCED "relay"
#define SFTP_MARKER "fpgas-board-sftp"

static int askpass(int argc, char **argv)
{
	char buf[256];
	FILE *f;

	/* Only ever answer a password prompt; never a yes/no question. */
	if (argc < 2 || strstr(argv[1], "assword") == NULL)
		return 1;
	f = fopen(PASSWORD, "re");
	if (f == NULL || fgets(buf, sizeof(buf), f) == NULL)
		return 1;
	fputs(buf, stdout);
	return 0;
}

static int term_ok(const char *t)
{
	size_t n = strlen(t);

	if (n < 1 || n > 64)
		return 0;
	return strspn(t, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-") == n;
}

int main(int argc, char **argv)
{
	const char *base = strrchr(argv[0], '/');
	const char *user, *logname, *cmd, *term;
	char termbuf[80], addr[32];
	struct board_site site;
	int a, b, sw, port, tty, n = 0;
	char *av[16];
	FILE *f;

	base = base ? base + 1 : argv[0];
	if (strcmp(base, "askpass") == 0)
		return askpass(argc, argv);

	/* sshd runs `<shell> -c <ForceCommand>`. Any other invocation means
	 * the Match block is not in force: refuse rather than guess. */
	if (argc != 3 || strcmp(argv[1], "-c") != 0 || strcmp(argv[2], FORCED) != 0) {
		fprintf(stderr, "board relay: not started by sshd's ForceCommand; refusing\n");
		return 126;
	}

	user = getenv("USER");
	logname = getenv("LOGNAME");
	if (user == NULL || logname == NULL || strcmp(user, logname) != 0 ||
	    board_parse_name(user, &sw, &port) != 0) {
		fprintf(stderr, "board relay: this login name is not a board\n");
		return 126;
	}
	f = fopen(SITE_CONF, "re");
	if (f == NULL || fscanf(f, "%d.%d", &a, &b) != 2 || a < 1 || a > 255 || b < 0 || b > 255 ||
	    board_site_read_ports(f, &site) != 0) {
		fprintf(stderr, "board relay: cannot read " SITE_CONF "\n");
		return 126;
	}
	fclose(f);
	if (!board_in_site(&site, sw, port)) {
		fprintf(stderr, "board relay: %s is not a port of this site\n", user);
		return 126;
	}
	snprintf(addr, sizeof(addr), "%d.%d.%d.%d", a, b, sw, port);

	/* Everything taken from the session before the environment is dropped. */
	cmd = getenv("SSH_ORIGINAL_COMMAND");
	if (cmd != NULL && (cmd = strdup(cmd)) == NULL)
		return 126;
	term = getenv("TERM");
	snprintf(termbuf, sizeof(termbuf), "%s", term != NULL && term_ok(term) ? term : "dumb");
	tty = isatty(STDIN_FILENO);

	if (sandbox_apply() != 0)
		return 126;
	/* Die with the sshd session. Without a terminal nothing else tells the
	 * ssh client that the visitor has gone, and it would stay, holding a
	 * process and a connection to the board, until the board's command
	 * ended. The setting survives the exec below. */
	if (prctl(PR_SET_PDEATHSIG, SIGHUP) != 0 || getppid() == 1) {
		fprintf(stderr, "board relay: the session is gone\n");
		return 126;
	}

	clearenv();
	setenv("PATH", "/usr/bin", 1);
	setenv("HOME", "/", 1);
	setenv("TERM", termbuf, 1);
	if (access(PASSWORD, R_OK) == 0) {
		/* The boards' password is public; the gateway already checked
		 * what the visitor typed. Answer the board's prompt with it. */
		setenv("SSH_ASKPASS", SELF_DIR "/askpass", 1);
		setenv("SSH_ASKPASS_REQUIRE", "force", 1);
	}

	av[n++] = "ssh";
	av[n++] = "-F";
	av[n++] = SSH_CONFIG;
	av[n++] = tty ? "-tt" : "-T";
	if (cmd != NULL && strcmp(cmd, SFTP_MARKER) == 0) {
		av[n - 1] = "-T";
		av[n++] = "-s";
		av[n++] = "--";
		av[n++] = addr;
		av[n++] = "sftp";
	} else {
		/* "--" ends ssh's option parsing for good: whatever the visitor
		 * asked for is one argument that only the board's shell reads. */
		av[n++] = "--";
		av[n++] = addr;
		if (cmd != NULL)
			av[n++] = (char *)cmd;
	}
	av[n] = NULL;
	execv(SSH, av);
	fprintf(stderr, "board relay: exec " SSH ": %s\n", strerror(errno));
	return 126;
}
