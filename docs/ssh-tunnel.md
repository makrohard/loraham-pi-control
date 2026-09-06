# Reaching the box through an SSH tunnel

When SSH (port 22) is the only thing open on the box and you do **not** want to expose the console
or any stack UI to the network, an SSH tunnel brings each local-only port to your own machine.
Nothing on the box changes: the console stays on `127.0.0.1:8443`, every stack UI stays on its
loopback port, and remote exposure stays off. The tunnel arrives on the box as a loopback client,
which the console serves **without a client certificate by design** (see
[Default behaviour](webserver.md#default-behaviour-local-https-no-client-cert)) — so **your SSH
login is the authentication**. Use SSH keys, not passwords:

```bash
ssh-copy-id lhpc@lhpc-e293.local        # once; then `ssh lhpc@lhpc-e293.local` needs no password
```

If you later want the console reachable *without* a tunnel, that is the mTLS path in
[Remote access to the web console](webserver.md#remote-access-to-the-web-console): issue a client
certificate per device, install it in the browser, and expose with an authenticated access mode.
A tunnel and mTLS coexist; the tunnel simply never needs the certificate.

## The console

```bash
ssh -N -L 8443:127.0.0.1:8443 lhpc@lhpc-e293.local
```

Then open `https://127.0.0.1:8443/` on your machine. The certificate warning is the box's
self-signed server certificate — expected on loopback, see [webserver](webserver.md). `-N` opens no
shell; end the tunnel with Ctrl+C. Add `-o ServerAliveInterval=30` for a tunnel that lives for
hours.

## One tunnel per stack

Every stack UI binds to loopback on the box; the local port on your side is the same number, so the
URLs in the console's own pages keep working once the tunnel is up.

| stack | on the box | tunnel | then, on your machine |
|---|---|---|---|
| Graywolf APRS | web UI `127.0.0.1:8080` | `ssh -N -L 8080:127.0.0.1:8080 lhpc@lhpc-e293.local` | `http://127.0.0.1:8080/` (admin login: the stack page's Password section) |
| MeshCore web UI (an optional component — start it from the stack's card or with `lhpc stack start meshcore-webui`) | `127.0.0.1:8788` | `ssh -N -L 8788:127.0.0.1:8788 lhpc@lhpc-e293.local` | `http://127.0.0.1:8788/` |
| MeshCore repeater dashboard | `127.0.0.1:8000` | `ssh -N -L 8000:127.0.0.1:8000 lhpc@lhpc-e293.local` | `http://127.0.0.1:8000/` (password: the stack page) |
| MeshCore companion port (a `meshcore-cli` on your machine) | `127.0.0.1:5000` | `ssh -N -L 5000:127.0.0.1:5000 lhpc@lhpc-e293.local` | `meshcli -t 127.0.0.1 -p 5000 …` (the packaged client is `meshcli`) |
| MeshCom web UI | `127.0.0.1:18083` | `ssh -N -L 18083:127.0.0.1:18083 lhpc@lhpc-e293.local` | `http://127.0.0.1:18083/` (502 until the firmware has booted) |
| MeshCom net-console (raw TCP) | `127.0.0.1:12323` | `ssh -N -L 12323:127.0.0.1:12323 lhpc@lhpc-e293.local` | `nc 127.0.0.1 12323` (commands end in CRLF) |
| Meshtastic node API (the Meshtastic CLI or app on your machine; the node opens its ports about a minute after the start) | `127.0.0.1:4403` | `ssh -N -L 4403:127.0.0.1:4403 lhpc@lhpc-e293.local` | `meshtastic --host 127.0.0.1 --info` |
| meshtasticd's own web UI | `127.0.0.1:9443` (HTTPS) | `ssh -N -L 9443:127.0.0.1:9443 lhpc@lhpc-e293.local` | `https://127.0.0.1:9443/` |
| KISS TNC (an APRS client on your machine) | `127.0.0.1:8001` | `ssh -N -L 8001:127.0.0.1:8001 lhpc@lhpc-e293.local` | KISS over TCP at `127.0.0.1:8001` |
| Reticulum TCP interface (another RNS node of yours) | `127.0.0.1:4242` | `ssh -N -L 4242:127.0.0.1:4242 lhpc@lhpc-e293.local` | a `TCPClientInterface` to `127.0.0.1:4242` |

The daemon, chat and voice have no TCP port: chat and the voice terminal are run in an SSH
session on the box itself (`ssh -t lhpc@lhpc-e293.local` and the command shown on the Dashboard).

## Everything at once

One tunnel can carry every port; use it when you work with several stacks:

```bash
ssh -N -o ServerAliveInterval=30 \
  -L 8443:127.0.0.1:8443 -L 8080:127.0.0.1:8080 -L 8788:127.0.0.1:8788 -L 8000:127.0.0.1:8000 \
  -L 5000:127.0.0.1:5000 -L 18083:127.0.0.1:18083 -L 12323:127.0.0.1:12323 \
  -L 4403:127.0.0.1:4403 -L 9443:127.0.0.1:9443 -L 8001:127.0.0.1:8001 -L 4242:127.0.0.1:4242 \
  lhpc@lhpc-e293.local
```

A port whose stack is not running is simply refused on the box side (`channel … open failed`);
the other forwards keep working.

## Notes

- Replace `lhpc-e293.local` with the box's hostname or address; `lhpc` is the operator user that
  runs lhpc. mDNS (`.local`) needs the box and your machine on the same network — over a longer
  path use the IP.
- The console's proxies for the stack UIs (the Webserver page) are for the mTLS path; through a
  tunnel you reach each UI directly on its own port, proxy or not.
- Only ports bound to `127.0.0.1` need a tunnel. meshtasticd's `4403`/`9443` bind to all
  interfaces unless the managed firewall closes them — see [Firewalling the Pi](firewall.md).
