# Wi-Fi: access point and client

A box has one Wi-Fi radio and it does one job at a time: it is either its **own access point**
(a phone or laptop joins it and reaches the console at `10.42.0.1`) or a **client** of your
WLAN. The NetworkManager profile `lhpc-ap` is the managed AP, and the console's **Network**
panel switches between the two with the AP as the automatic way home. Reaching the console
once you are on the network is the [remote exposure runbook](webserver.md).

## Contents

- [The managed AP](#the-managed-ap)
- [The Network panel](#the-network-panel)
- [Creating `lhpc-ap` by hand](#creating-lhpc-ap-by-hand)
- [Reaching the console over the AP](#reaching-the-console-over-the-ap)
- [Troubleshooting](#troubleshooting)

## The managed AP

On the Lite image, first boot creates `lhpc-ap`: `802-11-wireless.mode ap`, band `bg`,
`ipv4.method shared` (the box is `10.42.0.1/24` and runs DHCP + DNS for its clients), WPA2-PSK,
`autoconnect yes`. Its SSID defaults to the same `lhpc-<suffix>` as the hostname; the passphrase comes with the image's first
steps. It is a **recovery** network: it is up after every boot unless a preferred WLAN is
visible, it returns within seconds of losing a WLAN, and the panel can never delete it.

By hand: `sudo nmcli connection up lhpc-ap` / `down lhpc-ap`. Set the Wi-Fi country first
(`sudo raspi-config` → *Localisation Options* → *WLAN Country*): an AP does not start without
one.

## The Network panel

The panel (Apps page, **Network**) appears wherever `nmcli` exists **and** a Wi-Fi profile
named `lhpc-ap` exists. That is a capability check, not an image check: a Lite box has it, and
any other box gains it by [creating the profile](#creating-lhpc-ap-by-hand). Acting on it also
needs the polkit rule that authorizes the operator for NetworkManager; `bootstrap-deps.sh`
installs it (opt-out `--no-network-controls`), and the panel shows the install command when it
is missing.

- **Join** (SSID typed in + password). **Scan** renders only while the box is not hosting its AP (a radio hosting the AP cannot survey other channels). The join is two-stage (a confirm page: your AP
  session ends the moment the box joins) and respond-first: a detached helper activates the
  profile, waits for the lease and writes the outcome the panel shows afterwards. The password
  goes to NetworkManager through a 0600 secrets file (never argv, logs or state) and is
  persisted root-owned by NM itself. Profiles are identified by NM UUID; the SSID is
  display-only.
- **Allow console from that network** (checkbox, default on): the helper extends the console
  allow-list to the joined subnet, adds the joined address and names as server-certificate
  SANs, re-issues the server certificate and applies. With the managed firewall and its AP rules **off**, the joined CIDR changes the ruleset, so the apply is deferred until you run the shown sudo command (over SSH, port 22 is open there); the watchdog then completes it. With the AP rules on it completes at once ([firewall](firewall.md)). A join that would leave the console blocked on the new network
  is refused *before* the AP drops. With the checkbox off the box is SSH-only there.
- **AP fallback.** Client profiles are created `autoconnect no`, so after a reboot the box is
  its AP again; a lost WLAN brings the AP back within seconds, a failed join (wrong password,
  network gone) within about a minute.
  The box reappears as `https://<hostname>.local:8443` on a joined network and
  `https://10.42.0.1:8443` on its AP.
- **Prefer** (exactly one stored network): its profile gets `autoconnect yes`, priority 10,
  so NM picks it at boot when visible; while the box sits on the AP a watchdog retries it
  every 10 minutes, but only while no client is associated with the AP (`iw` station table),
  and **Retry now** forces an attempt. **Stop preferring** clears it.
- **Reconnect** and **Forget** for stored networks; **Back to AP mode** switches now and
  clears the preference so the watchdog does not re-join.

## Creating `lhpc-ap` by hand

For a manual install or a Desktop box, create the compatible profile (choose your own SSID and
a passphrase of at least 8 characters):

```bash
sudo nmcli connection add type wifi ifname wlan0 con-name lhpc-ap autoconnect yes ssid "<ssid>"
sudo nmcli connection modify lhpc-ap \
     802-11-wireless.mode ap 802-11-wireless.band bg \
     ipv4.method shared \
     wifi-sec.key-mgmt wpa-psk wifi-sec.psk "<passphrase>"
sudo nmcli connection up lhpc-ap
```

`ipv4.method shared` gives the box `10.42.0.1/24`, the address the console assumes. WPA2 is the
reliable choice; the Pi's own chip has inconsistent WPA3 AP support. From then on the Network
panel and its semantics above apply. To remove the AP: `sudo nmcli connection delete lhpc-ap`
(the panel then disappears).

## Reaching the console over the AP

The AP only puts the phone on the box's network. Order matters, certificates first and exposure
last: follow the [remote exposure runbook](webserver.md) with `10.42.0.1` as a server-certificate
SAN and `10.42.0.0/24` among the allowed CIDRs, issue the phone's client certificate before
exposing, and with the managed firewall enable its AP rules **before** the radio becomes an AP
([firewall](firewall.md)). Then browse to
`https://10.42.0.1:8443` and present the certificate.

## Troubleshooting

- **AP does not start / no network appears**: the Wi-Fi country is unset (see above).
- **Passphrase rejected**: WPA2 needs 8 or more characters.
- **Phone joins but the page does not load**: the console is not exposed to the AP subnet, or
  the managed firewall lacks the AP rules.
- **Lost the SSH session while joining or switching**: expected, the radio changed networks.
  Reconnect on the new network, or use Ethernet.
