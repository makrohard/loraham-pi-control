# Production webserver (HTTPS + mTLS)

LoRaHAM Pi Control can serve its console through a production topology:

```
Browser → HTTPS on <bind>:8443 → Nginx (TLS boundary, mTLS, source-CIDR gate)
        → Waitress over a protected Unix socket → LHPC Flask app
```

Nginx is the **only** TCP listener. The managed `lhpc-web.service` runs `lhpc web --socket`, so
Waitress binds a Unix-domain socket under the runtime root (`state/run/lhpc-web.sock`, 0600) and
opens **no TCP port at all**. Productive serving uses Waitress and **never** falls back to
Flask's development server. (A bare `lhpc web` — loopback TCP `:8770` — is a non-productive
interactive mode only; use it or the CLI to bootstrap before nginx is up.)

> Status truthfulness: the Monitor view renders only **cached, proven** evidence
> (`state/webserver.json`) — it never infers "active/exposed" from desired configuration, and
> never probes the network during a page load. Desired configuration lives separately in
> `config/local.toml [webserver]`.

## Contents

- [Default behaviour](#default-behaviour-local-https-no-client-cert)
- [First-time bootstrap](#first-time-bootstrap-operator-context)
- [Access modes](#access-modes)
- [Remote exposure](#remote-exposure-opt-in)
- [Expose to your LAN with mTLS — runbook](#expose-to-your-lan-with-mtls--runbook)
- [Certificates and the two-CA PKI](#certificates-and-the-two-ca-pki)
  - [Install the client certificate in a browser](#install-the-client-certificate-in-a-browser)
  - [Bundle transfer safety](#bundle-transfer-safety)
  - [Revocation](#revocation)
- [Verifying effective state](#verifying-effective-state)
- [Applying changes / recovery](#applying-changes--recovery)
- [Local dependencies](#local-dependencies)
- [Not validated here](#not-validated-here)

## Default behaviour (local, HTTPS, no client cert)

Out of the box: `bind = 127.0.0.1`, `port = 8443`, HTTPS on, **local access unauthenticated**,
**remote exposure disabled**. Loopback clients use HTTPS with no client certificate; remote
access is off until you explicitly enable it.

## First-time bootstrap (operator context)

The managed web unit serves the Unix socket immediately, but nginx needs a certificate + config
before it can front it. After install (or self-update), run — from an interactive operator
shell, not the web process:

```
sudo apt install -y nginx            # required system dependency
lhpc webserver init --dns pi.local --ip 192.168.0.10   # PKI + server cert; SANs are persisted
lhpc webserver start-service         # generates+validates config, then enables+starts nginx
```

`start-service` is the ONLY path that starts nginx (it uses `systemctl --user` and refuses to run
from a managed unit). The console is then at `https://127.0.0.1:8443/`. Until nginx is up you can
use the non-productive local console: `lhpc web` (loopback `http://127.0.0.1:8770/`).

## Access modes

Authentication is **browser client-certificate (mTLS) only** — there are no user accounts,
passwords, or roles. A client certificate is a named **device credential**; every valid,
unrevoked certificate has equal full access.

| Mode | Loopback | Remote |
|------|----------|--------|
| `local-open-remote-auth` (default) | open (no cert) | requires a valid client cert |
| `auth-everywhere` | requires a client cert | requires a client cert |
| `no-auth` | open | open (**dangerous** — see below) |

Access decisions use the **real TCP peer address** (`$remote_addr`). Client-supplied
`X-Forwarded-For` / `Forwarded` / `X-LHPC-*` headers are stripped at Nginx and never trusted.

## Remote exposure (opt-in)

Remote access is off by default. To enable it you must set a bind of `0.0.0.0`, provide **at
least one allowed source CIDR**, and confirm:

```
lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
```

- A public default route (`0.0.0.0/0`) or a **no-auth** remote mode requires the stronger
  `--confirm-phrase enable-remote-danger`.
- **IPv6 remote exposure is not supported in this release** — IPv6 bind/CIDR values are
  rejected; `::1` is honoured for local access only.
- LHPC never edits UFW/nftables/router/DNS. Opening the port at your firewall/router is your
  responsibility.

`no-auth` + remote means **anyone in the allowed range reaches the console with no client
authentication**. The Monitor and Configuration views show a persistent red warning while
this is active.

## Expose to your LAN with mTLS — runbook

The end-to-end path to reach the console from another machine on your network, protected by a
client certificate. Run every `lhpc` command from an interactive operator shell on the Pi (not the
web process). Replace `192.168.0.0/24` with your LAN range and `192.168.0.10` with the Pi's LAN IP.

1. **Front-end + PKI** (skip if `install.sh` already did it):
   ```
   sudo apt install -y nginx
   lhpc webserver init --dns pi.local --ip 192.168.0.10   # two CAs + server cert (DNS/IP SANs)
   lhpc webserver start-service                            # generate+validate config, start nginx
   ```
2. **Turn on remote access** (default access mode already requires a client cert off-loopback):
   ```
   lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
   lhpc webserver apply                                    # validate + reload nginx
   ```
   > **Known limitation:** a bind change (loopback → `0.0.0.0`) does not take effect through
   > the nginx *reload* that `apply` performs. Check `lhpc webserver status` — if it still shows
   > `remote_listener=False`, restart the front-end once: `systemctl --user restart lhpc-nginx`,
   > then re-run `lhpc webserver verify`.
3. **Issue a device certificate** and get its bundle off the Pi:
   ```
   lhpc webserver cert issue laptop        # prints a ONE-TIME passphrase — record it now
   lhpc webserver cert export laptop ~/laptop.p12         # write the encrypted .p12 to a file
   ```
   Or, from a browser **on the Pi** (loopback only), open the console's Webserver → Certificates
   panel and click **Download** on the `laptop` row. A remote browser can never pull a fresh key.
4. **Copy the bundle to the remote machine** over a trusted channel (`scp`, USB) — it is encrypted
   with the one-time passphrase, but treat it as a private key. Also copy the **server CA**
   certificate (`config/tls/` on the Pi) so the browser trusts the server.
5. **Install both in the remote browser** — import the server CA (clears the TLS warning) and the
   `.p12` (supplies the client credential). See [below](#install-the-client-certificate-in-a-browser).
6. **Open the port at your firewall/router** — LHPC never touches UFW/nftables/router/DNS; this
   step is yours.
7. **Prove it:** `lhpc webserver verify`, then browse to `https://192.168.0.10:8443/` from the
   remote machine and pick the `laptop` certificate when prompted.

To turn it back off: `lhpc webserver disable-remote && lhpc webserver apply` (then `verify`).
IPv6 remote exposure is not supported in this release (see above). Command details:
[`docs/cli.md`](cli.md#webserver).

## Certificates and the two-CA PKI

Two independent CAs (private keys never leave `config/tls/`, 0600):

- **Server TLS CA** → signs the HTTPS server certificate (DNS + IP SANs; `0.0.0.0` is never a
  SAN). Renewals stay under the same CA unless you explicitly rotate it.
- **Client-auth CA** → signs client/device certificates and the CRL.

Bootstrap everything from the CLI:

```
lhpc webserver init --dns pi.local --ip 192.168.0.10
lhpc webserver cert issue laptop        # prints a ONE-TIME bundle passphrase (record it)
lhpc webserver cert list
lhpc webserver tls-renew
lhpc webserver cert revoke laptop --confirm-label laptop
```

Each client certificate is exported as an **encrypted PKCS#12 `.p12`** bundle under
`config/tls/exports/` (0600). The private key exists only inside that bundle. The one-time
passphrase is shown once and never stored or logged.

### Install the client certificate in a browser

Two imports are needed on the remote machine, and LHPC automates neither:

- the **server TLS CA** — so the browser trusts `https://…:8443/` instead of warning;
- the **`.p12` client bundle** — the device credential mTLS asks for (you'll be prompted for the
  one-time passphrase from `cert issue`).

**Firefox** (its own store, not the OS): `about:preferences#privacy` → **Certificates** → *View
Certificates*. Under **Your Certificates** → *Import…* the `.p12`. Under **Authorities** → *Import…*
the server CA and tick "Trust this CA to identify websites". Firefox prompts you to pick the
certificate on first connect.

**Chrome / Chromium / Edge** (use the OS store): open *Manage certificates* (Settings → Privacy and
security → Security → Manage certificates) or the OS tool directly — **Linux**: `certutil -d
sql:$HOME/.pki/nssdb -A` for the CA and import the `.p12` into the same NSS DB; **macOS**: add both
to *Keychain Access* and mark the CA trusted; **Windows**: *certmgr.msc* → Trusted Root (CA) and
Personal (the `.p12`).

**Android**: Settings → Security → *Encryption & credentials* → *Install a certificate* — install
the CA under "CA certificate" and the `.p12` under "VPN & app user certificate".

**iOS / iPadOS**: AirDrop/email both files, install each profile (Settings → *Profile Downloaded*),
then Settings → General → *VPN & Device Management* to finish, and for the CA also Settings →
General → About → *Certificate Trust Settings* → enable full trust.

Without the CA import the connection still works but shows a trust warning; without the `.p12` any
cert-required access mode rejects the browser.

### Bundle transfer safety

A **new** `.p12` bundle can be downloaded through the web UI **only from a loopback session** —
a remotely-authenticated browser can manage existing certificates but can never pull a freshly
created private key. The CLI can always locate bundles on disk. After transferring a bundle,
discard it: `lhpc webserver cert discard-export laptop` (revocation history is preserved).

### Revocation

`revoke` is transactional: it writes the CRL first, then commits the inventory, committing the
`revoked` state only when both succeed. If the CRL write fails the certificate stays **active**
(nothing changed). If the CRL is written but the inventory commit fails, the certificate is
shown as **`revocation-pending`** (a durable marker) — never as ordinary active and never as a
clean `revoked`; re-running the revoke reconciles it. Even a committed `revoked` is only
reported **effective** once the proxy has reloaded with the new CRL and a revoked certificate is
proven rejected — until that proof exists, status says so truthfully. (The end-to-end "revoked
cert rejected by Nginx" check requires a real proxy + real client-cert material and is an opt-in
integration test, not part of the mocked unit suite.)

## Verifying effective state

```
lhpc webserver verify     # runs the proof checklist and persists state/webserver.json
lhpc webserver status     # renders the cached evidence (read-only)
```

The proof checklist covers: config validity, dependency presence, Waitress socket, `nginx -t`
validation, and PKI presence. Live listener / HTTPS-cert-presented / mTLS-behaviour /
revocation-enforcement are proven only under opt-in integration with a real proxy; in their
absence remote exposure is treated as **not proven active**.

## Applying changes / recovery

`lhpc webserver apply` (or the GUI **Apply**) regenerates the Nginx config, **validates it with
`nginx -t` before activating**, then reloads an already-running LHPC-owned Nginx master via
`nginx -s reload`. The running web process never calls `systemctl` and never starts the service:

- If validation fails, the **previous proven configuration stays active** and status says so.
- If the Nginx service is not running, `apply` reports **"service not active / repair
  required"** and performs no start. Starting nginx happens only in operator context via
  **`lhpc webserver start-service`** (or `systemctl --user enable --now lhpc-nginx.service`),
  never from the web process.

`lhpc webserver reset-defaults` returns desired config to loopback:8443 / local-unauthenticated
/ remote-off and clears remote CIDRs. It never deletes CA keys, certificates, the CRL,
revocation history, `.p12` exports, or the session secret; verify afterwards to prove the remote
listener has ceased.

## Local dependencies

- `waitress` and `cryptography` are declared LHPC dependencies (installed into the venv).
- `nginx` is a system package; the installer/repair path detects it and instructs/installs it
  in operator context. The running web service never installs packages.
- The installer writes and enables a rootless `lhpc-nginx.service` user unit (one of the four
  canonical managed units, byte-exact-verified by the self-update integrity proof). It is
  enabled but only starts once `lhpc webserver start-service` has generated + validated its
  config (a `ConditionPathExists` gates it until then); runtime config changes reload it via
  `nginx -s reload`, never `systemctl`, from the web process.

## Not validated here

This document describes behaviour verified by unit tests (config/PKI/CRL/nginx-config
generation/validation, evidence, CLI/GUI wiring). Real Nginx/Waitress serving, live browser
mTLS, and end-to-end revocation enforcement require on-host integration and are not claimed as
hardware-validated.
