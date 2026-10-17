/* Board login names: "pi-sw<S>-p<P>".
 *
 * Shared by the NSS module and the relay so that both accept exactly the
 * same strings. Lab prototype (tests/lab/sshd_native_routing).
 */
#ifndef BOARD_NAME_H
#define BOARD_NAME_H

#include <stdio.h>
#include <string.h>

#define BOARD_MAX_SWITCHES 16

/* One decimal number of 1 to 3 digits, no leading zero, no sign. */
static int board_parse_num(const char **p, int *out)
{
	const char *s = *p;
	int n = 0, digits = 0;

	if (*s < '1' || *s > '9')
		return -1;
	while (*s >= '0' && *s <= '9') {
		if (++digits > 3)
			return -1;
		n = n * 10 + (*s - '0');
		s++;
	}
	*out = n;
	*p = s;
	return 0;
}

/* 0 when name is exactly pi-sw<S>-p<P>; nothing before, nothing after. */
static int board_parse_name(const char *name, int *sw, int *port)
{
	const char *p;

	if (name == NULL || strncmp(name, "pi-sw", 5) != 0)
		return -1;
	p = name + 5;
	if (board_parse_num(&p, sw) != 0)
		return -1;
	if (p[0] != '-' || p[1] != 'p')
		return -1;
	p += 2;
	if (board_parse_num(&p, port) != 0)
		return -1;
	return *p == '\0' ? 0 : -1;
}

/* The site's shape: how many access ports each switch has. Switch numbers
 * start at 1. A (switch, port) pair outside it is not a board. */
struct board_site {
	int nswitches;
	int ports[BOARD_MAX_SWITCHES];
};

static int board_site_read_ports(FILE *f, struct board_site *site)
{
	int n;

	site->nswitches = 0;
	while (site->nswitches < BOARD_MAX_SWITCHES && fscanf(f, "%d", &n) == 1) {
		if (n < 1 || n > 254)
			return -1;
		site->ports[site->nswitches++] = n;
	}
	return site->nswitches > 0 ? 0 : -1;
}

static int board_in_site(const struct board_site *site, int sw, int port)
{
	return sw >= 1 && sw <= site->nswitches && port >= 1 &&
	    port <= site->ports[sw - 1];
}

#endif
