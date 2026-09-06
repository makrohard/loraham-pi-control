# Production webserver (HTTPS + mTLS)

LoRaHAM Pi Control serves its console through a production topology:

```
Browser → HTTPS on <bind>:8443 → Nginx (TLS boundary, mTLS, source-CIDR gate)
        → Waitress over a protected Unix socket → LHPC Flask app
```

Nginx is the **only** TCP listener. The managed `lhpc-web.service` runs `lhpc web --socket`, so
Waitress binds a Unix-domain socket under the runtime root (`state/run/lhpc-web.sock`, 0600) and
opens **no TCP port at all**. Productive serving uses Waitress and never Flask's development
server. A bare `lhpc web` (loopback TCP `:8770`) is a non-productive interactive mode: use it or
the CLI to bootstrap before nginx is up.

The Monitor view renders only **cached, proven** evidence (`state/webserver.json`): it never
infers "active/exposed" from desired configuration and never probes the network during a page
load. Desired configuration lives separately in `config/local.toml [webserver]`.

Operating the console (dashboard, stack pages, settings) is covered in
[operations](operations.md); this page is about serving and exposing it.

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

Out of the box: `bind = 127.0.0.1`, `port = 8443`, HTTPS on, **local access unauthenticated**,
**remote exposure disabled**. Loopback clients use HTTPS with no client certificate; remote
access is off until you explicitly enable it.

`8443` is the default, not a fixed value: `lhpc webserver configure --port <n>` (any
`1–65535`). This page uses `8443` throughout.

## First-time bootstrap

The managed web unit serves the Unix socket immediately, but nginx needs a certificate and a
config before it can front it. `install.sh` does all of this. For a manual install, from an
interactive operator shell (not the web process):

```
sudo apt install -y nginx
sudo systemctl disable --now nginx.service        # keep the package, disable the ROOT service
lhpc webserver init --dns pi.local --ip 192.168.0.10   # two CAs + server cert; SANs are persisted
lhpc webserver start-service                      # generate + validate config, enable + start nginx
```

Installing the Debian `nginx` package activates a system `nginx.service` on `:80`; left running,
that root process owns the web ports and the rootless `lhpc-nginx` user unit cannot bind.
`bootstrap-deps.sh` installs the package and disables the root service for you.

`start-service` is the **only** path that starts nginx (it uses `systemctl --user` and refuses to
run from a managed unit). The console is then at `https://127.0.0.1:8443/`. Until nginx is up,
`lhpc web` serves the non-productive console at `http://127.0.0.1:8770/`.

## Access modes

Authentication is **browser client-certificate (mTLS) only**: no user accounts, passwords or
roles. A client certificate is a named **device credential**; every valid, unrevoked certificate
has equal full access.

| Mode | Loopback | Remote |
|------|----------|--------|
| `local-open-remote-auth` (default) | open (no cert) | requires a valid client cert |
| `auth-everywhere` | requires a client cert | requires a client cert |
| `no-auth` | open | open (**dangerous**) |

Access decisions use the real TCP peer address (`$remote_addr`). Client-supplied
`X-Forwarded-For` / `Forwarded` / `X-LHPC-*` headers are stripped at nginx and never trusted.

Remote exposure is opt-in: a bind of `0.0.0.0`, at least one allowed source CIDR, and a confirm
phrase. `enable-remote` covers a private range with a cert-requiring mode; a public range
(`0.0.0.0/0`) or a `no-auth` remote mode needs `enable-remote-danger`, and the Monitor and
Configuration views show a persistent red warning while `no-auth` remote is active. IPv6 remote
exposure is not supported: IPv6 bind/CIDR values are rejected; `::1` is honoured for local
access only.

**The managed firewall gates exposure.** With it in use, `lhpc webserver apply` is refused while
the firewall is unapplied (*Firewall changes pending*, with the command to run), and at boot
nginx binds loopback-only until the live check passes. LHPC never edits your own firewall
configuration; a port at your router stays yours. See [firewall](firewall.md).

## Remote exposure runbook

