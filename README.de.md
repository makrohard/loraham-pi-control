# LoRaHAM Pi Control (`lhpc`) — Anleitung (Deutsch)

**[The original English README](README.md)**

LoRa-Amateurfunk-Stacks auf einem Raspberry Pi installieren, konfigurieren und betreiben — über eine
CLI und ein lokales WebGUI. `lhpc` installiert jeden Stack aus einem gepinnten Commit, einem Release,
dem Entwicklungsstand oder als geprüftes vorgebautes Binary, schreibt die Konfiguration jeder App aus
einem Satz Einstellungen, startet und stoppt sie in Abhängigkeitsreihenfolge und gibt jedes Band
jeweils nur einem Stack. Läuft rootless, unter der eigenen Benutzerkennung. Neun Stacks: der
LoRaHAM-Daemon mit Chat, Voice, KISS-TNC und Graywolf (APRS) sowie Meshtastic, MeshCom, MeshCore
und Reticulum — auf einem Pi Zero 2W oder Pi 5.

- **Du hast einen Pi mit passendem [LoRa-HAT](#hardware)?**

  > **Der normale Weg ist ein fertiges Image:** Raspberry Pi OS mit installiertem und gebautem LHPC
  > samt allen Stacks; der erste Start konfiguriert die Box und liest optional eine
  > `lhpc-config.txt` von der Boot-Partition. Diese Seite ist für den Selbstbau.
  >
  > ## → [Fertiges Image holen](https://github.com/makrohard/loraham-images)

- **Kein Pi zur Hand? Zwei Wege ganz ohne Hardware**

  > [![Live-Demo](https://img.shields.io/badge/%E2%96%B6%20Live--Demo-im%20Browser-2ea44f)](https://makrohard.github.io/loraham-pi-control/)
  > — das echte WebGUI im Browser, simuliert via Pyodide. **Keine Anmeldung.**
  >
  > [![In GitHub Codespaces öffnen](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)
  > — das Test-Lab: echte Stack-Prozesse auf simulierter Hardware. **Braucht ein (kostenloses)
  > GitHub-Konto.** Siehe [`docs/testlab.md`](docs/testlab.md) (englisch).

> Maßgeblich ist die englische [`README.md`](README.md). Code, Oberflächentexte und die übrigen
> Dokumente sind auf Englisch.

## Contents

- [Hardware](#hardware)
- [Stacks](#stacks)
- [Installation](#installation)
- [Stacks konfigurieren & betreiben](#stacks-konfigurieren--betreiben)
- [Danach](#danach)
- [Fehlerbehebung](#fehlerbehebung)
- [Dokumentation](#dokumentation)

## Hardware

Jedes Raspberry-Pi-HAT, dessen Funkchip ein **SX1276**, **SX1278** oder **SX1262** ist und
**direkt am SPI-Bus** des Pi hängt — `lhpc` steuert den Chip selbst: kein LoRaWAN-Concentrator,
kein USB- oder seriell angebundenes Funkgerät, keine RNode-Firmware dazwischen.

Für drei Boards ist ein fertiges Hardware-Preset enthalten:

- **LoRaHAM Pi HAT** — das Dual-Modul-Board des [LoRaHAM-Projekts](https://loraham.de)
  (SX1278 für 433 MHz + RFM95 für 868 MHz).
- **Uputronics Raspberry Pi Zero LoRa Expansion Board** ([Uputronics](https://store.uputronics.com))
  — RFM95/RFM98; ein Board für ein Band, oder zwei gestapelte Boards für Dualband
  (CE0 = 433 MHz, CE1 = 868 MHz).
- **Waveshare SX1262 LoRaWAN/GNSS HAT**
  ([Waveshare](https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT)) — Varianten 433M und
  868M.

Andere Boards mit diesen Chips: das Preset wählen, dessen Verdrahtung passt
([Katalog](docs/cli.md#hardware)); validiert sind sie hier nicht.

Getestet auf **Pi Zero 2W** und **Pi 5**. On air: LoRaHAM Pi HAT, Uputronics-Dual-Stack, Waveshare
SX1262 433M; datierte Protokolle der Läufe auf dem LoRaHAM Pi HAT: `docs/live-tests/` (englisch).

## Stacks

<details><summary><em>Die neun Stacks — Bänder, was sie sind, und ihre Doku</em></summary>

| Stack | Band(s) | Lizenz | Was es ist | Doku |
|---|---|---|---|---|
| `daemon` | 433 + 868 | — | LoRaHAM-Daemon — besitzt die Funkgeräte, stellt pro Band Sockets bereit | [daemon](docs/stacks/daemon.md) |
| `chat` | 433 | ja | APRS-/Chat-TUI (lokal oder über SSH) | [chat](docs/stacks/chat.md) |
| `voice` | 433 / 868 | ja | LoRa-Sprache — GTK-App auf Desktop, ncurses-Terminal auf Lite | [voice](docs/stacks/voice.md) |
| `kiss` | 433 / 868 | ja | KISS-TNC über TCP (xastir, YAAC …) | [kiss](docs/stacks/kiss.md) |
| `graywolf` | über `kiss` | ja | Graywolf-APRS-Station (Web-UI, Digipeater, iGate) | [graywolf](docs/stacks/graywolf.md) |
| `meshtastic` | 433 / 868 | nein | Rootless `meshtasticd`, steuert das Funkgerät direkt | [meshtastic](docs/stacks/meshtastic.md) |
| `meshcom` | 433 | ja | MeshCom-Firmware in QEMU, an den Daemon gebrückt | [meshcom](docs/stacks/meshcom.md) |
| `meshcore` | 868 | nein | MeshCore auf openHop — Chat-Node (TCP 5000) und/oder Repeater (Dashboard :8000), per `mode` | [meshcore](docs/stacks/meshcore.md) |
| `reticulum` | 433 / 868 | nein | Reticulum-Node, steuert das Funkmodul direkt über SPI | [reticulum](docs/stacks/reticulum.md) |
</details>

⚠️ **Lizenz.** Die `ja`-Stacks brauchen eine Amateurfunklizenz, die `nein`-Stacks sind für den
lizenzfreien ISM-Betrieb; Band, Leistung, Duty-Cycle und Rufzeichen liegen in **deiner rechtlichen
Verantwortung**.

Daemon-gestützte Stacks starten den Daemon automatisch; Meshtastic und Reticulum steuern das
Funkgerät selbst und können sich kein Band mit dem Daemon teilen (`lhpc` verweigert den Konflikt).
Position (GPS) ist eine globale Quelle mit einem Schalter pro Stack: [GPS](docs/gps.md) (englisch).

## Installation

### Manuelle Installation

Von der frisch geflashten Karte zu laufenden Stacks, der Reihe nach.

#### 1. Karte vorbereiten

Raspberry Pi Imager: **Modell** wählen, **Raspberry Pi OS Lite (64-bit)**, und vor dem Flashen
**Hostname, Benutzername, WLAN + Land, SSH aktivieren** setzen.

<details><summary><em>Headless-Rettung — falls die Erstboot-Anpassung des Imagers nicht greift (wiederholt beobachtet)</em></summary>

Auf dem Pi, mit Tastatur und Bildschirm:

```bash
sudo rfkill unblock wifi
sudo raspi-config nonint do_wifi_country DE          # dein ISO-Ländercode
sudo nmcli device wifi connect "<SSID>" password "<PSK>"
sudo systemctl enable --now ssh
sudo hostnamectl set-hostname lhpc-zero              # dann /etc/hosts abgleichen:
echo "127.0.1.1 lhpc-zero" | sudo tee -a /etc/hosts
sudo sed -i 's/^# *\(en_US.UTF-8\)/\1/; s/^# *\(de_DE.UTF-8\)/\1/' /etc/locale.gen
sudo locale-gen && sudo update-locale
```
</details>

#### 2. Erster SSH-Login — mit tmux bei WLAN- und Headless-Betrieb

Auf deinem eigenen Rechner, um eine Sitzung auf dem Pi zu öffnen:

```bash
ssh <benutzer>@lhpc-zero.local   # Benutzer + Hostname aus dem Imager
```

Und dann auf dem Pi — alles ab hier bis zum Ende der Installation läuft dort:

```bash
sudo apt update && sudo apt install -y tmux
tmux new -s lhpc                 # alles Weitere in dieser Sitzung ausführen
#   abkoppeln: Strg-B, dann D
```

Im WLAN (allen voran beim Zero 2W) setzt die Verbindung unter Build-Last aus und trennt SSH; tmux
hält den Schritt am Laufen. Nach einem Abbruch:

```bash
ssh <benutzer>@lhpc-zero.local
tmux attach -t lhpc              # `tmux ls` listet die Sitzungen
```

`bootstrap-deps.sh` und `lhpc auto-install` lassen sich wiederholen (sie setzen wieder auf);
`install.sh` verweigert ein vorhandenes Checkout — antwortet
`~/loraham-pi-control/venv/lhpc/bin/lhpc --version`, nicht erneut ausführen.

#### 3. Prüfen, was installiert würde

Nur lesend, **ohne Root**. Exit 5 heißt, apt kann die Pakete nicht auflösen: `sudo apt-get update`
ausführen und wiederholen. Prüfungen und Exit-Codes: [deps](docs/cli.md#deps) (englisch).

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run
```

#### 4. Abhängigkeiten installieren

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

- **Root erforderlich** (`sudo bash …`); das Skript selbst ruft nie sudo auf.
- **`--spi-mode` ist Pflicht** — `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, inkl. dual) ·
  `hardware-cs` (Kernel-CE0/CE1) · `skip`. Welcher, und die optionalen Schalter:
  [deps](docs/cli.md#deps).
- **Über apt hinaus** deaktiviert es den Root-`nginx.service`
  ([warum](docs/webserver.md#first-time-bootstrap)) · legt auf Boards mit wenig RAM eine Swapdatei
  an und schaltet bei einer Installation über WLAN den WLAN-Stromsparmodus ab
  ([Running on a Pi](docs/maintenance.md#running-on-a-pi)) · installiert `polkitd` und zwei
  polkit-Regeln für Neustart/Herunterfahren und das Netzwerk-Panel des WebGUI · installiert
  `chrony`, `gpsd` und `fake-hwclock` und ersetzt damit `systemd-timesyncd`
  ([Uhr](docs/operations.md#clock)) · deaktiviert eine paketierte `meshtasticd.service`.

<details><summary><em>Manuell — nur installieren, was deine Stacks brauchen (bootstrap-deps.sh ist die Referenz; Vorschau mit <code>--dry-run</code>, Neuerzeugung mit <code>lhpc deps --script</code>)</em></summary>

<!-- test:deps-manual:start -->
```bash
# lhpc selbst + Fetch-/TLS-Werkzeuge (nginx nur, wenn du das WebGUI willst)
sudo apt install -y --no-install-recommends git python3 python3-venv python3-pip nftables nginx iw ca-certificates curl zstd
sudo apt install -y --no-install-recommends cmake liblgpio-dev build-essential          # daemon / RadioLib
sudo apt install -y --no-install-recommends libncurses-dev                              # chat / voice (Terminal)
sudo apt install -y --no-install-recommends libcodec2-dev libasound2-dev                # voice (ncurses-Terminal-UI — keine grafischen Pakete)
sudo apt install -y --no-install-recommends socat                                       # kiss
sudo apt install -y --no-install-recommends python3-libgpiod python3-spidev            # reticulum (direct-SPI radio, no compiler needed)
sudo apt install -y --no-install-recommends libssl-dev libslirp0 meson ninja-build libglib2.0-dev libpixman-1-dev libslirp-dev zlib1g-dev libgcrypt20-dev   # meshcom (Bridge + QEMU, headless aus dem Quellcode gebaut)
sudo apt install -y --no-install-recommends libyaml-cpp-dev libuv1-dev libgpiod-dev libi2c-dev libusb-1.0-0-dev libulfius-dev libbluetooth-dev pkg-config   # meshtastic (aus dem Quellcode gebaut)
sudo apt install -y --no-install-recommends libgtk-3-dev libx11-dev python3-dev        # nur mit --with-gui (Voice-GTK-App, Sideband)

sudo systemctl disable --now nginx.service               # Paket behalten, den ROOT-Dienst abschalten
# Boards mit wenig RAM (<600 MB): eine Swapdatei bewahrt die meshtasticd-/meshcom-Builds vor dem OOM-Kill
sudo fallocate -l 768M /var/swap.lhpc && sudo chmod 600 /var/swap.lhpc && sudo mkswap /var/swap.lhpc
echo '/var/swap.lhpc none swap sw,pri=10 0 0' | sudo tee -a /etc/fstab && sudo swapon -a
printf 'dtparam=spi=on\ndtoverlay=spi0-0cs\n' | sudo tee -a /boot/firmware/config.txt   # SPI-Overlay
sudo usermod -aG spi,gpio "$USER"                        # → greift mit dem Neustart in Schritt 6
```
<!-- test:deps-manual:end -->

Die polkit-Regeln (Neustart/Herunterfahren, Netzwerk-Panel) und die Uhr-Pakete stehen nicht in
dieser Liste: `lhpc doctor` (oder Apps → LoRaHAM Pi Control → System dependencies) gibt ihre Befehle
aus, solange sie fehlen.
</details>

#### 5. lhpc installieren

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   oder aus einem Checkout: ./install.sh
#   Optionen: --target <dir> · --no-service (ohne Web-Dienst) · --no-path (ohne CLI-Symlink)
```

Alles außer dem Link `~/.local/bin/lhpc` und den systemd-User-Units landet unter
`~/loraham-pi-control/` ([die Runtime-Wurzel](docs/architecture.md#the-runtime-root)).

<details><summary><em>Manuell — clone / venv / bootstrap</em></summary>

```bash
mkdir -p ~/loraham-pi-control/src
git clone https://github.com/makrohard/loraham-pi-control.git ~/loraham-pi-control/src/loraham-pi-control
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control
~/loraham-pi-control/venv/lhpc/bin/lhpc bootstrap --yes
export PATH="$HOME/loraham-pi-control/venv/lhpc/bin:$PATH"
```
</details>

#### 6. Neustart

Schaltet das SPI-Overlay, die `spi`-/`gpio`-Mitgliedschaft und den `PATH` mit `lhpc` scharf (ohne
ihn: `lhpc: command not found`).

```bash
sudo reboot
```

SSH neu verbinden und wieder `tmux new -s lhpc` starten.

#### 7. Konfigurieren

```bash
lhpc config operator --callsign YOURCALL  # optional; YOURCALL = dein Basis-Rufzeichen
lhpc hardware loraham                     # dein Funk-Setup aus dem Katalog:
```

<details><summary><em>Der Hardware-Katalog — jedes <code>lhpc hardware</code>-Setup</em></summary>

<!-- test:hw-table:start -->
| `lhpc hardware …` | Board(s) | Bänder → Daemon-Preset |
|---|---|---|
| `loraham` | LoRaHAM Dual-Modul (SX1278 + RFM95) | 433 → loraham, 868 → loraham |
| `uputronics` | Uputronics dual (CE0 433 + CE1 868) | 433 → uputronics-ce0, 868 → uputronics-ce1 |
| `uputronics-x` | Uputronics dual, crossed modules (CE0 868 + CE1 433) | 433 → uputronics-ce1, 868 → uputronics-ce0 |
| `uputronics-433` | Uputronics 433 (CE0) | 433 → uputronics-ce0 |
| `uputronics-868` | Uputronics 868 (CE1) | 868 → uputronics-ce1 |
| `waveshare-433` | Waveshare SX1262 (433) | 433 → waveshare-sx1262 |
| `waveshare-868` | Waveshare SX1262 (868) | 868 → waveshare-sx1262 |
<!-- test:hw-table:end -->

Uputronics: CE0 trägt 433, CE1 trägt 868 (`uputronics-x` für vertauschte Module).
</details>

`lhpc hardware` ohne Argument zeigt den Katalog. Welche Stacks das Rufzeichen erben:
[identity](docs/architecture.md#identity-and-callsigns) (englisch).

#### 8. Das WebGUI — und wie du es von woanders erreichst

Die Installation hat es gestartet: **`https://127.0.0.1:8443/`** — keine Anmeldung auf Loopback;
der Browser warnt wegen der selbstsignierten CA. Nach `--no-service`:

```bash
lhpc self-update --repair-integration && lhpc webserver init && lhpc webserver start-service   # Units anlegen, dann nur lokal, ohne Anmeldung
```

**Von einem anderen Rechner aus** fünf Schritte in dieser Reihenfolge (das Zertifikat zuerst:
stellst du die Richtlinie um, bevor dein Rechner eins hat, sperrst du dich aus). Mit
[Access Point](docs/wifi-access-point.md) gehören `10.42.0.0/24` und `10.42.0.1` überall dazu.
Zuerst das Datum prüfen (`timedatectl`; falls falsch:
`sudo date -u -s 'YYYY-MM-DD HH:MM' && sudo fake-hwclock save`); solange die Uhr nicht
synchronisiert ist, das Häkchen **Accept unverified clock** setzen
([clock gate](docs/webserver.md#the-clock-gate), englisch).

1. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → Certificates → Issue client cert**
   — die angezeigte Einmal-Passphrase kopieren.
2. **… → Stacks WebGUIs** — eine Richtlinie für alle Stack-Oberflächen (Access `lan`, Scheme
   `https`, Access mode `local-open-remote-auth`, Allowed CIDRs, Confirm `enable-remote`); bleibt leer,
   bis die Stacks installiert sind (Schritt 9).
3. **… → LHPC WebGUI** — dieselben Werte, mit **Bind** `0.0.0.0`; zuletzt, denn es ist die Seite,
   auf der du arbeitest. Die **IP SANs** müssen jede Adresse nennen, die du aufrufst (auf einer
   AP-Box `10.42.0.1` ergänzen), dann mit `lhpc webserver tls-renew` neu ausstellen: Apply stellt das
   Zertifikat nur neu aus, wenn die eigene LAN-Adresse der Box darin fehlt, nie für eine andere SAN.
4. **Die verwaltete Firewall anwenden** auf dem Pi: die zwei Befehle ausführen, die das Panel zeigt
   ([Firewall](docs/firewall.md#scenarios), englisch).
5. **Anwenden** (der Knopf, oder `lhpc webserver apply`).

Befehle, Zertifikat-Import, öffentliche oder anmeldefreie Freigabe:
[Runbook](docs/webserver.md#remote-exposure-runbook) (englisch). Nichts freigeben:
[SSH-Tunnel](docs/ssh-tunnel.md) (englisch).

#### 9. Stacks per Auto-Install aufsetzen (CLI)

- **Zero 2W / wenig RAM:** die CLI unten, mit gestoppter Konsole während der Builds
  ([Running on a Pi](docs/maintenance.md#running-on-a-pi), englisch).
- **Pi 5:** die **Auto-install**-Seite des WebGUI geht ebenso.

Auf dem Pi, in tmux:

```bash
tmux attach -t lhpc || tmux new -s lhpc   # auf dem Pi: die Sitzung aus Schritt 6, sonst eine neue
lhpc auto-install --yes
```

daemon, meshtastic und meshcom installieren standardmäßig ein vorgebautes Binary
([Binary-Kanal](docs/provenance.md#the-binary-channel)); der Rest baut aus dem Quellcode. Gemessen
auf einem Pi Zero 2W: 19 min für den Standardlauf (0.2.10), ≈ 5 h mit `--source pinned`. Ein
erneuter Lauf setzt am bereits Gebauten auf. Host-Tests und `--tx`: [auto-install](docs/cli.md#auto-install).

<details><summary><em>Stack für Stack statt alles auf einmal</em></summary>

```bash
# daemon — LoRaHAM-Daemon, besitzt die Funkgeräte (beide Bänder); standardmäßig Binary
lhpc install daemon
#   stattdessen aus Quellen:  lhpc install daemon --source pinned && lhpc build daemon

# chat — APRS-/Chat-TUI
lhpc install chat
lhpc build chat

# voice — LoRa-Sprache (Terminal-Variante baut headless; die GTK-App braucht --with-gui)
lhpc install voice
lhpc build voice

# kiss — KISS-TNC über TCP
lhpc install kiss
lhpc build kiss

# graywolf — APRS-Station (Digipeater + iGate, eigene Web-UI); funkt über den KISS-TNC,
#            kiss und daemon kommen also mit. Der Build-Schritt holt das gepinnte Upstream-.deb.
lhpc install graywolf
lhpc build graywolf

# meshtastic — standardmäßig Binary (kein Build-Schritt); Quellen: ≈ 2¾ h Zero 2W (gemessen)
lhpc install meshtastic
#   stattdessen aus Quellen:  lhpc install meshtastic --source pinned && lhpc build meshtastic

# meshcom — standardmäßig Binary (kein Build-Schritt); Quellen: ≈ 2 h Zero 2W (gemessen)
lhpc install meshcom
#   stattdessen aus Quellen:  lhpc install meshcom --source pinned && lhpc build meshcom

# meshcore — MeshCore auf openHop (Chat-Node, Repeater oder beides)
lhpc install meshcore
lhpc build meshcore

# reticulum — RNS-Knoten, funkt direkt über SPI, dazu NomadNet, LXMF und Sideband
#             (Sideband ist eine Desktop-App — sie braucht bootstrap-deps.sh --with-gui)
lhpc install reticulum
lhpc build reticulum
```

```bash
lhpc stack start <stack>
lhpc status
lhpc stack stop <stack>
```
</details>

Der emulierte MeshCom-Node bootet nach seinem Start noch minutenlang
([meshcom](docs/stacks/meshcom.md#notes), englisch). Jeder Schritt gibt
`[log] <Komponente> -> tail -f <Pfad>` aus; stiller Build, Speicher, abgebrochener Lauf:
[Running on a Pi](docs/maintenance.md#running-on-a-pi) (englisch).

#### 10. Stack-Logins — entstehen beim ersten Start eines Stacks

Ein Stack mit eigenem Login legt ihn beim **ersten Start** an (**Apps → *Stack* → Start**);
**Apps → *Stack* → Password** zeigt ihn dann mit Kopierknopf. Pro Stack:
[graywolf](docs/stacks/graywolf.md), [meshcore](docs/stacks/meshcore.md),
[meshcom](docs/stacks/meshcom.md); die Regel: [secrets and passwords](docs/operations.md#secrets-and-passwords)
(alle englisch).

## Stacks konfigurieren & betreiben

*Home* zeigt, was läuft, die Funkmodule und Links auf das Web-UI jedes Stacks; *Apps* hat eine Zeile
pro Stack: Einstellungen, Start/Stopp, Logs, Passwort. Die Konsole:
[operations](docs/operations.md#operating-the-console) (englisch).

<details><summary><em>Dasselbe auf der CLI</em></summary>

```bash
lhpc status                        # was läuft (nur lesend)
lhpc config <stack>                # Optionen des Stacks samt aktueller Werte
lhpc config chat call YOURCALL-10 # eine Option setzen (YOURCALL-10 = dein Rufzeichen+SSID)
lhpc config <stack> --band 868 <param> <wert>     # bandabhängiger Wert bei umschaltbaren Stacks
lhpc stack start|stop|restart <stack>             # zeigt den Plan, fragt nach; --yes überspringt
lhpc logs <ziel>                   # Komponenten-Log verfolgen
lhpc rflog <stack> [--band B]      # RF-Log eines Stacks verfolgen (was der Funk hörte und sendete); --band: Daemon
lhpc rflog <stack> --decrypt       # dasselbe, mit den Schlüsseln dieser Box entschlüsselt (verschlüsselte Stacks)
lhpc doctor                        # Umgebungs-/Abhängigkeits-Checks
lhpc test <stack> [--tx] --yes     # Host-Tests; --tx sendet
```

Vollständige Referenz: [`docs/cli.md`](docs/cli.md); die HF-Regeln: [TX safety](docs/operations.md#tx-safety).
</details>

## Danach

### WLAN-Access-Point

Eine Box mit einem `lhpc-ap`-NetworkManager-Profil bekommt das **Netzwerk**-Panel des WebGUI: ein
WLAN aus dem Browser beitreten, mit dem eigenen Access Point als Rückfallebene. Das Profil anlegen:
[`docs/wifi-access-point.md`](docs/wifi-access-point.md) (englisch).

### Autostart

Eine Standardinstallation startet das WebGUI beim Booten, und die Stacks, die vor einem Neustart
liefen, kommen zurück ([boot restore](docs/operations.md#not-a-supervisor)); Schalter:
`lhpc autostart on|off`.

### Aktualisieren

**Apps → LoRaHAM Pi Control → Update → Check for updates → Update now**, oder
`lhpc self-update --apply`. Sicherung und Mechanik: [self-update](docs/deployment.md#self-update) (englisch).

## Fehlerbehebung

| Symptom | Ursache | Abhilfe |
|---|---|---|
| `lhpc: command not found` nach der Installation | du bist auf deinem eigenen Rechner, nicht auf dem Pi — `lhpc` gibt es nur auf der Box | erst `ssh <benutzer>@<host>`, dann dort ausführen |
| `lhpc: command not found` auf dem Pi | PATH noch nicht wirksam | Neustart (Schritt 6), oder neue Login-Shell öffnen |
| Build wirkt hängend, wird per OOM abgeschossen, oder das Board fällt aus dem Netz | RAM- und WLAN-Druck auf kleinen Boards | [Running on a Pi](docs/maintenance.md#running-on-a-pi) (englisch) |
| „optional deps missing" im Headless-Betrieb | GUI-Komponenten absichtlich übersprungen | ignorieren, oder `--with-gui` |
| WebGUI von einem anderen Rechner nicht erreichbar | nicht freigegeben / Firewall | [Schritt 8](#8-das-webgui--und-wie-du-es-von-woanders-erreichst); [Firewall](docs/firewall.md) |
| SSH **während der Installation** abgerissen, Lauf gestoppt | der Lauf bekam SIGHUP; abgekoppelte Build-Schritte laufen ggf. weiter | `lhpc auto-install` erneut ausführen (setzt wieder auf); tmux (Schritt 2) oder ein USB-LAN-Adapter. Laufende Stacks überstehen einen Abbruch |
| Quell-Installation meldet „GitHub clone failed" | der Clone oder der Checkout des gepinnten Commits hat aufgegeben | Grund am Ende von `logs/adopt-<Komponente>.log` (`[fail] <Schritt>: …`); Installation erneut starten |
| `auto-install` verweigert den Start nach einem abgebrochenen Lauf | übrig gebliebene Lauf-Marker | wiederherstellen: [auto-install](docs/cli.md#auto-install) |

## Dokumentation

Alle Dokumente, nach Aufgabe gruppiert (englisch): [`docs/README.md`](docs/README.md).
