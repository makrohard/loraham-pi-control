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
- [Installation](#installation) — von der frisch geflashten Karte zu laufenden Stacks (Schritte 0–7)
- [Verwenden](#verwenden) — [CLI](#cli) · [Web-Konsole](#web-konsole) · [Aktualisieren](#aktualisieren)
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

Getestet: **LoRaHAM Pi, Uputronics (einfach und dual), Waveshare** — auf Pi **Zero 2W** und
**Pi 5**. Andere Boards sollten funktionieren, sind aber nicht validiert. `lhpc hardware` listet den
Katalog:

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

Waveshare 868 ist noch nicht On-Air-validiert; eine frische Installation ist unkonfiguriert.
**SPI-Modus:** `soft-cs` (`dtparam=spi=on` + `dtoverlay=spi0-0cs`) deckt LoRaHAM Pi / Uputronics /
Waveshare ab (inkl. dual Uputronics, Chip-Selects als GPIOs); `hardware-cs` nur für
kernelgesteuerte CE0/CE1.

### Nicht enthalten

- **Keine Firewall-Verwaltung** — `lhpc` schützt nur seine eigene Konsole; von einem Stack geöffnete Ports schließt du selbst ([Firewall](docs/firewall.md)).
- **Es wird nie ein GUI/Desktop installiert** — nur GUI-*Anwendungs*-Bibliotheken, und nur mit `--with-gui`.
- **Lizenz & Sendebetrieb** bleiben in der Verantwortung des Betreibers — HF wird nie automatisch gesendet.

## Installation

Von der frisch geflashten Karte zu laufenden Stacks. Die Schritte laufen der Reihe nach.

### 0. Karte vorbereiten

Im Raspberry Pi Imager: **Modell**, **Raspberry Pi OS Lite (64-bit)** wählen und **Hostname,
Benutzer, WLAN + Land, SSH aktivieren** vor dem Flashen setzen.

<details><summary>Notlösung ohne Oberfläche — falls die Erstkonfiguration des Imagers nicht greift (wiederholt beobachtet)</summary>

```bash
sudo rfkill unblock wifi
sudo raspi-config nonint do_wifi_country DE          # dein ISO-Ländercode
sudo nmcli device wifi connect "<SSID>" password "<PSK>"
sudo systemctl enable --now ssh
sudo hostnamectl set-hostname loraham                # dann /etc/hosts abgleichen:
echo "127.0.1.1 loraham" | sudo tee -a /etc/hosts
sudo sed -i 's/^# *\(en_US.UTF-8\)/\1/' /etc/locale.gen && sudo locale-gen && sudo update-locale
```
</details>

### 1. Prüfen, was installiert wird  (~30 s, gemessen)

Nur-lesender Vorab-Check — löst die Paket-Closure eines frischen Images auf und **bricht ab**, wenn
etwas Grafisches hereingezogen würde. Ändert nichts.

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
sudo bash bootstrap-deps.sh --dry-run
```

### 2. Abhängigkeiten installieren  (~2,5 min kalt / 30–50 s erneut, gemessen)

```bash
sudo bash bootstrap-deps.sh --spi-mode soft-cs
```

`--spi-mode` ist **erforderlich**: `soft-cs` (LoRaHAM Pi / Uputronics / Waveshare, inkl. dual) ·
`hardware-cs` (Kernel-CE0/CE1) · `skip`. Außerdem: `--with-gui` (GUI-App-Bibliotheken) ·
`--no-swapfile` · `--swap-size <MB>` (Standard 768) · `--operator-user <name>`, falls als root
ausgeführt. Über apt hinaus **deaktiviert es den System-`nginx.service`** (das Paket bleibt; `lhpc`
liefert über seine eigene rootless-Unit) und legt auf Hosts unter ~600 MB RAM `/var/swap.lhpc`
(768 MB, unter zram) als OOM-Absicherung für die langen Builds an — auf Kosten von etwas
SD-Karten-Verschleiß.

<details><summary>Manuell — nur installieren, was die Stacks brauchen, die du betreibst (bootstrap-deps.sh ist die Quelle der Wahrheit; Vorschau mit <code>--dry-run</code>, neu erzeugen mit <code>lhpc deps --script</code>)</summary>

<!-- test:deps-manual:start -->
```bash
# lhpc selbst + Fetch/TLS-Werkzeuge (nginx nur, wenn du die Web-Konsole willst)
sudo apt install -y --no-install-recommends git python3 python3-venv python3-pip nginx ca-certificates curl wget xz-utils
sudo apt install -y --no-install-recommends cmake liblgpio-dev build-essential          # daemon / RadioLib
sudo apt install -y --no-install-recommends libncurses-dev                              # chat / igate
sudo apt install -y --no-install-recommends socat                                       # kiss
sudo apt install -y --no-install-recommends libssl-dev libslirp0                        # meshcom (Bridge + QEMU)
sudo apt install -y --no-install-recommends libyaml-cpp-dev libuv1-dev libgpiod-dev libi2c-dev libusb-1.0-0-dev libulfius-dev libbluetooth-dev pkg-config   # meshtastic (aus Quelltext gebaut)
sudo apt install -y --no-install-recommends libcodec2-dev libgtk-3-dev libasound2-dev python3-tk           # nur mit --with-gui (Voice, MeshCore Node Manager)

sudo systemctl disable --now nginx.service               # Paket behalten, den ROOT-Dienst deaktivieren
# Boards mit wenig RAM (<600 MB): eine Disk-Swapdatei verhindert OOM bei den meshtasticd-/meshcom-Builds
sudo fallocate -l 768M /var/swap.lhpc && sudo chmod 600 /var/swap.lhpc && sudo mkswap /var/swap.lhpc
echo '/var/swap.lhpc none swap sw,pri=-2 0 0' | sudo tee -a /etc/fstab && sudo swapon -a
printf 'dtparam=spi=on\ndtoverlay=spi0-0cs\n' | sudo tee -a /boot/firmware/config.txt   # SPI-Overlay
sudo usermod -aG spi,gpio "$USER"                        # → weiter bei Schritt 3 (Neustart)
```
<!-- test:deps-manual:end -->
</details>

### 3. Neustart

SPI-Overlay und deine neue `spi`/`gpio`-Gruppenmitgliedschaft werden mit dem Neustart wirksam.

```bash
sudo reboot
```

### 4. lhpc installieren  (~1,5 min, gemessen)

```bash
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   oder aus einem Checkout: ./install.sh
#   Optionen: --target <verzeichnis> · --no-service (kein Web-Dienst) · --no-path (kein CLI-Symlink)
```

Alles landet unter `~/loraham-pi-control/`: LHPCs Checkout unter `src/loraham-pi-control`, das venv
unter `venv/lhpc`, Einstellungen/Secrets/Zertifikate unter `config/`. (Ein einmaliger PyPI-Retry
beim Laden von `cryptography` ist harmlos — pip wiederholt.)

<details><summary>Manuell — Klonen / venv / bootstrap</summary>

```bash
mkdir -p ~/loraham-pi-control/src
git clone https://github.com/makrohard/loraham-pi-control.git ~/loraham-pi-control/src/loraham-pi-control
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control
~/loraham-pi-control/venv/lhpc/bin/lhpc bootstrap --yes
export PATH="$HOME/loraham-pi-control/venv/lhpc/bin:$PATH"
```
</details>

### 5. Neu anmelden

`~/.local/bin` ist in deiner aktuellen Shell noch nicht im `PATH`. **Neu per SSH verbinden oder eine
neue Login-Shell öffnen**, sonst schlägt der nächste Befehl mit `lhpc: command not found` fehl.

### 6. Konfigurieren

```bash
lhpc config operator --callsign DL1ABC    # dein Rufzeichen (erben lizenzierte Stacks)
lhpc hardware                             # Katalog anzeigen
lhpc hardware uputronics                  # dein Funkgerät wählen (hier dual Uputronics)
```

### 7. Stacks hochfahren

Die **Web-Konsole** ist der Hauptweg: lokal öffnen (Schritt 4 hat sie unter
`https://127.0.0.1:8443/` gestartet, oder `lhpc web` → `http://127.0.0.1:8770/`) und die Seite
**Auto-install** nutzen. Auf einem **Pi Zero 2W / RAM-armen Board besser die CLI** — ein
mehrstündiger Build sollte nicht von einer Browser-Sitzung abhängen. Führe ihn abgekoppelt aus,
damit eine abgebrochene SSH-Verbindung ihn nicht beendet:

```bash
sudo apt install -y tmux
tmux
lhpc auto-install --yes          # abkoppeln: Strg-B, dann D · wieder verbinden: tmux attach
```

Host-Tests sind **standardmäßig aus**; `--tests` aktiviert sie, `--tx` impliziert `--tests` und
sendet **echtes HF** (Dummy-Loads). Build-Artefakte bleiben erhalten, ein erneuter Lauf setzt am
bereits Kompilierten an. Dauer (**extrapoliert**): meshtasticd ~2,5–3,5 h, meshcom ~26 min kalt /
~2,5 min inkrementell; Gesamtdauer **ausstehend**. Auf einer Headless-Box sind „optional deps
missing"-Warnungen zu erwarten.

**Fortschritt beobachten.** `lhpc` gibt pro Schritt eine kopierbare Zeile
`[log] <component> -> tail -f <path>` aus — diese Pfade nutzen, nicht raten. Logs aktualisieren in
**Schüben** (block-gepuffert ohne TTY), ein stilles `tail -f` ist also kein Stillstand — an CPU und
Objektanzahl messen:

```bash
ps -eo pcpu,etime,cmd --sort=-pcpu | head -3          # läuft wirklich ein Compiler?
while sleep 60; do echo "$(date +%T) objs=$(find ~/loraham-pi-control/src -path '*/.pio/build/*' -name '*.o' | wc -l)"; done
while sleep 30; do free -m | awk '/Mem:/{print "mem",$3"/"$2} /Swap:/{print "swap",$3}'; vcgencmd measure_temp; done >> ~/watch.log
```

<details><summary>Einzeln statt alles</summary>

```bash
lhpc install <stack>
lhpc build <stack>
lhpc stack start <stack>
lhpc status
```
</details>

## Verwenden

### CLI

```bash
lhpc status                        # was läuft (nur lesend)
lhpc doctor                        # Umgebungs-/Abhängigkeits-Checks
lhpc logs <target>                 # ein Komponenten-Log anzeigen
lhpc stack start|stop <stack>      # starten / stoppen (Plan + Bestätigung)
lhpc build <target>                # einen Stack bauen
lhpc test <target> [--tx] --yes    # begrenzter HF-Test (echtes HF mit --tx)
lhpc hardware [<setup>]            # Funk-Hardware anzeigen oder setzen
lhpc config operator --callsign <RUFZEICHEN>
```

Verändernde Befehle zeigen einen Plan und brauchen `--yes`; volle Referenz [`docs/cli.md`](docs/cli.md).

### Web-Konsole

`lhpc web` liefert eine nur-lokale Konsole unter `:8770`; das produktive HTTPS-+-mTLS-Frontend
(nginx, `:8443`) gibst du ins Netz frei:

```bash
lhpc webserver init --dns pi.local --ip 192.168.0.10
lhpc webserver start-service
lhpc webserver expose --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver cert issue laptop && lhpc webserver cert export laptop ~/laptop.p12
lhpc webserver apply
```

Details: [`docs/webserver.md`](docs/webserver.md); Ports über Loopback hinaus zu öffnen erfordert
eine Firewall ([`docs/firewall.md`](docs/firewall.md)).

### Aktualisieren

Ein Klick in der Konsole, oder aus einer Shell (vorher `config/` + `profiles/` sichern —
[`docs/operations.md`](docs/operations.md#backup--restore)):

```bash
systemctl --user stop lhpc-web && lhpc self-update --apply
lhpc self-update --repair-integration      # verwaltete Units neu installieren
```

Serving-Modell und der One-Click-Mechanismus: [`docs/deployment.md`](docs/deployment.md).

## Fehlerbehebung

| Symptom | Ursache | Was tun |
|---|---|---|
| `lhpc: command not found` nach der Installation | PATH nicht übernommen | neu anmelden (Schritt 5) |
| Build-Log wirkt eingefroren / minutenlang still | Logs aktualisieren in Schüben (block-gepuffert), große Downloads auch | an CPU + Objektanzahl messen (Schritt 7); [field-notes](docs/field-notes.md) |
| Build abgebrochen / OOM auf RAM-armen Boards | RAM-Druck | Swapdatei (Schritt 2); [field-notes](docs/field-notes.md) |
| „optional deps missing" auf einer Headless-Box | GUI-Komponenten bewusst übersprungen | ignorieren, oder `--with-gui` |
| Web-Konsole von einem anderen Rechner nicht erreichbar | nicht freigegeben / Firewall | [Web-Konsole](#web-konsole); [Firewall](docs/firewall.md) |
| SSH abgebrochen, Lauf gestoppt | Orchestrator bekam SIGHUP; abgekoppelte Build-Schritte laufen ggf. weiter | `lhpc auto-install` erneut (setzt an Artefakten an); tmux nutzen (Schritt 7) |
| Board während eines langen Builds nicht erreichbar | RAM-arme Boards verlieren unter Last das Netz | Konsole prüfen, NetworkManager neu starten oder rebooten, dann erneut; [field-notes](docs/field-notes.md) |
| `auto-install` startet nach abgebrochenem Lauf nicht | übrig gebliebene Lauf-Marker | `lhpc auto-install --status`, dann `lhpc auto-install --recover`; [field-notes](docs/field-notes.md) |

## Dokumentation

| Gruppe | Dokumente |
|---|---|
| Verstehen | [Architektur](docs/architecture.md) |
| Verwenden | [CLI](docs/cli.md) · [Betrieb & Sicherheit](docs/operations.md) · [Field notes](docs/field-notes.md) |
| Web-Konsole & Fernzugriff | [Deployment](docs/deployment.md) · [Webserver (HTTPS + mTLS)](docs/webserver.md) · [WLAN-Access-Point](docs/wifi-access-point.md) · [Firewall](docs/firewall.md) · [Migration](docs/deployment-migration.md) |
| Stacks | [Stack hinzufügen](docs/adding-a-stack.md) · [daemon](docs/stacks/daemon.md) · [kiss](docs/stacks/kiss.md) · [aprs](docs/stacks/aprs.md) · [meshcore](docs/stacks/meshcore.md) · [meshcom](docs/stacks/meshcom.md) · [meshtastic](docs/stacks/meshtastic.md) · [voice](docs/stacks/voice.md) |
| Referenz & Richtlinien | [Hardening](docs/hardening-0.1.md) · [Provenance](docs/provenance.md) |

Vollständiger Index: [`docs/README.md`](docs/README.md).