Reach the console from another machine, protected by a client certificate. Run every `lhpc`
command from an interactive operator shell on the Pi. Replace `192.168.0.0/24` with your LAN
range and `192.168.0.10` with the Pi's LAN address; `10.42.0.1` / `10.42.0.0/24` is the box's
own [access point](wifi-access-point.md), include it only if you use that. Command details:
[CLI](cli.md).

1. **Name every address in the server certificate.** `install.sh` created the PKI with
   loopback SANs only. `configure` REPLACES each list, so repeat the loopback entries, then
   re-issue the server leaf under the unchanged CAs:
   ```
   lhpc webserver configure --dns localhost --dns pi.local \
                            --ip 127.0.0.1 --ip 192.168.0.10 --ip 10.42.0.1
   lhpc webserver tls-renew
   ```
   Adding an address later repeats this step; client credentials already imported on a phone
   or laptop keep working, because only `init` recreates the CAs. **Never re-run `init` on a
   box with a PKI**: it voids every client certificate you have issued.
2. **Turn on remote access.** `--cidr` is repeatable and REPLACES the allowed-source list; the
   default access mode already requires a client cert off-loopback:
   ```
   lhpc webserver expose --cidr 192.168.0.0/24 --cidr 10.42.0.0/24 --confirm-phrase enable-remote
   lhpc webserver apply
   ```
   A bind change (loopback → `0.0.0.0`, console or a stack proxy) cannot take effect through a
   reload, because nginx cannot rebind a held socket. `apply` verifies the effective listeners
   and restarts `lhpc-nginx` when needed: directly from an operator shell, or from the console
   through the managed restart watcher (`lhpc-nginx-restart.path`; the console itself cannot
   command systemd, it writes a request marker that systemd consumes). `apply` reports success
   only after the listeners match.
3. **Issue a device certificate** and write its bundle to a file:
   ```
   lhpc webserver cert issue lhpc-laptop                       # prints a ONE-TIME passphrase; record it
   lhpc webserver cert export lhpc-laptop ~/lhpc-laptop.p12    # encrypted .p12, mode 0600
   ```
   Prefix labels with `lhpc-`: the label is what the device's certificate chooser shows. The
   console's **Webserver → Certificates** panel offers **Download .p12** on a **loopback**
   session only; a remote browser can never pull a fresh private key.
4. **Copy the bundle and the server CA to the remote machine.** The panel shows paste-ready
   `scp` commands for the server CA and each issued `.p12`, addressed at the box's current
   address (`10.42.0.1` on its AP, its LAN address otherwise); `ca.crt` is also a plain
   download (a public certificate, the path phones can use). One file per `scp` command:
   ```
   scp <user>@<host>:lhpc-laptop.p12 .
   scp <user>@<host>:loraham-pi-control/config/tls/server-ca/ca.crt .
   ```
   **Never** `scp host:{a,b}`: the last argument is always the destination, scp copies
   remote→remote without complaint, and the brace list writes the first file **over the
   second**, destroying your CA certificate. Sanity-check the CA before copying: a PEM file of a
   few hundred bytes whose first line is `-----BEGIN CERTIFICATE-----`.
