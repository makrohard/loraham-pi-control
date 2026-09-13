# LoRaHAM Pi Control (`lhpc`) — Anleitung (Deutsch)

**[The original English README](README.md)**

Die LoRa-Amateurfunk-Stacks auf einem Raspberry Pi von einer Stelle aus installieren,
konfigurieren und betreiben — über eine CLI und ein lokales WebGUI. `lhpc` installiert jeden
Stack aus dem Kanal deiner Wahl — festgepinnter Commit, Release oder Entwicklungsstand — oder als
geprüftes vorgebautes Binary, schreibt die Konfiguration jeder App aus einem einzigen Satz
Einstellungen, startet und stoppt sie in Abhängigkeitsreihenfolge und vergibt die Funkgeräte so, dass
sich nie zwei Stacks um ein Band streiten. Läuft rootless, unter der eigenen Benutzerkennung. Neun
Stacks: der LoRaHAM-Daemon mit Chat, Voice, KISS-TNC und Graywolf (APRS) sowie Meshtastic, MeshCom, MeshCore und
Reticulum — auf einem Pi Zero 2W oder Pi 5.

- **Du hast einen Pi mit passendem [LoRa-HAT](#hardware)?**

  > **Der einfachste Weg zu einer laufenden Box ist ein fertiges Image.** Es liefert Raspberry Pi
  > OS mit fertig installiertem und gebautem LHPC samt allen Stacks, und die dortige README führt
  > in zwölf kurzen Schritten durch die komplette Einrichtung, auf Deutsch und Englisch. Der erste
  > Start des Images konfiguriert die Box selbst und liest optional eine `lhpc-config.txt` von der
  > Boot-Partition. Das ist der normale Weg; alles auf dieser Seite ist für den Selbstbau.
  >
  > ## → [Fertiges Image holen](https://github.com/makrohard/loraham-images)

- **Kein Pi zur Hand? Zwei Wege ganz ohne Hardware**

  > [![Live-Demo](https://img.shields.io/badge/%E2%96%B6%20Live--Demo-im%20Browser-2ea44f)](https://makrohard.github.io/loraham-pi-control/)
  > — das echte WebGUI direkt im Browser. Nichts zu installieren, **keine Anmeldung**, für alle
  > nutzbar (simuliert, via Pyodide).
  >
  > [![In GitHub Codespaces öffnen](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)
  > — das vollständige Test-Lab mit echten Stack-Prozessen auf simulierter Hardware. **Erfordert die
  > Anmeldung mit einem (kostenlosen) GitHub-Konto.** Siehe [`docs/testlab.md`](docs/testlab.md)
  > (englisch).

> Maßgeblich ist die englische [`README.md`](README.md); diese Übersetzung kann hinterherhinken.
> Code, Oberflächentexte und die übrigen Dokumente sind auf Englisch.

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

Andere Boards mit diesen Chips sollten funktionieren — das Preset wählen, dessen Verdrahtung passt
(`lhpc hardware`, [Katalog](docs/cli.md#hardware)) — validiert sind sie hier aber nicht.

Getestet auf **Pi Zero 2W** und **Pi 5**. On air: LoRaHAM Pi HAT (Dual-Modul-Controller),
Uputronics-Dual-Stack, Waveshare SX1262 433M; datierte Nachweise der Läufe auf dem LoRaHAM Pi HAT
liegen in `docs/live-tests/` (englisch).

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

⚠️ **Lizenz.** Die `ja`-Stacks sind Amateurfunk und brauchen eine Lizenz; die `nein`-Stacks sind für
den lizenzfreien ISM-Betrieb gedacht. **Für den rechtmäßigen Betrieb bist du verantwortlich** —
Band, Sendeleistung und Duty-Cycle unterscheiden sich je nach Land, und das gesendete Rufzeichen ist
deines. Gesendet wird erst, wenn du Hardware wählst und einen Stack startest.

Daemon-gestützte Stacks starten den Daemon automatisch; Meshtastic und Reticulum steuern das
Funkgerät selbst und können sich kein Band mit dem Daemon teilen (`lhpc` blockiert den Konflikt).

**Position (GPS)** ist eine globale Einstellung für alle Stacks, die sie nutzen können — ein gpsd
auf diesem oder einem anderen Rechner, ein direkt gelesener Empfänger oder eine feste Position.
Jeder Stack hat zusätzlich seinen eigenen Schalter: `lhpc gps --source gpsd`, dann
`lhpc config meshtastic use_gps on`. Siehe [GPS](docs/gps.md).

## Installation

### Manuelle Installation

Von der frisch geflashten Karte zu laufenden Stacks. Die Schritte laufen der Reihe nach.

#### 1. Karte vorbereiten

Raspberry Pi Imager: **Modell** wählen, **Raspberry Pi OS Lite (64-bit)**, und vor dem Flashen
**Hostname, Benutzername, WLAN + Land, SSH aktivieren** setzen.

<details><summary><em>Headless-Rettung — falls die Erstboot-Anpassung des Imagers nicht greift (wiederholt beobachtet)</em></summary>

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

Auf einem headless betriebenen Pi im WLAN (allen voran dem Zero 2W) setzt die Verbindung unter
Build-Last aus, und eine nackte SSH-Sitzung reißt dabei ab; tmux hält den Schritt am Laufen. Nach
einem Abbruch neu verbinden und wieder ankoppeln:

```bash
ssh <benutzer>@lhpc-zero.local
tmux attach -t lhpc              # `tmux ls` listet die Sitzungen
```

`bootstrap-deps.sh` und `lhpc auto-install` lassen sich beliebig oft wiederholen (sie setzen am
Cache wieder auf); `install.sh` ist ein Erstinstaller und verweigert ein vorhandenes Checkout — vor
einem erneuten Lauf prüfen, ob `~/loraham-pi-control/venv/lhpc/bin/lhpc --version` antwortet.

#### 3. Prüfen, was installiert würde

Reine Vorschau, bewusst **ohne Root**: Erst prüfen, was das Skript installieren will, dann Rechte
gewähren (alles Weitere verlangt `sudo`). Was geprüft wird und die Exit-Codes:
[deps](docs/cli.md#deps) (englisch).

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run
```

#### 4. Abhängigkeiten installieren

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

- **Root erforderlich** — genau wie gezeigt ausführen (`sudo bash …`); ohne Root bricht das Skript
  sofort ab. Selbst ruft es **nie sudo auf** und läuft damit auch unbeaufsichtigt oder ganz ohne sudo.
- **`--spi-mode` ist Pflicht** — `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, inkl. dual) ·
  `hardware-cs` (Kernel-CE0/CE1) · `skip` ([welcher, und warum](docs/cli.md#deps)).
- **Optionale Schalter**: [deps](docs/cli.md#deps).
- **Über apt hinaus** — deaktiviert den Root-`nginx.service`
  ([warum](docs/webserver.md#first-time-bootstrap)) · legt auf Boards mit wenig RAM eine Swapdatei
  an und schaltet den WLAN-Stromsparmodus ab, wenn die Installation über WLAN läuft (der Rückweg
  wird als Warnung ausgegeben), beides für die langen Builds
  ([Running on a Pi](docs/maintenance.md#running-on-a-pi)) · installiert zwei polkit-Regeln (und das Paket `polkitd`), damit die WebGUI-Schaltflächen
  Neustart/Herunterfahren und sein Netzwerk-Panel autorisiert sind (`--no-power-controls` /
  `--no-network-controls` lassen sie weg) · schaltet ein persistentes Journal ein
  (`/var/log/journal`) · deaktiviert eine paketierte `meshtasticd.service`, falls vorhanden.

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

Für Neustart/Herunterfahren und das Netzwerk-Panel des WebGUI braucht es zusätzlich `polkitd` plus
zwei polkit-Regeln — `lhpc deps` (oder Apps → LoRaHAM Pi Control → Dependencies) zeigt die genauen
Befehle.
</details>

#### 5. lhpc installieren

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   oder aus einem Checkout: ./install.sh
#   Optionen: --target <dir> · --no-service (ohne Web-Dienst) · --no-path (ohne CLI-Symlink)
```

Alles landet unter `~/loraham-pi-control/` ([die Runtime-Wurzel](docs/architecture.md#the-runtime-root)).

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

Ein Neustart, der alles auf einmal scharf schaltet: das SPI-Overlay und deine neue
`spi`-/`gpio`-Mitgliedschaft aus Schritt 4 (gebraucht erst, sobald ein Stack ans Funkgerät geht —
genau das kommt als Nächstes) sowie den `PATH` mit `lhpc` darauf. Ohne Neustart scheitert der
nächste Befehl mit `lhpc: command not found`.

```bash
sudo reboot
```

Danach SSH neu verbinden (und für die folgenden Schritte wieder `tmux new -s lhpc` starten).

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

Die Uputronics-Chip-Selects folgen der Stapel-Konvention oben (CE0 trägt 433, CE1 trägt 868).
</details>

`lhpc hardware` ohne Argument zeigt den Katalog ([hardware](docs/cli.md#hardware)). Welche Stacks
das Basis-Rufzeichen erben und was Meshtastic/MeshCore stattdessen brauchen:
[identity](docs/architecture.md#identity-and-callsigns) (englisch).

#### 8. Das WebGUI — und wie du es von woanders erreichst

Die Installation hat sie bereits gestartet: **`https://127.0.0.1:8443/`** — lokaler Zugriff ist
offen (keine Anmeldung auf Loopback; die Browser-Warnung zur selbstsignierten CA ist erwartbar).
Falls übersprungen (`--no-service`):

```bash
lhpc self-update --repair-integration && lhpc webserver init && lhpc webserver start-service   # Units anlegen, dann nur lokal, ohne Anmeldung
```

**Von einem anderen Rechner aus** sind es fünf Schritte, in dieser Reihenfolge. Das Zertifikat kommt
zuerst: Die Remote-Modi verlangen eins, und stellst du die Richtlinie um, bevor dein eigener Rechner
es hat, sperrst du dich aus.

1. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → Certificates → Issue client cert**<br>
   Stellt das Zertifikat aus und zeigt seine Einmal-Passphrase — sofort kopieren. Derselbe
   Abschnitt bietet Kopierfelder, um `.p12` und Server-CA auf den eigenen Rechner zu holen.
   Verlorene oder abhandengekommene Passphrasen, die Befehle und der Import in Browser oder Handy:
   [Zertifikate](docs/webserver.md#certificates-and-the-two-ca-pki) (englisch).

2. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → Stacks WebGUIs**<br>
   Eine Richtlinie für alle Stack-Oberflächen ([Details](docs/webserver.md#stack-web-ui-proxies)):
   Access `lan`, Scheme `https` (http erzwingt no-auth), Access mode `local-open-remote-auth`,
   Allowed CIDRs — das Netz, aus dem du kommst, z. B. `192.168.1.0/24`, für lan und public
   Pflicht — und Confirm `enable-remote` (`enable-remote-danger` für einen öffentlichen oder
   unauthentifizierten Listener). Dieser Teil bleibt leer, bis die Stacks installiert sind
   (Schritt 9) — dann noch einmal herkommen.

3. **Apps → LoRaHAM Pi Control → Webserver (HTTPS / mTLS) → LHPC WebGUI**<br>
   Das WebGUI selbst, und zuletzt, denn es ist die Seite, auf der du gerade arbeitest. Dieselben
   Werte, nur hat sie **Bind** statt Access: `0.0.0.0`, damit sie über Loopback hinaus lauscht.

4. **Die verwaltete Firewall anwenden — auf dem Pi.** Die zwei Befehle ausführen, die das Panel
   zeigt — das Root-Skript, dann das `lhpc webserver apply`, das die gesperrten Listener aktiviert
   ([die verwaltete Firewall](docs/firewall.md#the-managed-firewall-one-command), englisch).

5. **Anwenden.** Der **Apply**-Knopf im WebGUI, oder `lhpc webserver apply` auf dem Pi: prüft und
   aktiviert die Listener ([applying changes](docs/webserver.md#applying-changes-and-recovery)).

Dieselben fünf Schritte aus der Shell, die genauen Pfade, der Import in Browser und Handy,
öffentliche oder anmeldefreie Freigabe: [Runbook](docs/webserver.md#remote-exposure-runbook)
(englisch).

*Alternative, wenn du gar nichts freigeben willst:* Ein [SSH-Tunnel](docs/ssh-tunnel.md)
(englisch) holt das WebGUI und jede Stack-Oberfläche auf deinen Rechner, ohne einen zusätzlichen
Listener.

#### 9. Stacks per Auto-Install aufsetzen (CLI)

- **Kleine Systeme (Zero 2W / wenig RAM):** die CLI unten nutzen und die Konsole während der
  Builds stoppen ([Running on a Pi](docs/maintenance.md#running-on-a-pi), englisch).
- **Pi 5 / Desktop-Klasse:** hier fällt die Last des WebGUI nicht ins Gewicht — nutzen: die
  **Auto-install**-Seite, danach im Webserver-Panel die fünf Schritte von oben.

Auf dem Pi ausführen (innerhalb der SSH-Sitzung — nicht auf deinem Desktop), und in tmux:

```bash
tmux attach -t lhpc || tmux new -s lhpc   # auf dem Pi: die Sitzung aus Schritt 6, sonst eine neue
lhpc auto-install --yes
```

Die drei lange kompilierenden Stacks (daemon, meshtastic, meshcom) installieren standardmäßig ein
vorkompiliertes Binary ([Provenienz](docs/provenance.md#the-binary-channel); im Betrieb:
[Installationskanäle](docs/operations.md#install-channels)); der Rest baut in je wenigen Minuten aus
dem Quellcode. Der komplette Standardlauf wurde auf einem Pi Zero 2W mit 19 min gemessen, bei
0.2.10; alles aus Quellen (`--source pinned`) dauert dort ≈ 5 h, die Summe der gemessenen
Stack-Builds. Host-Tests und `--tx`: [auto-install](docs/cli.md#auto-install). Build-Artefakte
bleiben erhalten — ein erneuter Lauf setzt am bereits Gebauten auf. Warnungen über fehlende
optionale Abhängigkeiten sind im Headless-Betrieb normal.

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

Nach `lhpc stack start meshcom` bootet der emulierte Node selbst noch minutenlang; was normal
ist: [meshcom](docs/stacks/meshcom.md#notes) (englisch).

**Fortschritt beobachten.** `lhpc` gibt pro Schritt ein kopierbares
`[log] <Komponente> -> tail -f <Pfad>` aus. Wie man ein stilles Log beurteilt, die Speichergrenze
kleiner Boards und das Aufräumen eines abgebrochenen Laufs:
[Running on a Pi](docs/maintenance.md#running-on-a-pi) (englisch).

#### 10. Stack-Logins — entstehen beim ersten Start eines Stacks

Ein Stack mit eigenem Login legt ihn beim **ersten Start** an, danach zeigt das WebGUI den Wert.
Einmal starten genügt; einloggen musst du dich noch nicht.

- **Apps → *Stack* → Start**<br>
  Der Login entsteht während dieses ersten Starts. Zu lesen gibt es noch nichts.

- **Apps → *Stack* → Password**<br>
  Konto und Passwort, mit Kopierknopf. Den Abschnitt gibt es nur bei Stacks, die einen Login haben,
  und erst wenn er existiert — MeshCore sagt genau das, solange kein Repeater-Modus lief.

Wo jeder Stack seinen Login ablegt: [graywolf](docs/stacks/graywolf.md), [meshcore](docs/stacks/meshcore.md),
[meshcom](docs/stacks/meshcom.md); die Regel: [secrets and passwords](docs/operations.md#secrets-and-passwords)
(alle englisch).

## Stacks konfigurieren & betreiben

**Auf Home starten, auf Apps konfigurieren.** *Home* ist die Übersicht — was läuft, die Funkmodule
und ihre Bänder, und je ein Link auf das eigene Web-UI jedes laufenden Stacks. *Apps* ist die
Arbeitsseite: eine Zeile pro Stack mit Einstellungen, Start und Stopp, Logs und dem
Passwort-Abschnitt. Install, Update, Clean, Strom, WLAN und Self-Update zeigen einen Plan und
fragen nach; ein normaler Start läuft sofort und fragt nur, wenn er einen anderen Stack stoppen
müsste; Einstellungen werden beim ersten Klick gespeichert.

<details><summary><em>Dasselbe auf der CLI</em></summary>

```bash
lhpc status                        # was läuft (nur lesend)
lhpc config <stack>                # Optionen des Stacks samt aktueller Werte
lhpc config chat call YOURCALL-10 # eine Option setzen (YOURCALL-10 = dein Rufzeichen+SSID)
lhpc config <stack> --band 868 <param> <wert>     # bandabhängiger Wert bei umschaltbaren Stacks
lhpc stack start|stop|restart <stack>             # zeigt den Plan, fragt nach; --yes überspringt
lhpc logs <ziel>                   # Komponenten-Log verfolgen
lhpc doctor                        # Umgebungs-/Abhängigkeits-Checks
lhpc test <stack> [--tx] --yes     # Host-Tests; --tx sendet
```

Vollständige Referenz: [`docs/cli.md`](docs/cli.md); die HF-Regeln: [TX safety](docs/operations.md#tx-safety).
</details>

## Danach

### WLAN-Access-Point

Das WebGUI von einem anderen Rechner erreichen ist
[Schritt 8](#8-das-webgui--und-wie-du-es-von-woanders-erreichst) — Zertifikat, Richtlinie,
Firewall, Anwenden. Tiefer: [`docs/webserver.md`](docs/webserver.md),
[`docs/firewall.md`](docs/firewall.md) und [`docs/ssh-tunnel.md`](docs/ssh-tunnel.md), um alles
allein über SSH zu erreichen, ohne etwas freizugeben (alle englisch).

Ein Board mit einem `lhpc-ap`-NetworkManager-Profil bekommt das **Netzwerk**-Panel des WebGUI: ein
WLAN aus dem Browser beitreten, mit dem eigenen Access Point als Rückfallebene, wenn dieses WLAN
außer Reichweite ist.

Welches Image das Profil mitbringt und wie du es auf einer Desktop-Box oder einer manuellen
Installation von Hand anlegst: [`docs/wifi-access-point.md`](docs/wifi-access-point.md) (englisch).

### Autostart

Die Installation aktiviert das WebGUI beim Booten; Stacks, die vor einem Neustart liefen, werden
wiederhergestellt ([boot restore](docs/operations.md#not-a-supervisor)). Der Schalter:
`lhpc autostart on|off` ([CLI](docs/cli.md#autostart)).

### Aktualisieren

**Apps → LoRaHAM Pi Control (die erste Zeile) → Update → Check for updates → Update now**, oder
`lhpc self-update --apply` aus einer Operator-Shell. Vorher sichern, die Mechanik und
`--repair-integration`: [self-update](docs/deployment.md#self-update) (englisch).

## Fehlerbehebung

| Symptom | Ursache | Abhilfe |
|---|---|---|
| `lhpc: command not found` nach der Installation | du bist auf deinem eigenen Rechner, nicht auf dem Pi — `lhpc` gibt es nur auf der Box | erst `ssh <benutzer>@<host>`, dann dort ausführen |
| `lhpc: command not found` auf dem Pi | PATH noch nicht wirksam | Neustart (Schritt 6), oder neue Login-Shell öffnen |
| Build wirkt hängend, wird per OOM abgeschossen, oder das Board fällt aus dem Netz | RAM- und WLAN-Druck auf kleinen Boards | [Running on a Pi](docs/maintenance.md#running-on-a-pi) (englisch) |
| „optionale Abhängigkeiten fehlen" im Headless-Betrieb | GUI-Komponenten absichtlich übersprungen | ignorieren, oder `--with-gui` |
| WebGUI von einem anderen Rechner nicht erreichbar | nicht freigegeben / Firewall | [Schritt 8](#8-das-webgui--und-wie-du-es-von-woanders-erreichst); [Firewall](docs/firewall.md) |
| SSH **während der Installation** abgerissen, Lauf gestoppt | Orchestrator bekam SIGHUP; abgekoppelte Build-Schritte laufen ggf. weiter | `lhpc auto-install` erneut ausführen (setzt am Cache auf); tmux nutzen (Schritt 2). **Betrifft nur die Installation** — laufende Stacks hängen an systemd bzw. laufen abgekoppelt und überstehen WLAN-Abbrüche; im Normalbetrieb ist danach nichts neu zu installieren. Auf einem Zero 2W umgeht ein USB-LAN-Adapter das Problem bei der Installation ganz |
| Quell-Installation meldet „GitHub clone failed" | der Clone — oder ein Schritt danach (Checkout des gepinnten Commits) — hat aufgegeben | der Grund steht am Ende von `logs/adopt-<Komponente>.log` (`[fail] <Schritt>: …`); Installation erneut starten, eine langsame Leitung wird nicht gemerkt |
| `auto-install` verweigert den Start nach einem abgebrochenen Lauf | übrig gebliebene Lauf-Marker | wiederherstellen: [auto-install](docs/cli.md#auto-install) |

## Dokumentation

Alle Dokumente sind auf Englisch.

| Gruppe | Doku |
|---|---|
| Verstehen | [Architektur](docs/architecture.md) |
| Betreiben | [CLI](docs/cli.md) · [Betrieb](docs/operations.md) · [GPS](docs/gps.md) · [Wartung](docs/maintenance.md) · [Backlog](docs/backlog.md) |
| Erreichen | [Deployment](docs/deployment.md) · [Webserver (HTTPS + mTLS)](docs/webserver.md) · [SSH-Tunnel](docs/ssh-tunnel.md) · [WLAN-Access-Point](docs/wifi-access-point.md) · [Firewall](docs/firewall.md) |
| Stacks | [Stack hinzufügen](docs/adding-a-stack.md) · [daemon](docs/stacks/daemon.md) · [kiss](docs/stacks/kiss.md) · [graywolf](docs/stacks/graywolf.md) · [chat](docs/stacks/chat.md) · [meshcore](docs/stacks/meshcore.md) · [meshcom](docs/stacks/meshcom.md) · [meshtastic](docs/stacks/meshtastic.md) · [reticulum](docs/stacks/reticulum.md) · [voice](docs/stacks/voice.md) |
| Prüfen | [Test-Matrix](docs/test-matrix.md) · [Test-Lab](docs/testlab.md) |
| Richtlinien | [Provenienz](docs/provenance.md) |

Gesamtindex: [`docs/README.md`](docs/README.md).
