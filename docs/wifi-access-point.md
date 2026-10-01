# Wi-Fi: access point and client

The box's one Wi-Fi radio is either its **own access point** (NetworkManager profile `lhpc-ap`;
the box is `10.42.0.1`) or a **client** of your WLAN. The console's **Network** panel switches
between the two, with the AP as the automatic way home.

## Contents

- [The managed AP](#the-managed-ap)
- [The Network panel](#the-network-panel)
- [Creating `lhpc-ap` by hand](#creating-lhpc-ap-by-hand)
- [Reaching the console over the AP](#reaching-the-console-over-the-ap)
- [Troubleshooting](#troubleshooting)

## The managed AP

On the Lite image, first boot creates `lhpc-ap`: `802-11-wireless.mode ap`, band `bg`,
`ipv4.method shared` (the box is `10.42.0.1/24` and runs DHCP + DNS for its clients), WPA2-PSK,
`autoconnect yes`. Its SSID defaults to the hostname's `lhpc-<suffix>`; the passphrase comes with
the image's first steps. It is the **recovery** network: up after every boot unless a preferred
WLAN is visible, back within seconds of losing a WLAN, and never deletable from the panel.

By hand: `sudo nmcli connection up lhpc-ap` / `down lhpc-ap`. Set the Wi-Fi country first
(`sudo raspi-config` → *Localisation Options* → *WLAN Country*): an AP does not start without
one.

## The Network panel

The panel (Apps page, **Network**) appears wherever `nmcli` and a Wi-Fi profile `lhpc-ap` exist
(any box, after [creating the profile](#creating-lhpc-ap-by-hand)). Acting on it needs the polkit
rule `bootstrap-deps.sh` installs (opt-out `--no-network-controls`); the panel shows the install
command when it is missing.

- **Join** (SSID + password). **Scan** shows only while the box is not hosting its AP (the radio
  cannot survey other channels then). A confirm page comes first, because your AP session ends
  when the box joins; a detached helper then activates the profile, waits for the lease and
  records the outcome the panel shows. The password reaches NetworkManager through a 0600 file
  (never argv, logs or state); NM stores it root-owned. Profiles are identified by NM UUID.
- **Allow console from that network** (checkbox, default on): the helper adds the joined subnet
  to the console allow-list and the joined address and names to the server certificate
  (re-issued; [clock gate](webserver.md#the-clock-gate)), then applies. With the managed
  firewall's AP rules **off**, the new CIDR changes the ruleset, so the apply waits for the shown
  sudo command (over SSH; its port is open) and the watchdog then completes it; with the AP rules
  on it completes at once. A join whose console would be blocked by an unverified firewall is
  refused *before* the AP drops. With the checkbox off the box is SSH-only there.
- **AP fallback.** Client profiles are created `autoconnect no`, so after a reboot the box is
  its AP again; a lost WLAN brings the AP back within seconds, a failed join (wrong password,
  network gone) within about a minute. The console is `https://<hostname>.local:8443` on a
  joined network and `https://10.42.0.1:8443` on the AP.
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

`ipv4.method shared` gives the box `10.42.0.1/24`. Use WPA2: the Pi's chip has inconsistent WPA3
AP support. Remove the AP with `sudo nmcli connection delete lhpc-ap` (the panel disappears).

## Reaching the console over the AP

The AP only puts the phone on the box's network. Certificate, SANs, allow-list and the
firewall's AP rules: the [remote exposure runbook](webserver.md#remote-exposure-runbook); then
browse to `https://10.42.0.1:8443` and present the certificate.

## Troubleshooting

- **AP does not start / no network appears**: the Wi-Fi country is unset (see above).
- **Passphrase rejected**: WPA2 needs 8 or more characters.
- **Phone joins but the page does not load**: the console is not exposed to the AP subnet, or
  the managed firewall lacks the AP rules.
- **Lost the SSH session while joining or switching**: expected; reconnect on the new network,
  or use Ethernet.