5. **Import both in the remote browser**: the CA clears the trust warning, the `.p12` supplies
   the client credential (you are prompted for the one-time passphrase). Per-platform steps
   below under [Install the client certificate in a browser](#install-the-client-certificate-in-a-browser).
6. **Firewall.** Apply the managed firewall ([firewall](firewall.md)), or open `8443` in your
   own; LHPC never edits your firewall.
7. **Prove it:** `lhpc webserver verify`, then browse to `https://192.168.0.10:8443/` from the
   remote machine and pick the `lhpc-laptop` certificate when prompted. Afterwards discard the
   bundle on the Pi: `lhpc webserver cert discard-export lhpc-laptop` (the certificate stays).

Back to loopback: `lhpc webserver disable-remote && lhpc webserver apply`, then `verify`.

**Public, no client authentication** (a trusted test rig or LAN only): `lhpc webserver expose
--cidr 0.0.0.0/0 --access-mode no-auth --confirm-phrase enable-remote-danger`, then `apply` and
`verify`. The console is then at `https://<host-ip>:8443/` with a self-signed server certificate
(the browser warns), reachable by **anyone who can route to the host**, with no client
authentication. The elevated phrase is required because the public range and `no-auth` are both
elevated cases; plain `enable-remote` is refused.

## Stack web-UI proxies

Several stacks ship their **own** web UIs; some of those ports bind all interfaces, and their
built-in protection ranges from none (meshtasticd `:9443`, MeshCom `:18083`) to the app's own
login (graywolf). `lhpc` fronts each one with a dedicated nginx listener carrying the same mTLS +
source-CIDR gate as the console, so you never rely on the raw port:

```
lhpc webserver proxy meshtastic --mode lan --port 8447 --access-mode local-open-remote-auth \
     --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

- `--mode` is `local` (loopback only, no firewall rule needed), `lan` (listen; only `--cidr`
  ranges pass) or `public` (`0.0.0.0/0`, elevated). Any non-`local` mode needs
  `--confirm-phrase enable-remote`; `public`, a `no-auth` `--access-mode`, or an `http`
  `--scheme` need `enable-remote-danger`.
- `--port` is **required**: a page with no port is not proxied. The console suggests a stable
  per-page default (console port + 1 + the page's position: the stacks' first pages sorted by
  id, then further pages; on a fresh box graywolf `8444`, meshcom `8445`, meshcore `8446`,
  meshtastic `8447`, skipping ports already saved); any free port ≥ 1024 works.
- `--access-mode` (alias `--auth`) takes the console's values (default
  `local-open-remote-auth`); proxied UIs use the **same** client certificates. Pass it
  explicitly: a stack whose stored policy is already `no-auth` otherwise refuses with *elevated
  confirmation required*.
- Eligibility is manifest-derived: every **component** that declares a client http/https web
  endpoint is its own proxied **page** with its own port, policy and listener. A stack's first
  page is addressed by the stack id (`lhpc webserver proxy meshcore`), further pages by
  `<stack>-<component>`; the stack's Webserver panel shows one sub-panel per page. Pages:
  graywolf, meshcom, meshtastic (one each) and meshcore (two: `meshcore` = the MeshCore Web UI,
  `meshcore-meshcore-node` = the openHop repeater dashboard). kiss and the daemon speak non-HTTP
  protocols and cannot be proxied. A new web component becomes eligible automatically but is
  configured only when you save its panel or submit the bulk form; nothing is exposed on its own.

**One policy for all stack WebGUIs.** The **Webserver → Stacks WebGUIs** subpanel applies one
policy (access local/LAN/public, scheme, access mode, allowed CIDRs) to **every** eligible page
in a single confirmed action; the sibling **LHPC WebGUI** subpanel configures the console
itself. Ports stay per-page: an existing port is never changed, a page without one gets its
suggested default (unique across the set), and if a unique port cannot be assigned the action
fails before changing anything. The whole set is validated first; any problem (confirmation,
CIDRs, http + cert-auth, a port conflict) refuses the entire action. Confirmation and firewall
rules are the per-stack ones; activation is one staged apply behind the firewall gate. Each
stack's own panel remains available for individual exceptions afterwards; a later bulk Apply
overwrites the shared fields again.

Keep the native port firewalled and reach the UI through the proxy port
([what actually listens](firewall.md#what-actually-listens)).

## Certificates and the two-CA PKI

Two independent CAs (private keys never leave `config/tls/`, 0600):

- **Server TLS CA** signs the HTTPS server certificate (DNS + IP SANs; `0.0.0.0` is never a
  SAN). `tls-renew` stays under the same CA.
- **Client-auth CA** signs client/device certificates and the CRL.

```
lhpc webserver init --dns pi.local --ip 192.168.0.10     # once; --confirm-recreate to redo (voids every client cert)
lhpc webserver cert issue lhpc-laptop                    # one-time .p12 passphrase, shown once, never stored
lhpc webserver cert reissue lhpc-laptop                  # rotate + new passphrase
lhpc webserver cert list
lhpc webserver cert export lhpc-laptop <path> [--force]  # write the .p12 (0600; no overwrite without --force)
lhpc webserver cert discard-export lhpc-laptop           # delete the stored .p12 (certificate kept)
lhpc webserver cert revoke lhpc-laptop --confirm-label lhpc-laptop
lhpc webserver tls-renew                                 # new server cert, same CAs
```

Each client certificate is exported as an encrypted PKCS#12 `.p12` bundle under
`config/tls/exports/` (0600); the private key exists only inside that bundle. The fetch
commands in the Certificates panel (username, paths, labels) are shown only to a **trusted
session**: loopback, or a remote session whose *applied* policy already requires a client
certificate. Under no-auth remote exposure they are withheld.

### Install the client certificate in a browser

Two imports on the remote machine, neither automated by LHPC: the **server TLS CA**
(`config/tls/server-ca/ca.crt`) so the browser trusts `https://…:8443/`, and the **`.p12`
bundle** for the client credential mTLS asks for. Without the CA the connection works with a
trust warning; without the `.p12` any cert-requiring access mode rejects the browser.

- **Firefox** (its own store): `about:preferences#privacy` → **Certificates** → *View
  Certificates*. **Your Certificates** → *Import…* the `.p12`; **Authorities** → *Import…* the
  CA, tick "Trust this CA to identify websites". Firefox prompts for the certificate on first
  connect.
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

`revoke` is transactional: the CRL is written first, then the inventory. If the CRL write fails
the certificate stays **active**. If the CRL is written but the inventory commit fails, the
certificate shows as **`revocation-pending`** (a durable marker), never as active and never as a
clean `revoked`; re-running the revoke reconciles it. Even a committed `revoked` is reported
**effective** only once the proxy has reloaded with the new CRL and a revoked certificate is
proven rejected; until then status says so.

## Verifying effective state

```
lhpc webserver verify     # runs the proof checklist and persists state/webserver.json
lhpc webserver status     # renders the cached evidence (read-only)
```

The checklist covers config validity, dependency presence, the Waitress socket, `nginx -t` and
PKI presence. A live listener, the presented certificate, mTLS behaviour and revocation
enforcement are proven only on a box with a real proxy and real client material
([live tests](live-test.md)); without that proof, remote exposure is reported as **not proven
active**.

**Verify activates nothing and records no activation.** Beside the desired config, the evidence
file keeps an *applied snapshot*: the console's and every enabled proxy's
bind/port/scheme/access-mode/CIDRs as they were when nginx last **successfully loaded them**.
That is what the security pills colour a LIVE listener with, never a freshly saved policy nginx
has not seen. Narrowing the bind, adding client authentication or tightening the allow-list all
read unchanged until you `apply`; a saved port change keeps the old port represented, because
that is where the socket still is. A live exposed listener with no applied snapshot reads red
("policy unknown") until one `apply` records it; a loopback listener is unaffected.

## Applying changes and recovery

`lhpc webserver apply` (or the console's **Apply**) regenerates the nginx config, validates it
with `nginx -t` **before activating**, then reloads the running LHPC-owned nginx via `nginx -s
reload` (or restarts it through the watcher when a bind changed). The web process never calls
`systemctl` and never starts the service:

- If validation fails, the previous proven configuration stays active and status says so.
- If nginx is not running, `apply` reports **"service not active / repair required"** and
  performs no start. Starting happens only in operator context: `lhpc webserver
  start-service` (or `systemctl --user enable --now lhpc-nginx.service`).

`lhpc webserver reset-defaults` returns desired config to loopback:8443 / local-unauthenticated
/ remote-off and clears remote CIDRs. It never deletes CA keys, certificates, the CRL,
revocation history, `.p12` exports or the session secret; `verify` afterwards proves the remote
listener has ceased. If a box comes up loopback-only (firewall gate at boot), recover over an
[SSH tunnel](ssh-tunnel.md) and re-apply.

## Local dependencies

- `waitress` and `cryptography` are LHPC dependencies (installed into the venv).
- `nginx` is a system package; the installer/repair path detects it and instructs or installs it
  in operator context. The web service never installs packages. After any manual `apt install
  nginx`, run `sudo systemctl disable --now nginx.service` yourself.
- The installer writes and enables the rootless `lhpc-nginx.service` user unit (one of the
  canonical managed units, byte-exact-verified by the self-update integrity proof). It is
  enabled but starts only once `start-service` has generated and validated its config (a
  `ConditionPathExists` gates it until then); runtime config changes reload it via `nginx -s
  reload`, never `systemctl`, from the web process.
