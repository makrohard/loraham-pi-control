# Production webserver (HTTPS + mTLS)

```
Browser → HTTPS on <bind>:8443 → Nginx (TLS boundary, mTLS, source-CIDR gate)
        → Waitress over a protected Unix socket → LHPC Flask app
```

What listens and what does not is the [serving model](deployment.md#serving-model); before nginx
is up, use the CLI or a bare `lhpc web`. Desired configuration lives in
`config/local.toml [webserver]`; the Monitor view renders only the cached, proven evidence in
`state/webserver.json` and never probes the network during a page load. Operating the console
itself: [operations](operations.md).

## Contents

- [Default behaviour](#default-behaviour)
- [First-time bootstrap](#first-time-bootstrap)
- [Access modes](#access-modes)
- [Remote exposure runbook](#remote-exposure-runbook)
- [Stack web-UI proxies](#stack-web-ui-proxies)
- [Certificates and the two-CA PKI](#certificates-and-the-two-ca-pki)
- [Verifying effective state](#verifying-effective-state)
- [Applying changes and recovery](#applying-changes-and-recovery)
- [Local dependencies](#local-dependencies)

## Default behaviour

`bind = 127.0.0.1`, `port = 8443`, HTTPS, loopback clients need no client certificate, remote
exposure off. Change the port with `lhpc webserver configure --port <n>` (accepted `1–65535`;
the rootless nginx can bind only `≥ 1024`). This page uses `8443`.

## First-time bootstrap

`install.sh` does this. By hand, from an interactive operator shell (not the web process):

```
sudo apt install -y nginx
sudo systemctl disable --now nginx.service        # keep the package, disable the ROOT service
lhpc webserver init --dns pi.local --ip 192.168.0.10   # two CAs + server cert; SANs are persisted
lhpc webserver start-service                      # generate + validate config, enable + start nginx
```

The Debian package starts a root `nginx.service`; `bootstrap-deps.sh` disables it (LHPC uses the
binary only, through the rootless `lhpc-nginx` user unit). `start-service` is the
**only** path that starts nginx (it uses `systemctl --user` and refuses to run from a managed
unit). The console is then at `https://127.0.0.1:8443/`; until then, `lhpc web` serves it at
`http://127.0.0.1:8770/`.

## Access modes

Authentication is **browser client certificate (mTLS) only**: no accounts, passwords or roles.
Every valid, unrevoked client certificate (a named device credential) has full access.

| Mode | Loopback | Remote |
|------|----------|--------|
| `local-open-remote-auth` (default) | open (no cert) | requires a valid client cert |
| `auth-everywhere` | requires a client cert | requires a client cert |
| `no-auth` | open | open (**dangerous**) |

Decisions use the real TCP peer (`$remote_addr`); nginx blanks client-supplied
`X-Forwarded-For` / `Forwarded` and overwrites `X-LHPC-Peer` / `X-LHPC-Client-Verify` with its own
values.

Remote exposure needs a bind of `0.0.0.0`, at least one allowed source CIDR and a confirm
phrase: `enable-remote` for a private range with a cert-requiring mode, `enable-remote-danger`
for a public range (`0.0.0.0/0`) or `no-auth` (the Monitor then shows a red warning
for `no-auth`, a yellow one for a public range). IPv6 CIDRs are rejected; `::1` counts for local access only.

With the managed firewall in use, exposure (`apply` and the boot-time bind) is gated on its live
receipt ([the three status dimensions](firewall.md#the-three-status-dimensions-and-why-green-is-strict)).

## Remote exposure runbook

Reach the console from another machine with a client certificate. Run every `lhpc` command from
an operator shell on the Pi. Replace `192.168.0.0/24` with your LAN range and `192.168.0.10`
with the Pi's LAN address. `10.42.0.1` / `10.42.0.0/24` is the box's own
[access point](wifi-access-point.md): where it exists, include it, because it is the way back
into a box that has lost its known networks and without it the console answers `403` there.
Flags: [CLI](cli.md#webserver).

1. **Name every address in the server certificate.** `configure` REPLACES each list, so repeat
   the loopback entries; then re-issue the server certificate under the unchanged CAs:
   ```
   lhpc webserver configure --dns localhost --dns pi.local \
                            --ip 127.0.0.1 --ip 192.168.0.10 --ip 10.42.0.1
   lhpc webserver tls-renew
   ```
   Adding an address later needs **both lines**: `lhpc webserver apply` never re-issues the
   certificate (the console's Apply does only to add the box's own LAN address), so a SAN without
   `tls-renew` is saved but not served (the browser reports a name mismatch while status reports
   the config as applied). Installed client credentials keep working; only `init` recreates the
   CAs. **Never re-run `init` on a box with a PKI**: it voids every client certificate.
2. **Turn on remote access.** `--cidr` is repeatable and REPLACES the allowed list; the default
   access mode already requires a client cert off-loopback:
   ```
   lhpc webserver expose --cidr 192.168.0.0/24 --cidr 10.42.0.0/24 --confirm-phrase enable-remote
   lhpc webserver apply
   ```
   The bind change makes `apply` restart nginx ([applying changes](#applying-changes-and-recovery)).
3. **Issue a device certificate** and write its bundle to a file:
   ```
   lhpc webserver cert issue lhpc-laptop                       # prints a ONE-TIME passphrase; record it
   lhpc webserver cert export lhpc-laptop ~/lhpc-laptop.p12    # encrypted .p12, mode 0600
   ```
   The label is what the device's certificate chooser shows; prefix it with `lhpc-`. The
   console's **Webserver → Certificates** panel offers **Download .p12** on a loopback session
   only.
4. **Copy the bundle and the server CA to the remote machine**, one file per `scp` (the panel
   shows equivalent commands with the box's current address, fetching the `.p12` straight from
   `config/tls/exports/`; `ca.crt` is also a plain download there,
   the path phones use):
   ```
   scp <user>@<host>:lhpc-laptop.p12 .
   scp <user>@<host>:loraham-pi-control/config/tls/server-ca/ca.crt .
   ```
   **Never** `scp host:{a,b}`: the last argument is the destination, so the first file
   overwrites your CA certificate. A good `ca.crt` is a few hundred bytes starting with
   `-----BEGIN CERTIFICATE-----`.
5. **Import both in the remote browser** (the CA clears the trust warning, the `.p12` is the
   credential and asks for the passphrase):
   [per-platform steps](#install-the-client-certificate-in-a-browser).
6. **Firewall.** On a box with the access point, enable its AP rules first
   ([scenarios](firewall.md#scenarios)); then apply the
   [managed firewall](firewall.md#the-managed-firewall-one-command), or open `8443` in your own.
7. **Prove it:** `lhpc webserver verify`, browse to `https://192.168.0.10:8443/` from the remote
   machine and pick the `lhpc-laptop` certificate. Then delete the bundle on the Pi:
   `lhpc webserver cert discard-export lhpc-laptop && rm ~/lhpc-laptop.p12` (the certificate stays
   valid).

Back to loopback: `lhpc webserver disable-remote && lhpc webserver apply`, then `verify`.

**Public, no client authentication** (a trusted test rig or LAN only): `lhpc webserver expose
--cidr 0.0.0.0/0 --access-mode no-auth --confirm-phrase enable-remote-danger`, then `apply` and
`verify`. Anyone who can route to the host then reaches `https://<host-ip>:8443/` (the browser
warns about the box's own server CA).

## Stack web-UI proxies

Stack web UIs range from loopback with a login (graywolf) to all interfaces with none
(meshtasticd `:9443`) — [what actually listens](firewall.md#what-actually-listens). `lhpc`
fronts each with its own nginx listener carrying the console's mTLS + source-CIDR gate; keep the
native port firewalled and use the proxy port:

```
lhpc webserver proxy meshtastic --mode lan --port 8447 --access-mode local-open-remote-auth \
     --cidr 192.168.0.0/24 --cidr 10.42.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

- `--mode`: `local` (loopback only), `lan` (only `--cidr` ranges pass) or `public`
  (like `lan`; `--cidr 0.0.0.0/0` admits everyone). Non-`local` needs `--confirm-phrase enable-remote`;
  a public range, `no-auth` or an `http` `--scheme` need `enable-remote-danger`.
- `--port`: `0` = not proxied (omitted: the saved port is kept). The console suggests console port + 1 + the page's
  position (first pages sorted by id, then further pages; fresh box: graywolf `8444`, meshcom
  `8445`, meshcore `8446`, meshtastic `8447`, reticulum `8448`), skipping ports already saved;
  any free port ≥ 1024 works.
- `--access-mode` (alias `--auth`) takes the console's values and the same client certificates.
  Pass it explicitly: a page whose stored policy is `no-auth` otherwise refuses with *elevated
  confirmation required*.
- Pages come from the manifests: every component with a client http/https web endpoint is a page
  with its own port, policy and listener. A stack's first page is addressed by the stack id,
  further pages by `<stack>-<component>`. Today: graywolf, meshcom, meshtastic, reticulum
  (MeshChat) and meshcore (`meshcore` = MeshCore Web UI, `meshcore-meshcore-node` = openHop
  repeater dashboard); daemon, kiss, chat and voice have none. A new page is configured only when
  you save its panel or the bulk form.

**Webserver → Stacks WebGUIs** applies one policy (access, scheme, access mode, CIDRs) to
**every** page in one confirmed action (**LHPC WebGUI** is the console's own). Existing ports
are kept; a page without one gets its suggested default. The whole set is validated first, and
any problem (confirmation, CIDRs, http + cert-auth, a port that cannot be assigned) refuses the
whole action. Activation is one apply behind the firewall gate. Per-stack panels still take
individual exceptions; a later bulk Apply overwrites the shared fields again.

## Certificates and the two-CA PKI

Two independent CAs (private keys under `config/tls/`, 0600; `lhpc secrets backup` copies them
into its file):

- **Server TLS CA** signs the HTTPS server certificate (DNS + IP SANs; `0.0.0.0` is never a
  SAN). `tls-renew` stays under the same CA.
- **Client-auth CA** signs device certificates and the CRL.

Same CAs on a second box (one device certificate for both): `lhpc secrets backup` on the first,
`lhpc secrets restore <file> --only pki` on the second ([cli](cli.md#secrets)).

```
lhpc webserver init --dns pi.local --ip 192.168.0.10     # once; --confirm-recreate to redo
lhpc webserver cert issue lhpc-laptop                    # one-time .p12 passphrase
lhpc webserver cert reissue lhpc-laptop                  # rotate + new passphrase
lhpc webserver cert list
lhpc webserver cert export lhpc-laptop <path> [--force]  # write the .p12 (0600; no overwrite without --force)
lhpc webserver cert discard-export lhpc-laptop           # delete the stored .p12 (certificate kept)
lhpc webserver cert revoke lhpc-laptop --confirm-label lhpc-laptop
lhpc webserver tls-renew                                 # new server cert, same CAs
```

Each device certificate is an encrypted PKCS#12 `.p12` under `config/tls/exports/` (0600); the
private key exists only inside it. **The passphrase is shown once and never stored**; lost, it
cannot be recovered — `reissue` makes a new bundle and passphrase for the label. A bundle in the
wrong hands is withdrawn with `revoke`, then issue a fresh one. The Certificates panel's fetch
commands (user, paths, labels; `.p12` only for active certificates) show in every serving mode.

**Lifetimes.** Server and client certificates default to **825 days** (`server_cert_days` /
`client_cert_days` in `[webserver]`), with **no auto-renewal**: rotate with `tls-renew` /
`cert reissue`. The server certificate's whole span (one-day backdate included) is capped at
825 × 86,400 − 1 s, Apple's limit, whatever `server_cert_days` says; one over the cap (an older
issue, or a provisional one — see [the clock gate](#the-clock-gate)) is flagged; upgrading LHPC
does not replace it, `tls-renew` then `apply` under a verified clock does. End dates show:

- server: the Webserver panel, `lhpc webserver status` and `lhpc doctor`, with the renewal
  commands from 30 days before the end; an expired one makes `lhpc doctor` non-OK;
- client: marked in the console list and `cert list` from 60 days before expiry (active ones),
  with a `lhpc doctor` line; `cert reissue <label>` and install the new bundle.

### The clock gate

A Pi has no battery-backed clock, so a box in 1970, or one whose GPS gave it a rolled-back date,
would mint material that is already expired (a clock running ahead: "not yet valid") and lock you
out.

**Gated:** `tls-renew`, `cert issue`, `cert reissue`, `cert revoke`, and `expose` when it
re-issues the server certificate (a WLAN join under an unverified clock skips the re-issue instead). The check runs before anything is written (for
`reissue`, before the old certificate is revoked). The clock counts as verified when the kernel
reports it synchronised, its estimated error is at most 1 s, and it reads no earlier than
2025-01-01 (compiled in). File timestamps are not an input (they come from the same clock); the
System panel's Time row uses them only as a diagnostic. This proves the clock synchronised, not
right: a synchronised wrong source passes. Otherwise:

```
ERR   refusing to reissue the certificate for 'phone': the clock is not synchronised
      (no time source has set it yet). Nothing was changed. Fix the clock (see `lhpc doctor`),
      or accept the risk with --accept-unverified-clock
```

`lhpc doctor`'s `clock:` line gives the same verdict; time sources: [Clock](operations.md#clock).
`--accept-unverified-clock` proceeds for that one command only (never stored) and is separate
from `--confirm-recreate` / `--confirm-label`.

**The provisional window.** `webserver init` is not gated (firstboot runs it before the console
exists; a Lite box has no RTC and, on its AP, no NTP). Under an unverified clock it writes the
marker `config/tls/unverified-clock`, then gives the PKI a fixed validity of 2025-01-01 to
2049-12-31 instead of clock dates. While that marker is set and the clock unverified, an
`expose` or WLAN join that must add an address re-issues the server certificate in the same
window rather than refusing; with a verified clock that re-issue is clock-dated. Exposure whose address
is already a SAN changes no dates and is never gated. Within a minute of the clock becoming
verified, the console's watchdog **normalises** the server certificate and CRL (same key,
ordinary dates, nginx reloaded); the CAs keep the window, since replacing the client CA would
void every installed client certificate. `verify` and the Certificates panel show `provisional`
until then.

### Install the client certificate in a browser

Two imports on the remote machine: the **server TLS CA** (`config/tls/server-ca/ca.crt`, without
it: a trust warning) and the **`.p12`** (without it, cert-requiring modes reject the browser).

- **Firefox** (its own store): `about:preferences#privacy` → **Certificates** → *View
  Certificates*. **Your Certificates** → *Import…* the `.p12`; **Authorities** → *Import…* the
  CA, tick "Trust this CA to identify websites".
- **Chrome / Chromium / Edge** (the OS store): Settings → Privacy and security → Security →
  *Manage certificates*, or the OS tool. **Linux**: `certutil -d sql:$HOME/.pki/nssdb -A` for
  the CA and import the `.p12` into the same NSS DB. **macOS**: add both to *Keychain Access*,
  mark the CA trusted. **Windows**: *certmgr.msc* → Trusted Root (CA) and Personal (the `.p12`).
- **Android**: Settings → Security → *Encryption & credentials* → *Install a certificate*: the
  CA under "CA certificate", the `.p12` under "VPN & app user certificate".
- **iOS / iPadOS**: AirDrop or mail both files, install each profile (Settings → *Profile
  Downloaded*), finish under Settings → General → *VPN & Device Management*; for the CA also
  Settings → General → About → *Certificate Trust Settings* → enable full trust.

### Revocation

`revoke` writes the CRL first, then the inventory. A failed CRL write leaves the certificate
**active**; a failed inventory commit after it shows **`revocation-pending`** (never active);
re-running the revoke reconciles it. A damaged inventory (`config/tls/client-ca/client-index.json`
present but unreadable, not valid, or with an entry that lacks a text label, state or hex serial) is
never overwritten: issue and revoke refuse until it is restored from a backup or the PKI is recreated
(`lhpc webserver init --confirm-recreate`), and the console and `lhpc doctor` say it needs repair.
Meanwhile the CRL refresh re-signs the current CRL's revocations unchanged, so no valid client is
locked out and nothing revoked comes back; with no readable current CRL it refuses.

`revoke` and `reissue` reload nginx: NEW connections with the old certificate are refused; one
already established may finish. If the reload fails, the command fails (exit 1, red in the
console), the revocation stays recorded and the console retries the reload on its next pass (the
message says if that retry could not be scheduled). Do not revoke again: run
`lhpc webserver apply`. `verify` does not test revocation.

**The CRL** is valid for 30 days. The console's watchdog (every 60 s on a box with the Wi-Fi
feature or with work pending, else every 300 s) rebuilds it when `nextUpdate` has passed or
`lastUpdate` is in the future, keeping the CA and every revoked serial, and reloads nginx.
Under an unverified clock the rebuild uses the provisional window and is normalised later.
Without the console running, nothing rebuilds it and after 30 days nginx refuses **every**
client certificate ("400 The SSL certificate error"). Beyond this and normalisation, nothing
is regenerated automatically.

## Verifying effective state

```
lhpc webserver verify     # runs the proof checklist and persists state/webserver.json
lhpc webserver status     # renders the cached evidence (read-only)
```

The checklist covers config validity, dependencies, whether `lhpc-web` (the Waitress backend) is
active, `nginx -t`, PKI presence and the effective listeners. The presented certificate, mTLS
behaviour and revocation enforcement are proven only by the live tests with real client material;
verify does not claim them.

**Verify activates nothing.** The evidence file also keeps the *applied snapshot*: the console's
and every enabled proxy's bind/port/scheme/access mode/CIDRs as nginx last loaded them
successfully. The security pills colour a live listener from that snapshot, never from a saved
policy; a live exposed listener with no snapshot reads red (auth `unknown`) until one `apply`.

## Applying changes and recovery

`lhpc webserver apply` (or the console's **Apply**) regenerates the nginx config and validates it
with `nginx -t` **before activating**; a failed validation leaves the previous configuration
active. It then reloads (or, for a bind change, restarts) the running LHPC-owned nginx and reports
success only once the listeners match the desired exposure.

- **Bind change** (loopback ↔ `0.0.0.0`, console or proxy): a reload cannot rebind a held
  socket, so `apply` restarts `lhpc-nginx`. When the running master holds the listener on the
  old side and the applied snapshot shows it on the same port, it restarts without reloading
  first; otherwise it reloads first, which may log `bind() … failed` lines. From the console the
  restart goes through the managed watcher (`lhpc-nginx-restart.path`): the console cannot
  command systemd, it writes a request marker. If the console does not come back:
  `systemctl --user restart lhpc-nginx lhpc-web` from an operator shell.
- **nginx not running**: `apply` reports **"the nginx service is not active — repair required"** and starts
  nothing; start it from an operator shell with `lhpc webserver start-service` (or
  `systemctl --user enable --now lhpc-nginx.service`).

`lhpc webserver reset-defaults` sets desired config back to loopback:8443 /
https / `local-open-remote-auth` / remote off, clears the CIDRs and disables every stack proxy (port
cleared, mode/CIDRs kept). It never deletes CA keys, certificates, the CRL, revocation history,
`.p12` exports or the session secret. A reload cannot move a console nginx holds on `0.0.0.0` back
to loopback, so from an operator shell reset then restarts `lhpc-nginx`; from the console (which
cannot restart services) it says so, and Apply completes it. `verify` then proves the remote
listener is gone. A box
that came up loopback-only (firewall gate at boot): recover over an [SSH tunnel](ssh-tunnel.md)
and re-apply.

## Local dependencies

- `waitress` and `cryptography` are installed into the venv with LHPC.
- `nginx` is a system package; the installer/repair path installs it or says how, in operator
  context, never from the web service. After a manual `apt install nginx`, disable the root
  service as in [first-time bootstrap](#first-time-bootstrap).
- `lhpc-nginx.service` is one of the managed user units
  ([deployment](deployment.md#run-it-under-systemd)); a `ConditionPathExists` on its generated
  config keeps it off until `start-service` has produced one.
