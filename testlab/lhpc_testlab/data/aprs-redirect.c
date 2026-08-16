/* Test-lab APRS-IS safety interposer. LD_PRELOADed into the igate process ONLY, it
 * resolves EVERY hostname to 127.0.0.1 so the deprecated igate connects to the lab's
 * local APRS-IS sink instead of the LIVE ham network (euro.aprs2.net). Scoped to that
 * one spawn by the lab's spawn guard; nothing else is affected. */
#define _GNU_SOURCE
#include <netdb.h>
#include <string.h>
#include <arpa/inet.h>
#include <stdlib.h>

static struct in_addr _lo;
static char *_alist[2];
static char *_aliases[1] = { NULL };
static struct hostent _he;

struct hostent *gethostbyname(const char *name) {
    (void)name;
    _lo.s_addr = inet_addr("127.0.0.1");
    _alist[0] = (char *)&_lo;
    _alist[1] = NULL;
    _he.h_name = (char *)"localhost";
    _he.h_aliases = _aliases;
    _he.h_addrtype = AF_INET;
    _he.h_length = sizeof(struct in_addr);
    _he.h_addr_list = _alist;
    return &_he;
}
