# Reaching the box through an SSH tunnel

An SSH tunnel brings the box's loopback-only ports (console and stack UIs) to your machine
without exposing anything; remote exposure stays off. Use SSH keys, not passwords:

```bash
ssh-copy-id lhpc@<host>        # once; then `ssh lhpc@<host>` needs no password
```

`<host>` is the box's hostname (`<hostname>.local` over mDNS on the same network) or address;
`lhpc` is the operator user. The console *without* a tunnel is the [remote exposure
runbook](webserver.md#remote-exposure-runbook); both can coexist.

## Contents

- [The console](#the-console)
- [One tunnel per stack](#one-tunnel-per-stack)
- [Everything at once](#everything-at-once)
- [Notes](#notes)

## The console

```bash
ssh -N -L 8443:127.0.0.1:8443 lhpc@<host>
```

Open `https://127.0.0.1:8443/`. The tunnel arrives as a loopback client, which the default
[access mode](webserver.md#access-modes) serves **without a client certificate** (your SSH login
authenticates); `auth-everywhere` requires one here too. The browser warns because the box's own
server CA signed the certificate. `-N` opens no shell; Ctrl+C ends the tunnel;
`-o ServerAliveInterval=30` keeps a long one alive.

## One tunnel per stack

The local port is the same number as on the box, so the console's own links work through the
tunnel; a proxied page is linked by its proxy port, so forward that too or use the native port
below. meshtasticd listens on all interfaces
([what actually listens](firewall.md#what-actually-listens)); its tunnel rows work whether or not
the firewall blocks it.

| stack | on the box | tunnel | then, on your machine |
|---|---|---|---|
| LHPC console | `127.0.0.1:8443` (HTTPS) | `ssh -N -L 8443:127.0.0.1:8443 lhpc@<host>` | `https://127.0.0.1:8443/` |
| Graywolf APRS | web UI `127.0.0.1:8080` | `ssh -N -L 8080:127.0.0.1:8080 lhpc@<host>` | `http://127.0.0.1:8080/` (admin login: the stack page's Password section) |
| MeshCore web UI (an optional component, started from the stack's card or with `lhpc stack start meshcore-webui`) | `127.0.0.1:8788` | `ssh -N -L 8788:127.0.0.1:8788 lhpc@<host>` | `http://127.0.0.1:8788/` |
| MeshCore repeater dashboard | `127.0.0.1:8000` | `ssh -N -L 8000:127.0.0.1:8000 lhpc@<host>` | `http://127.0.0.1:8000/` (password: the stack page) |
| MeshCore companion port (a `meshcore-cli` on your machine) | `127.0.0.1:5000` | `ssh -N -L 5000:127.0.0.1:5000 lhpc@<host>` | `meshcli -t 127.0.0.1 -p 5000 …` (the packaged client is `meshcli`) |
| MeshCom web UI | `127.0.0.1:18083` | `ssh -N -L 18083:127.0.0.1:18083 lhpc@<host>` | `http://127.0.0.1:18083/` (502 until the firmware has booted) |
| MeshCom net-console (raw TCP) | `127.0.0.1:12323` | `ssh -N -L 12323:127.0.0.1:12323 lhpc@<host>` | `nc 127.0.0.1 12323` (commands end in CRLF) |
| Meshtastic node API (the Meshtastic CLI or app on your machine; the node opens its ports about a minute after the start) | `127.0.0.1:4403` | `ssh -N -L 4403:127.0.0.1:4403 lhpc@<host>` | `meshtastic --host 127.0.0.1 --info` |
| meshtasticd's own web UI | `127.0.0.1:9443` (HTTPS) | `ssh -N -L 9443:127.0.0.1:9443 lhpc@<host>` | `https://127.0.0.1:9443/` |
| KISS TNC (an APRS client on your machine) | `127.0.0.1:8001` | `ssh -N -L 8001:127.0.0.1:8001 lhpc@<host>` | KISS over TCP at `127.0.0.1:8001` |
| Reticulum TCP interface (another RNS node of yours) | `127.0.0.1:4242` | `ssh -N -L 4242:127.0.0.1:4242 lhpc@<host>` | a `TCPClientInterface` to `127.0.0.1:4242` |
| MeshChat (an optional component of the reticulum stack, started from its card or with `lhpc stack start meshchat`) | `127.0.0.1:8790` | `ssh -N -L 8790:127.0.0.1:8790 lhpc@<host>` | `http://127.0.0.1:8790/` |

The daemon, chat and voice have no TCP port: run chat and the voice terminal in an SSH session
on the box (`ssh -t lhpc@<host>` and the command shown on the Dashboard).

## Everything at once

One tunnel can carry every port:

```bash
ssh -N -o ServerAliveInterval=30 \
  -L 8443:127.0.0.1:8443 -L 8080:127.0.0.1:8080 -L 8788:127.0.0.1:8788 -L 8000:127.0.0.1:8000 \
  -L 5000:127.0.0.1:5000 -L 18083:127.0.0.1:18083 -L 12323:127.0.0.1:12323 \
  -L 4403:127.0.0.1:4403 -L 9443:127.0.0.1:9443 -L 8001:127.0.0.1:8001 -L 4242:127.0.0.1:4242 \
  -L 8790:127.0.0.1:8790 \
  lhpc@<host>
```

A port whose stack is not running is refused on the box side (`channel … open failed`); the
other forwards keep working.

## Notes

- The stack web-UI proxies are for the mTLS path; through a tunnel you reach each UI on its own
  port, proxied or not.
