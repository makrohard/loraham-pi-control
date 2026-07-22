# LoRaHAM Pi Control (`lhpc`) — Anleitung (Deutsch)

Die LoRa-Amateurfunk-Software-Stacks auf einem Raspberry Pi von einer Stelle aus installieren,
konfigurieren und betreiben — über eine CLI und eine lokale Web-Konsole. `lhpc` übernimmt den
Quellcode jedes Stacks, baut ihn, startet/stoppt ihn in Abhängigkeitsreihenfolge, erzwingt einen
Stack pro Funkband und schreibt die Konfiguration jeder App. Für Betreiber, die eine LoRaHAM- /
Meshtastic- / MeshCom- / MeshCore-Box auf einem Pi Zero 2W oder Pi 5 aufsetzen.

> Maßgeblich ist die englische [`README.md`](README.md); diese Übersetzung kann hinterherhinken.
> Code, Oberflächentexte und die übrigen Dokumente sind auf Englisch.

## Inhalt

- [Überblick](#überblick) — [Stacks](#stacks) · [Hardware](#hardware) · [Nicht enthalten](#nicht-enthalten)
- [Installation](#installation) — von der frisch geflashten Karte zu laufenden Stacks (Schritte 0–8)
- [Stacks konfigurieren & betreiben](#stacks-konfigurieren--betreiben) · [Fernzugriff](#fernzugriff) · [Autostart](#autostart) · [Aktualisieren](#aktualisieren)
- [Fehlerbehebung](#fehlerbehebung) · [Dokumentation](#dokumentation)

## Überblick

### Stacks

| Stack | Band | Was es ist | Doku |
|---|---|---|---|
| `daemon` | 433 + 868 | LoRaHAM-Daemon — besitzt die Funkgeräte, stellt pro Band Sockets bereit | [daemon](docs/stacks/daemon.md) |
| `chat` | 433 | APRS-/Chat-TUI (lokal oder über SSH) | [aprs](docs/stacks/aprs.md) |
| `igate` | 433 | APRS-iGate | [aprs](docs/stacks/aprs.md) |
| `voice` | 433 / 868 | LoRa-Sprache (GUI) | [voice](docs/stacks/voice.md) |
| `kiss` | 433 / 868 | KISS-TNC über TCP (xastir, YAAC …) | [kiss](docs/stacks/kiss.md) |
| `meshtastic` | 433 / 868 | Rootless `meshtasticd`, steuert das Funkgerät direkt | [meshtastic](docs/stacks/meshtastic.md) |
| `meshcom` | 433 | MeshCom-Firmware in QEMU, an den Daemon gebrückt | [meshcom](docs/stacks/meshcom.md) |
| `meshcore` | 868 | MeshCore-Pi-Node (TCP 5000) | [meshcore](docs/stacks/meshcore.md) |

Daemon-gestützte Stacks starten den Daemon automatisch; Meshtastic steuert das Funkgerät selbst und
kann sich kein Band mit dem Daemon teilen (`lhpc` blockiert den Konflikt).

### Hardware

Getestete Boards, auf Pi **Zero 2W** und **Pi 5** (andere SX127x-/SX1262-SPI-Boards sollten
funktionieren, sind aber nicht validiert):

- **LoRaHAM Pi HAT** — das Dual-Modul-Board des [LoRaHAM-Projekts](https://loraham.de)
  (SX1278 für 433 MHz + RFM95 für 868 MHz).
- **Uputronics Raspberry Pi Zero LoRa Expansion Board** ([Uputronics](https://store.uputronics.com))
  — ein Board für ein Band, oder zwei gestapelte Boards für Dualband (CE0 = 433 MHz, CE1 = 868 MHz).
- **Waveshare SX1262 LoRaWAN/GNSS HAT**
  ([Waveshare](https://www.waveshare.com/wiki/SX1262_XXXM_LoRaWAN/GNSS_HAT)) — Varianten 433M und
  868M; 868 ist noch nicht on-air-validiert.

**SPI-Modus:** `soft-cs` (`dtparam=spi=on` + `dtoverlay=spi0-0cs`) deckt LoRaHAM Pi / Uputronics /
Waveshare ab (inkl. dual, Chip-Selects als GPIOs); `hardware-cs` nur für kernelgesteuerte CE0/CE1.

### Nicht enthalten

- **Keine Firewall-Verwaltung** — `lhpc` schützt nur seine eigene Konsole; von einem Stack geöffnete Ports schließt du selbst ([Firewall](docs/firewall.md)).
- **Es wird nie ein GUI/Desktop installiert** — nur GUI-*Anwendungs*-Bibliotheken, und nur mit `--with-gui`.
- **Lizenz & Sendebetrieb** bleiben in der Verantwortung des Betreibers — HF wird nie automatisch gesendet.

## Installation

Von der frisch geflashten Karte zu laufenden Stacks. Die Schritte laufen der Reihe nach.

### 0. tmux — dein Sicherheitsnetz über SSH

Das WLAN eines Pi Zero 2W setzt unter Build-Last kurz aus: Die Verbindung hängt sekundenweise,
`sshd` antwortet nicht mehr, das Board arbeitet aber weiter — ein langer Schritt in einer nackten
SSH-Sitzung reißt dabei ab. Darum alles in `tmux` laufen lassen; nach einem Abbruch verbindest du
dich einfach neu und koppelst wieder an:

```bash
sudo apt install -y tmux     # gleich nach dem ersten SSH-Login
tmux new -s lhpc             # alles Weitere in dieser Sitzung ausführen
#   abkoppeln: Strg-B, dann D  ·  nach einem Abbruch: SSH neu verbinden, dann  tmux attach -t lhpc
```

Wichtig ist das bei den Schritten **3** (Abhängigkeiten), **4** (lhpc installieren) und **8**
(`auto-install` — die langen Builds). Erwischt es dich doch einmal außerhalb von tmux: neu
verbinden und den Schritt wiederholen — jeder Schritt ist idempotent und setzt am Cache wieder auf.

### 1. Karte vorbereiten

Raspberry Pi Imager: **Modell** wählen, **Raspberry Pi OS Lite (64-bit)**, und vor dem Flashen
**Hostname, Benutzername, WLAN + Land, SSH aktivieren** setzen.

<details><summary>Headless-Rettung — falls die Erstboot-Anpassung des Imagers nicht greift (wiederholt beobachtet)</summary>

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

### 2. Prüfen, was installiert würde

Reine Vorschau — löst die Paketliste eines frischen Images auf und **bricht ab**, sobald etwas
Grafisches hereingezogen würde. Ändert nichts und braucht bewusst **kein Root**: Erst prüfen, was
das Skript installieren will, dann Rechte gewähren (alles Weitere verlangt `sudo`).

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run
```

### 3. Abhängigkeiten installieren

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

- **Root erforderlich** — genau wie gezeigt ausführen (`sudo bash …`); ohne Root bricht das Skript
  sofort ab. Selbst ruft es **nie sudo auf** und läuft damit auch unbeaufsichtigt oder ganz ohne sudo.
- **`--spi-mode` ist Pflicht** — `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, inkl. dual) ·
  `hardware-cs` (Kernel-CE0/CE1) · `skip`.
- **Optionale Schalter** — `--with-gui` (GUI-Anwendungs-Bibliotheken) · `--no-swapfile` ·
  `--swap-size <MB>` (Standard 768) · `--operator-user <name>` (bei Ausführung als root) ·
  `--keep-wifi-powersave`.
- **Über apt hinaus** — deaktiviert den System-`nginx.service` (das Paket bleibt; `lhpc` nutzt eine
  eigene rootlose Unit) · legt auf Boards unter ~600 MB RAM `/var/swap.lhpc` an (768 MB, unter
  zram) als OOM-Reserve für die langen Builds · schaltet den WLAN-Stromsparmodus ab, aber **nur
  wenn die Installation tatsächlich über WLAN läuft** (das WLAN eines Zero 2W reißt unter
  Dauerlast ab; über LAN bleibt das WLAN unangetastet, der Rückweg wird als Warnung ausgegeben).

<details><summary>Manuell — nur installieren, was deine Stacks brauchen (bootstrap-deps.sh ist die Referenz; Vorschau mit <code>--dry-run</code>, Neuerzeugung mit <code>lhpc deps --script</code>)</summary>

<!-- test:deps-manual:start -->
```bash
# lhpc selbst + Fetch-/TLS-Werkzeuge (nginx nur, wenn du die Web-Konsole willst)
sudo apt install -y --no-install-recommends git python3 python3-venv python3-pip nginx ca-certificates curl
sudo apt install -y --no-install-recommends cmake liblgpio-dev build-essential          # daemon / RadioLib
sudo apt install -y --no-install-recommends libncurses-dev                              # chat / igate
sudo apt install -y --no-install-recommends socat                                       # kiss
sudo apt install -y --no-install-recommends libssl-dev libslirp0 meson ninja-build libglib2.0-dev libpixman-1-dev libslirp-dev zlib1g-dev libgcrypt20-dev   # meshcom (Bridge + QEMU, headless aus dem Quellcode gebaut)
sudo apt install -y --no-install-recommends libyaml-cpp-dev libuv1-dev libgpiod-dev libi2c-dev libusb-1.0-0-dev libulfius-dev libbluetooth-dev pkg-config   # meshtastic (aus dem Quellcode gebaut)
sudo apt install -y --no-install-recommends libcodec2-dev libgtk-3-dev libasound2-dev python3-tk           # nur mit --with-gui (Voice, MeshCore Node Manager)

sudo systemctl disable --now nginx.service               # Paket behalten, den ROOT-Dienst abschalten
# Boards mit wenig RAM (<600 MB): eine Swapdatei bewahrt die meshtasticd-/meshcom-Builds vor dem OOM-Kill
sudo fallocate -l 768M /var/swap.lhpc && sudo chmod 600 /var/swap.lhpc && sudo mkswap /var/swap.lhpc
echo '/var/swap.lhpc none swap sw,pri=-2 0 0' | sudo tee -a /etc/fstab && sudo swapon -a
printf 'dtparam=spi=on\ndtoverlay=spi0-0cs\n' | sudo tee -a /boot/firmware/config.txt   # SPI-Overlay
sudo usermod -aG spi,gpio "$USER"                        # → greift mit dem Neustart in Schritt 5
```
<!-- test:deps-manual:end -->
</details>

### 4. lhpc installieren

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   oder aus einem Checkout: ./install.sh
#   Optionen: --target <dir> · --no-service (ohne Web-Dienst) · --no-path (ohne CLI-Symlink)
```

Alles landet unter `~/loraham-pi-control/`: der lhpc-Checkout in `src/loraham-pi-control`, das venv
in `venv/lhpc`, Einstellungen/Geheimnisse/Zertifikate unter `config/`.

<details><summary>Manuell — clone / venv / bootstrap</summary>

```bash
mkdir -p ~/loraham-pi-control/src
git clone https://github.com/makrohard/loraham-pi-control.git ~/loraham-pi-control/src/loraham-pi-control
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control
~/loraham-pi-control/venv/lhpc/bin/lhpc bootstrap --yes
export PATH="$HOME/loraham-pi-control/venv/lhpc/bin:$PATH"
```
</details>

### 5. Neustart

Ein Neustart, der alles auf einmal scharf schaltet: das SPI-Overlay und deine neue
`spi`-/`gpio`-Mitgliedschaft aus Schritt 3 (gebraucht erst, sobald ein Stack ans Funkgerät geht —
genau das kommt als Nächstes) sowie den `PATH` mit `lhpc` darauf. Ohne Neustart scheitert der
nächste Befehl mit `lhpc: command not found`.

```bash
sudo reboot
```

Danach SSH neu verbinden (und für die folgenden Schritte wieder `tmux new -s lhpc` starten).

### 6. Konfigurieren

```bash
lhpc config operator --callsign W1ABC     # dein Rufzeichen (erben alle lizenzpflichtigen Stacks)
lhpc hardware loraham                     # dein Funk-Setup aus dem Katalog:
```

<!-- test:hw-table:start -->
| `lhpc hardware …` | Board(s) | Bänder → Daemon-Preset |
|---|---|---|
| `loraham` | LoRaHAM Dual-Modul (SX1278 + RFM95) | 433 → loraham, 868 → loraham |
| `uputronics` | Uputronics dual (CE0 433 + CE1 868) | 433 → uputronics-ce0, 868 → uputronics-ce1 |
| `uputronics-433` | Uputronics 433 (CE0) | 433 → uputronics-ce0 |
| `uputronics-868` | Uputronics 868 (CE1) | 868 → uputronics-ce1 |
| `waveshare-433` | Waveshare SX1262 (433) | 433 → waveshare-sx1262 |
| `waveshare-868` | Waveshare SX1262 (868) | 868 → waveshare-sx1262 |
<!-- test:hw-table:end -->

Die Uputronics-Chip-Selects folgen der Stapel-Konvention oben (CE0 trägt 433, CE1 trägt 868).
`lhpc hardware` ohne Argument zeigt diesen Katalog; die Hardware-Ansicht der Web-Konsole bietet
zusätzlich eine LED-**Detect**-Probe, um die Verdrahtung zu prüfen.

### 7. Web-Konsole starten

Die Installation hat sie bereits gestartet: **`https://127.0.0.1:8443/`** — lokaler Zugriff ist
offen (keine Anmeldung auf Loopback; die Browser-Warnung zur selbstsignierten CA ist erwartbar).
Falls übersprungen (`--no-service`):

```bash
lhpc webserver start-service      # nur lokal, ohne Anmeldung — nach außen ist nichts offen
```

- **Desktop-Klasse (Pi 5):** die Konsole nutzen. Erste Schritte dort: die **Auto-install**-Seite,
  danach im Webserver-Panel [Stack-UIs proxyen / Konsole mit Zertifikats-Anmeldung freigeben](#fernzugriff).
- **Pi Zero 2W oder headless:** besser die CLI (nächster Schritt) — ein stundenlanger Build sollte
  nicht an einem Browser-Tab hängen.

### 8. Stacks per Auto-Install aufsetzen (CLI)

Komplettlauf: **≈ 45 min auf einem Pi 5, ≈ 4 h auf einem Pi Zero 2W.** Auf der Box ausführen
(innerhalb der SSH-Sitzung — nicht auf deinem Desktop), und in tmux:

```bash
tmux new -s lhpc                 # auf dem Pi; nach einem Abbruch: tmux attach -t lhpc
lhpc auto-install --yes
```

Host-Tests sind standardmäßig **aus**; `--tests` schaltet sie ein, `--tx` schließt `--tests` ein
und sendet **echte HF** (Dummy-Loads!). Build-Artefakte bleiben erhalten — ein erneuter Lauf setzt
am bereits Gebauten auf. Warnungen über fehlende optionale Abhängigkeiten sind auf einer
Headless-Box normal.

<details><summary>Stack für Stack statt alles auf einmal</summary>

```bash
# daemon — LoRaHAM-Daemon, besitzt die Funkgeräte (beide Bänder)
lhpc install daemon
lhpc build daemon

# chat — APRS-/Chat-TUI
lhpc install chat
lhpc build chat

# igate — APRS-iGate
lhpc install igate
lhpc build igate

# voice — LoRa-Sprache (GUI; braucht die --with-gui-Abhängigkeiten)
lhpc install voice
lhpc build voice

# kiss — KISS-TNC über TCP
lhpc install kiss
lhpc build kiss

# meshtastic — baut meshtasticd aus dem Quellcode: ≈ 15 min Pi 5 / ≈ 1¾ h Zero 2W
lhpc install meshtastic
lhpc build meshtastic

# meshcom — baut headless QEMU + Firmware aus dem Quellcode: ≈ 20 min Pi 5 / ≈ 2 h Zero 2W
lhpc install meshcom
lhpc build meshcom

# meshcore — MeshCore-Pi-Node
lhpc install meshcore
lhpc build meshcore
```

```bash
lhpc stack start <stack>
lhpc status
lhpc stack stop <stack>
```
</details>

Nach `lhpc stack start meshcom` bootet der **emulierte Node selbst noch** (~1 min auf dem Pi 5,
~5–6 min auf dem Zero 2W) — solange antwortet seine Web-UI mit 502 und das Rufzeichen bleibt ein
Platzhalter (erwartet, kein Fehler).

**Fortschritt beobachten.** `lhpc` gibt pro Schritt ein kopierbares
`[log] <Komponente> -> tail -f <Pfad>` aus — diese Pfade nutzen, keine geratenen. Logs kommen in
**Schüben** (blockgepuffert ohne TTY); ein stilles `tail -f` ist also kein Stillstand — nach CPU
und Objektzahl urteilen:

```bash
ps -eo pcpu,etime,cmd --sort=-pcpu | head -3          # läuft überhaupt ein Compiler?
while sleep 60; do echo "$(date +%T) objs=$(find ~/loraham-pi-control/src -path '*/.pio/build/*' -name '*.o' | wc -l)"; done
```

## Stacks konfigurieren & betreiben

```bash
lhpc status                        # was läuft (nur lesend)
lhpc config <stack>                # Optionen des Stacks samt aktueller Werte
lhpc config chat call W1ABC       # eine Option setzen
lhpc config <stack> --band 868 <param> <wert>     # bandabhängiger Wert bei umschaltbaren Stacks
lhpc stack start|stop|restart <stack>             # zeigt den Plan, fragt nach; --yes überspringt
lhpc logs <ziel>                   # Komponenten-Log verfolgen
lhpc doctor                        # Umgebungs-/Abhängigkeits-Checks
lhpc test <stack> [--tx] --yes     # begrenzter HF-Test (echtes Senden nur mit --tx — Dummy-Loads!)
```

Verändernde Befehle zeigen einen Plan und verlangen `--yes`; vollständige Referenz:
[`docs/cli.md`](docs/cli.md).

## Fernzugriff

Nach der Installation lauscht die Konsole nur auf Loopback. Für den Zugriff aus dem LAN bleibt TLS
an, und vor jeder entfernten Anfrage steht eine **Client-Zertifikats-Anmeldung** (lokal bleibt es
offen):

```bash
lhpc webserver init --dns lhpc-zero.local --ip 192.168.0.10     # PKI: CAs + Server-Zertifikat
lhpc webserver cert issue laptop
lhpc webserver cert export laptop ~/laptop.p12                  # diese Datei im Browser importieren
lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

Eine **öffentliche oder anmeldefreie** Freigabe geht auch — aber auf eigene Gefahr: Wer den Port
erreicht, steuert deine Funkgeräte:

```bash
lhpc webserver expose --cidr 0.0.0.0/0 --auth no-auth --confirm-phrase enable-remote-danger
```

Stack-Web-UIs laufen über dieselbe Front (ihre rohen Ports lauschen auf allen Interfaces, ganz
ohne Anmeldung — lieber proxyen als diese Ports öffnen):

```bash
lhpc webserver proxy meshtastic --mode lan --port 8445 --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver proxy meshcom    --mode lan --port 8446 --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

MeshCore hat keine Web-UI zum Proxyen — der entfernte Node Manager spricht den Node direkt auf
TCP 5000 an; die erlaubten Quellbereiche stehen in der Konfiguration des Stacks
([meshcore](docs/stacks/meshcore.md)).

Ports jenseits von Loopback zu öffnen verlangt eine Firewall ([`docs/firewall.md`](docs/firewall.md));
Details samt Browser-Runbook für Client-Zertifikate: [`docs/webserver.md`](docs/webserver.md).

## Autostart

Die Installation richtet die Konsole für den Systemstart ein (rootlose User-Units + Lingering).
Stacks starten **nicht** automatisch — die startest du über Konsole oder CLI.

```bash
systemctl --user disable lhpc-nginx lhpc-web     # Konsole: nicht beim Booten starten
systemctl --user enable lhpc-nginx lhpc-web      # wieder beim Booten starten (Standard)
systemctl --user stop lhpc-nginx lhpc-web        # jetzt stoppen
systemctl --user start lhpc-nginx lhpc-web       # jetzt starten
```

## Aktualisieren

Ein Klick in der Konsole, oder aus der Shell (vorher `config/` + `profiles/` sichern —
[`docs/operations.md`](docs/operations.md#backup--restore)):

```bash
systemctl --user stop lhpc-web && lhpc self-update --apply
lhpc self-update --repair-integration      # die verwalteten Units wiederherstellen
```

Betriebsmodell und Ein-Klick-Mechanik: [`docs/deployment.md`](docs/deployment.md).

## Fehlerbehebung

| Symptom | Ursache | Abhilfe |
|---|---|---|
| `lhpc: command not found` nach der Installation | PATH noch nicht wirksam | Neustart (Schritt 5), oder neue Login-Shell öffnen |
| Build-Log minutenlang still | Logs kommen in Schüben (blockgepuffert), große Downloads ebenso | nach CPU + Objektzahl urteilen (Schritt 8); [field-notes](docs/field-notes.md) |
| Build abgebrochen / OOM auf Boards mit wenig RAM | Speicherdruck | Swapdatei (Schritt 3); [field-notes](docs/field-notes.md) |
| „optionale Abhängigkeiten fehlen" auf einer Headless-Box | GUI-Komponenten absichtlich übersprungen | ignorieren, oder `--with-gui` |
| Web-Konsole von einem anderen Rechner nicht erreichbar | nicht freigegeben / Firewall | [Fernzugriff](#fernzugriff); [Firewall](docs/firewall.md) |
| SSH **während der Installation** abgerissen, Lauf gestoppt | Orchestrator bekam SIGHUP; abgekoppelte Build-Schritte laufen ggf. weiter | `lhpc auto-install` erneut ausführen (setzt am Cache auf); tmux nutzen (Schritt 0). **Betrifft nur die Installation** — laufende Stacks hängen an systemd bzw. laufen abgekoppelt und überstehen WLAN-Abbrüche; im Normalbetrieb ist danach nichts neu zu installieren. Auf einem Zero 2W umgeht ein USB-LAN-Adapter das Problem bei der Installation ganz |
| Board während eines langen Builds nicht erreichbar | Boards mit wenig RAM verlieren unter Last das Netz | Konsole prüfen, NetworkManager neu starten oder rebooten, dann erneut ausführen; [field-notes](docs/field-notes.md) |
| `auto-install` verweigert den Start nach einem abgebrochenen Lauf | übrig gebliebene Lauf-Marker | `lhpc auto-install --status`, dann `lhpc auto-install --recover`; [field-notes](docs/field-notes.md) |

## Dokumentation

| Gruppe | Doku |
|---|---|
| Verstehen | [Architektur](docs/architecture.md) |
| Benutzen | [CLI](docs/cli.md) · [Betrieb & Sicherheit](docs/operations.md) · [Feldnotizen](docs/field-notes.md) |
| Web-Konsole & Fernzugriff | [Deployment](docs/deployment.md) · [Webserver (HTTPS + mTLS)](docs/webserver.md) · [WLAN-Access-Point](docs/wifi-access-point.md) · [Firewall](docs/firewall.md) · [Migration](docs/deployment-migration.md) |
| Stacks | [Stack hinzufügen](docs/adding-a-stack.md) · [daemon](docs/stacks/daemon.md) · [kiss](docs/stacks/kiss.md) · [aprs](docs/stacks/aprs.md) · [meshcore](docs/stacks/meshcore.md) · [meshcom](docs/stacks/meshcom.md) · [meshtastic](docs/stacks/meshtastic.md) · [voice](docs/stacks/voice.md) |
| Referenz & Richtlinien | [Härtung](docs/hardening-0.1.md) · [Provenienz](docs/provenance.md) |

Gesamtindex: [`docs/README.md`](docs/README.md).
