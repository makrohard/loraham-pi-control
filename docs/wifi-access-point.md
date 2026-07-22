# Raspberry Pi as a WiFi access point (field access)

In the field with no WiFi, turn the Pi into its **own** WiFi network so a phone or
laptop can join it and reach the console. This is a **network-layer** setup done
with the OS (NetworkManager) — it is independent of `lhpc`. Once the phone is on
the Pi's network, reaching the web console is the separate step in
[`webserver.md`](webserver.md).

Everything below uses `nmcli` (NetworkManager), which is the default on Raspberry
Pi OS (Bookworm/Trixie) and handles the access point, its DHCP server, and
start-on-boot for you — no `hostapd`/`dnsmasq` editing.

## Contents

- [Before you start](#before-you-start)
- [Create the access point (once)](#create-the-access-point-once)
- [Turn it on and off](#turn-it-on-and-off)
- [Start the AP on boot](#start-the-ap-on-boot)
- [Connect your phone](#connect-your-phone)
- [Reach the lhpc console](#reach-the-lhpc-console)
- [Troubleshooting](#troubleshooting)
- [Remove the access point](#remove-the-access-point)

## Before you start

- **Set the WiFi country first — the #1 reason an AP won't start.** Run
  `sudo raspi-config` → *Localisation Options* → *WLAN Country* → pick yours,
  then *Finish*.
- **One WiFi radio, one job.** Starting the AP **disconnects the Pi from any WiFi
  it was joined to**. If you are connected over WiFi (SSH), you will lose it — do
  the first setup over Ethernet, a USB-serial console, or a keyboard + monitor.
- A Pi Zero 2 W is **2.4 GHz only** (fine — better range).
- Choose an SSID (network name) and a password of **at least 8 characters**.

## Create the access point (once)

Replace the SSID and password. `field-ap` is just the profile name.

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name field-ap \
     autoconnect no ssid "LoRaHAM-Pi"

sudo nmcli connection modify field-ap \
     802-11-wireless.mode ap \
     802-11-wireless.band bg \
     ipv4.method shared \
     wifi-sec.key-mgmt wpa-psk \
     wifi-sec.psk "ChangeMe-StrongPassword"
```

What the key lines do:

- `mode ap` — be an access point instead of joining one.
- `ipv4.method shared` — the Pi becomes `10.42.0.1` and runs a DHCP + DNS server,
  so clients get an address automatically.
- `wifi-sec.key-mgmt wpa-psk` — **WPA2 (AES/CCMP)**: current, secure, and works
  with every phone. (WPA3-SAE is possible with `wifi-sec.key-mgmt sae`, but the
  Pi's built-in chip has inconsistent WPA3 *AP* support, so WPA2 is the reliable
  default.)

## Turn it on and off

```bash
sudo nmcli connection up   field-ap    # AP on
sudo nmcli connection down field-ap    # AP off
```

## Start the AP on boot

Flip one property on the profile:

```bash
sudo nmcli connection modify field-ap connection.autoconnect yes   # AP on every boot
sudo nmcli connection modify field-ap connection.autoconnect no    # back to manual
```

With `autoconnect yes` the AP comes up automatically at boot with no login. You can
still stop it for the current session with `nmcli connection down field-ap`.

## Connect your phone

1. On the phone, join the WiFi network you named (e.g. `LoRaHAM-Pi`) with the
   password.
2. The Pi is reachable at **`10.42.0.1`** (or `loraham.local`).

Verify on the Pi:

```bash
nmcli connection show --active     # field-ap should be listed
ip addr show wlan0                 # should show 10.42.0.1
```

## Reach the lhpc console

The AP only puts the phone on the Pi's network. **Order matters** (the same ordering as
[firewall.md](firewall.md), scenario 4): certificates FIRST, exposure LAST — a fresh setup
that exposes before any PKI exists applies a config with no certificates behind it.

**1 — put the AP address in the server certificate.**

*First install (no PKI yet):* `init` creates the CAs and the server cert together —

```bash
lhpc webserver init --ip 10.42.0.1 --dns loraham.local
```

*Existing install (CAs already present):* **never re-run `init`** — it would recreate the
CAs and void every client certificate you have issued. Add the SANs to the existing
server cert instead:

```bash
lhpc webserver configure --ip 10.42.0.1 --dns loraham.local
lhpc webserver tls-renew
```

**2 — issue the phone's client certificate and move it across:**

```bash
lhpc webserver cert issue phone
lhpc webserver cert export phone ~/phone.p12
```

**3 — expose the console to the AP subnet and activate:**

```bash
lhpc webserver expose --cidr 10.42.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

Then browse to **`https://10.42.0.1:8443`** and present the client certificate you
imported to the phone (see [`webserver.md`](webserver.md) for the full mTLS runbook).

## Troubleshooting

- **AP won't start / no network appears** — the WiFi country is almost always
  unset. Redo *Before you start*, then `sudo nmcli connection up field-ap`.
- **Password rejected** — WPA2 requires **8+ characters**.
- **Phone joins but can't load the page** — you are on the network, but the console
  is not exposed to the AP subnet yet. See *Reach the lhpc console*.
- **Lost your SSH session** — expected: the Pi left your WiFi to become an AP.
  Reconnect by joining the Pi's new network, or use Ethernet/serial.

## Remove the access point

```bash
sudo nmcli connection down field-ap
sudo nmcli connection delete field-ap
```
