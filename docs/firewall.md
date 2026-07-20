# Firewalling the Pi

`lhpc` gates its **own** console (bind + source CIDR + client certificate). It never
edits your firewall, and it cannot gate the stacks — some open ports on **all
interfaces** with no authentication at all. That is what a host firewall is for.

This page uses **ufw**, the friendly front end to the kernel's packet filter. Setting
the baseline below once makes every stack local-only; each scenario then opens exactly
what it needs.

## Contents

- [What actually listens](#what-actually-listens)
- [Baseline — close everything](#baseline--close-everything)
- [Scenario 1: local only](#scenario-1-local-only)
- [Scenario 2: your LAN](#scenario-2-your-lan)
- [Scenario 3: public internet](#scenario-3-public-internet)
- [Scenario 4: Pi WiFi AP + phone](#scenario-4-pi-wifi-ap--phone)
- [Stack web UIs — proxy, don't open](#stack-web-uis--proxy-dont-open)
- [Persistence, IPv6, and undo](#persistence-ipv6-and-undo)

## What actually listens

| Port | Who | Bind | Auth | Controlled by `lhpc`? |
|---|---|---|---|---|
| **4403** | meshtasticd API | **all interfaces** | **none** | **no — no upstream option** |
| **9443** | meshtasticd web UI | **all interfaces** | **none** | **no — no upstream option** |
| 8001 | KISS/TCP TNC | loopback by default | none (source allow-list) | yes — `--bind` |
| 5000 | MeshCore companion | loopback by default | none (source allow-list) | yes — `wifi.allow` |
| 7000 | MeshCom bridge | loopback by default | password | yes — `--bind` |
| 18083 / 12323 | MeshCom QEMU | loopback (hardcoded) | — | already safe |
| 8443 | `lhpc` console (nginx) | loopback until exposed | **mTLS** | yes — `webserver expose` |
| 8444 / 8445 | stack proxies (meshcom / meshtastic) | loopback until exposed | **mTLS** | yes — `webserver proxy` |

The daemon, chat, iGate and voice components use Unix sockets and open no ports.

**meshtastic is the one you cannot fix in `lhpc`.** Its 4403 and 9443 are reachable
from anywhere on your network the moment the stack starts. The dashboard marks them
red rather than pretending otherwise. Only a firewall closes them.

> On kiss and meshcore, `--bind` / `wifi.allow` is a **source allow-list**, not a listen
> address. Widening it really does put the socket on `0.0.0.0` — connections are then
> filtered as they arrive. Useful, but it is not a substitute for a firewall.

## Baseline — close everything

Do this once. It blocks all inbound traffic, including meshtasticd's open ports.

```bash
sudo apt install -y ufw

sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp        # SSH FIRST — skip this and you lock yourself out
sudo ufw enable
sudo ufw status verbose
```

**Nothing stops working.** The stacks talk to each other over Unix sockets and make
outbound connections (APRS-IS, MQTT) freely — only *inbound* traffic is refused. You
can still reach every service from the Pi itself.

Confirm what is left listening:

```bash
ss -ltnp
```

## Scenario 1: local only

You browse the console on the Pi, or reach it over an SSH tunnel from your laptop.

**The baseline is the entire answer** — no further rules. The console stays on
`127.0.0.1:8443`, which is its default.

From a laptop, forward the port over SSH instead of opening anything:

```bash
ssh -N -L 8443:127.0.0.1:8443 pi@raspberrypi.local
```

Then browse `https://127.0.0.1:8443/` on the laptop. Loopback access needs no client
certificate by default.

## Scenario 2: your LAN

Everyone on your home network can reach the console; the internet cannot.

**1 — open the port to your subnet only** (use your real subnet):

```bash
sudo ufw allow from 192.168.0.0/24 to any port 8443 proto tcp
```

**2 — tell `lhpc` to listen off-loopback:**

```bash
lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

**3 — issue a client certificate** (remote access always requires one):

```bash
lhpc webserver cert issue laptop
lhpc webserver cert export laptop ~/laptop.p12
```

`issue` prints a **one-time passphrase — record it now**, it is never stored. Copy the
`.p12` to the device, import it into the browser or OS keychain, then browse
`https://192.168.0.10:8443/` and pick the certificate when prompted.

**4 — verify:**

```bash
lhpc webserver verify
sudo ufw status
```

## Scenario 3: public internet

Reachable from anywhere, protected by mTLS. The risk is real but bounded: an attacker
without a valid client certificate gets a TLS rejection.

**Forward only 8443** in your router — never 4403, 9443, 8001, 5000 or 7000. None of
them have authentication.

```bash
sudo ufw allow 8443/tcp

lhpc webserver expose --cidr 0.0.0.0/0 --confirm-phrase enable-remote-danger
lhpc webserver apply
```

Then issue a certificate per device as in scenario 2. Two things worth doing:

- Set access mode to `auth-everywhere` so even loopback needs a certificate.
- Revoke promptly when a device is lost: `lhpc webserver cert revoke phone --confirm-label phone`.

## Scenario 4: Pi WiFi AP + phone

The Pi is its own WiFi network (see [WiFi access point](wifi-access-point.md)) and your
phone joins it. Nothing else must get in.

**1 — open the AP interface only** (the wired/uplink side stays closed). In shared mode
NetworkManager runs a DHCP + DNS server on `wlan0`; the phone cannot get an address or
resolve names unless you also allow those, so open all three:

```bash
sudo ufw allow in on wlan0 to any port 67 proto udp    # DHCP — hands the phone an address
sudo ufw allow in on wlan0 to any port 53              # DNS  — udp + tcp
sudo ufw allow in on wlan0 to any port 8443 proto tcp  # the console
```

**2 — put the AP address in the server certificate** so the browser's name check
matches. **Order matters — do this before issuing the phone certificate.**

*First install (no PKI yet):* `init` creates the CAs and the server cert together —

```bash
lhpc webserver init --ip 10.42.0.1 --dns loraham.local
```

*Existing install (CAs already present):* **never re-`init`** — `--confirm-recreate`
would replace the CAs and void every certificate you have issued. Add the SANs to the
existing server cert instead:

```bash
lhpc webserver configure --ip 10.42.0.1 --dns loraham.local
lhpc webserver tls-renew
lhpc webserver apply
```

**3 — issue the phone certificate and move it across:**

```bash
lhpc webserver cert issue phone
lhpc webserver cert export phone ~/phone.p12
```

**4 — expose the console to the AP subnet:**

```bash
lhpc webserver expose --cidr 10.42.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

Transfer the `.p12` (USB, or `scp` while still on your LAN — not over the open air),
import it in the phone's settings, then browse **`https://10.42.0.1:8443/`**.

## Stack web UIs — proxy, don't open

meshtasticd's `:9443` and MeshCom's `:18083` have **no authentication**. Do not open
them in the firewall.

Instead put them behind the `lhpc` proxy, which fronts them with the same mTLS and CIDR
gate as the console:

```bash
lhpc webserver proxy meshtastic --mode lan --port 8445 \
     --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply

sudo ufw allow from 192.168.0.0/24 to any port 8445 proto tcp
```

`--port` is required — a stack with no port set is simply not proxied. The web console
suggests **8444** for meshcom and **8445** for meshtastic; any free port above 1023
works. Use `--mode local` to reach the UI only from the Pi, in which case no firewall
rule is needed at all.

The native ports stay firewalled; you reach the UI through the proxy port instead. Only
these two stacks can be proxied — kiss, meshcore and the daemon speak non-HTTP
protocols.

## Persistence, IPv6, and undo

`ufw enable` survives reboot — there is nothing else to install or enable.

Leave `IPV6=yes` in `/etc/default/ufw` (the default). `lhpc` refuses IPv6 *exposure*,
but the stack ports still exist on IPv6, and the baseline should close them too.

Inspect, remove a rule, or turn the firewall off:

```bash
sudo ufw status numbered
sudo ufw delete 3           # by number, from the listing above
sudo ufw disable
```

Optional, unrelated to `lhpc`: a stock Raspberry Pi OS also runs `rpcbind` on `:111`
and often nginx's default site on `:80`. The baseline firewalls both. If you use
neither, `sudo systemctl disable --now rpcbind rpcbind.socket` removes the service too.
