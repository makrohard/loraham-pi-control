# LoRaHAM Pi Control (`lhpc`) — Anleitung (Deutsch)

[![Live-Demo](https://img.shields.io/badge/%E2%96%B6%20Live--Demo-im%20Browser-2ea44f)](https://makrohard.github.io/loraham-pi-control/)
<br>
[![In GitHub Codespaces öffnen](https://github.com/codespaces/badge.svg)](https://codespaces.new/makrohard/loraham-pi-control)

> **Kein Pi zur Hand? Zwei Wege ganz ohne Hardware:**
> - **[Live-Demo](https://makrohard.github.io/loraham-pi-control/)** — die echte Konsole
>   direkt im Browser. Nichts zu installieren, **keine Anmeldung**, für alle nutzbar
>   (simuliert, via Pyodide).
> - **Codespace** (Badge oben) — das vollständige Test-Lab mit echten Stack-Prozessen auf
>   simulierter Hardware. **Erfordert die Anmeldung mit einem (kostenlosen)
>   GitHub-Konto.** Siehe [`docs/testlab.md`](docs/testlab.md) (englisch).

Die LoRa-Amateurfunk-Software-Stacks auf einem Raspberry Pi von einer Stelle aus installieren,
konfigurieren und betreiben — über eine CLI und eine lokale Web-Konsole. `lhpc` übernimmt den
Quellcode jedes Stacks, baut ihn, startet/stoppt ihn in Abhängigkeitsreihenfolge, erzwingt einen
Stack pro Funkband und schreibt die Konfiguration jeder App. Für Betreiber, die einen LoRaHAM- /
Meshtastic- / MeshCom- / MeshCore-Knoten auf einem Pi Zero 2W oder Pi 5 aufsetzen.

> Maßgeblich ist die englische [`README.md`](README.md); diese Übersetzung kann hinterherhinken.
> Code, Oberflächentexte und die übrigen Dokumente sind auf Englisch.

## Inhalt

- [Überblick](#überblick) — [Stacks](#stacks) · [Hardware](#hardware)
- [Installation](#installation) — von der frisch geflashten Karte zu laufenden Stacks (Schritte 0–8)
- [Stacks konfigurieren & betreiben](#stacks-konfigurieren--betreiben) · [Fernzugriff](#fernzugriff) · [Autostart](#autostart) · [Binary-Kanal](#binary-kanal-vorkompiliert) · [Aktualisieren](#aktualisieren)
- [Fehlerbehebung](#fehlerbehebung) · [Dokumentation](#dokumentation)

## Überblick

### Stacks

| Stack | Band | Was es ist | Doku |
|---|---|---|---|
| `daemon` | 433 + 868 | LoRaHAM-Daemon — besitzt die Funkgeräte, stellt pro Band Sockets bereit | [daemon](docs/stacks/daemon.md) |
| `chat` | 433 | APRS-/Chat-TUI (lokal oder über SSH) | [aprs](docs/stacks/aprs.md) |
| `igate` | 433 | APRS-iGate — **veraltet**, stattdessen `graywolf` | [aprs](docs/stacks/aprs.md) |
| `voice` | 433 / 868 | LoRa-Sprache — GTK-App auf Desktop, ncurses-Terminal auf Lite | [voice](docs/stacks/voice.md) |
| `kiss` | 433 / 868 | KISS-TNC über TCP (xastir, YAAC …) | [kiss](docs/stacks/kiss.md) |
| `graywolf` | über `kiss` | Graywolf-APRS-Station (Web-UI, Digipeater, iGate) — ersetzt `igate` | [graywolf](docs/stacks/graywolf.md) |
| `meshtastic` | 433 / 868 | Rootless `meshtasticd`, steuert das Funkgerät direkt | [meshtastic](docs/stacks/meshtastic.md) |
| `meshcom` | 433 | MeshCom-Firmware in QEMU, an den Daemon gebrückt | [meshcom](docs/stacks/meshcom.md) |
| `meshcore` | 868 | MeshCore-Pi-Node (TCP 5000) | [meshcore](docs/stacks/meshcore.md) |
| `reticulum` | 433 / 868 | Reticulum-Node, steuert das Funkmodul direkt über SPI | [reticulum](docs/stacks/reticulum.md) |

Daemon-gestützte Stacks starten den Daemon automatisch; Meshtastic steuert das Funkgerät selbst und
kann sich kein Band mit dem Daemon teilen (`lhpc` blockiert den Konflikt).

**Position (GPS)** ist eine globale Einstellung für alle Stacks, die sie nutzen können — ein gpsd
auf diesem oder einem anderen Rechner, ein direkt gelesener Empfänger oder eine feste Position.
Jeder Stack hat zusätzlich seinen eigenen Schalter: `lhpc gps --source gpsd`, dann
`lhpc config meshtastic use_gps on`. Siehe [GPS](docs/gps.md).

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

## Installation

> **Einfachster Weg — ein fertiges Image.** [`loraham-images`](https://github.com/makrohard/loraham-images)
> liefert fertige Raspberry-Pi-OS-Images mit vorinstalliertem LHPC und allen Stacks — flashen, booten,
> loslegen. Die Schritte unten sind die manuelle Alternative.

Von der frisch geflashten Karte zu laufenden Stacks. Die Schritte laufen der Reihe nach.

### 0. Karte vorbereiten

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

### 1. Erster SSH-Login — mit tmux bei WLAN- und Headless-Betrieb

```bash
ssh <benutzer>@lhpc-zero.local   # Benutzer + Hostname aus dem Imager
sudo apt install -y tmux
tmux new -s lhpc                 # alles Weitere in dieser Sitzung ausführen
#   abkoppeln: Strg-B, dann D  ·  nach einem Abbruch: SSH neu verbinden, dann  tmux attach -t lhpc
```

Der tmux-Teil zählt auf einem **Pi Zero 2W und überhaupt bei jedem headless betriebenen Pi im
WLAN**: Dessen WLAN setzt
unter Build-Last kurz aus — die Verbindung hängt sekundenweise, `sshd` antwortet nicht mehr, das
Board arbeitet aber weiter — und ein langer Schritt in einer nackten SSH-Sitzung reißt dabei ab.
tmux hält die Arbeit am Laufen; du koppelst einfach wieder an. Relevant für die Schritte **3**
(Abhängigkeiten), **4** (lhpc installieren) und **8** (`auto-install` — die langen Builds); auf
einem Pi 5 am LAN kannst du dir tmux sparen. Erwischt es dich doch einmal außerhalb von tmux: neu
verbinden und den Schritt wiederholen — jeder Schritt ist idempotent und setzt am Cache wieder auf.

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
sudo apt install -y --no-install-recommends git python3 python3-venv python3-pip nftables nginx ca-certificates curl zstd
sudo apt install -y --no-install-recommends cmake liblgpio-dev build-essential          # daemon / RadioLib
sudo apt install -y --no-install-recommends libncurses-dev                              # chat / igate / voice (Terminal)
sudo apt install -y --no-install-recommends libcodec2-dev libasound2-dev                # voice (ncurses-Terminal-UI — keine grafischen Pakete)
sudo apt install -y --no-install-recommends socat                                       # kiss
sudo apt install -y --no-install-recommends python3-libgpiod python3-spidev            # reticulum (direct-SPI radio, no compiler needed)
sudo apt install -y --no-install-recommends libssl-dev libslirp0 meson ninja-build libglib2.0-dev libpixman-1-dev libslirp-dev zlib1g-dev libgcrypt20-dev   # meshcom (Bridge + QEMU, headless aus dem Quellcode gebaut)
sudo apt install -y --no-install-recommends libyaml-cpp-dev libuv1-dev libgpiod-dev libi2c-dev libusb-1.0-0-dev libulfius-dev libbluetooth-dev pkg-config   # meshtastic (aus dem Quellcode gebaut)
sudo apt install -y --no-install-recommends libgtk-3-dev libx11-dev python3-dev        # nur mit --with-gui (Voice-GTK-App, Sideband)

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

- **Kleine Systeme (Zero 2W / wenig RAM):** besser die CLI (nächster Schritt), und die Konsole während
  der großen Builds gestoppt lassen — die Konsole selbst (nginx + Web-App + Status-Abfragen) kostet
  RAM und CPU, die die Builds besser gebrauchen können. Nur den Browser-Tab zu schließen bringt
  nichts, die Serverseite läuft weiter:
  ```bash
  systemctl --user stop lhpc-web lhpc-nginx      # nach dem auto-install wieder starten
  ```
- **Pi 5 / Desktop-Klasse:** hier fällt die Last der Konsole nicht ins Gewicht — nutzen. Vom
  Desktop aus, ganz ohne Freigabe: `ssh -L 8443:127.0.0.1:8443 <benutzer>@lhpc-zero.local`, dann
  `https://127.0.0.1:8443/` öffnen. Erste Schritte dort: die **Auto-install**-Seite, danach im
  Webserver-Panel [Stack-UIs proxyen / Konsole mit Zertifikats-Anmeldung freigeben](#fernzugriff).

### 8. Stacks per Auto-Install aufsetzen (CLI)

Auf dem Pi ausführen (innerhalb der SSH-Sitzung — nicht auf deinem Desktop), und in tmux:

```bash
tmux new -s lhpc                 # auf dem Pi; nach einem Abbruch: tmux attach -t lhpc
lhpc auto-install --yes
```

Die drei lange kompilierenden Stacks (daemon, meshtastic, meshcom) werden standardmäßig aus dem
[Binary-Kanal](#binary-kanal-vorkompiliert) installiert — Sekunden bis wenige Minuten Download
statt Stunden. Der Rest baut in je wenigen Minuten aus dem Quellcode. **Alles** stattdessen aus
Quellen zu bauen (`--source pinned`) dauert ≈ 35–45 min auf einem Pi 5 und ≈ 4 h auf einem Pi Zero 2W.

Host-Tests sind standardmäßig **aus**; `--tests` schaltet sie ein, `--tx` schließt `--tests` ein
und sendet **echte HF** (Dummy-Loads!). Build-Artefakte bleiben erhalten — ein erneuter Lauf setzt
am bereits Gebauten auf. Warnungen über fehlende optionale Abhängigkeiten sind im
Headless-Betrieb normal.

<details><summary>Stack für Stack statt alles auf einmal</summary>

```bash
# daemon — LoRaHAM-Daemon, besitzt die Funkgeräte (beide Bänder); standardmäßig Binary
lhpc install daemon
#   stattdessen aus Quellen:  lhpc install daemon --source pinned && lhpc build daemon

# chat — APRS-/Chat-TUI
lhpc install chat
lhpc build chat

# igate — APRS-iGate — VERALTET: bekannte Fehler, ungepflegt; stattdessen graywolf
lhpc install igate
lhpc build igate

# voice — LoRa-Sprache (Terminal-Variante baut headless; die GTK-App braucht --with-gui)
lhpc install voice
lhpc build voice

# kiss — KISS-TNC über TCP
lhpc install kiss
lhpc build kiss

# meshtastic — standardmäßig Binary (kein Build-Schritt); Quellen: ≈ 15 min Pi 5 / ≈ 1¾ h Zero 2W
lhpc install meshtastic
#   stattdessen aus Quellen:  lhpc install meshtastic --source pinned && lhpc build meshtastic

# meshcom — standardmäßig Binary (kein Build-Schritt); Quellen: ≈ 20 min Pi 5 / ≈ 2 h Zero 2W
lhpc install meshcom
#   stattdessen aus Quellen:  lhpc install meshcom --source pinned && lhpc build meshcom

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
# die Installation hat die PKI bereits mit Loopback-SANs angelegt — alle eigenen Adressen ergänzen,
# inklusive 10.42.0.1, wenn dieser Pi später sein eigener Access-Point werden soll
lhpc webserver configure --dns localhost --dns lhpc-zero.local \
                         --ip 127.0.0.1 --ip 192.168.0.10 --ip 10.42.0.1
lhpc webserver tls-renew                # Server-Zertifikat mit diesen SANs neu ausstellen
lhpc webserver cert issue lhpc-laptop   # gibt EINMALIG eine Passphrase aus — notieren!
lhpc webserver cert export lhpc-laptop ~/lhpc-laptop.p12
lhpc webserver expose --cidr 192.168.0.0/24 --cidr 10.42.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

> **Beide Listen sind wiederholbar, und `configure` ERSETZT sie** — alle Adressen und Bereiche in
> einem Zug angeben (`localhost` / `127.0.0.1` inklusive, sonst verliert die lokale Konsole ihren
> eigenen Zertifikatsnamen). Wer den [Access-Point](docs/wifi-access-point.md) gleich mitnimmt,
> braucht beim späteren Umschalten nur noch die Firewall. Nachträglich ergänzen ist ebenfalls
> unkritisch: `configure` + `tls-renew` + `webserver apply` stellt nur das Server-Zertifikat unter
> derselben CA neu aus, importierte Client-Zertifikate funktionieren weiter. Nie erneut ausführen
> darf man auf einem laufenden System `init` — das legt beide CAs neu an und macht jedes
> ausgegebene Client-Zertifikat ungültig.

> **Zertifikatsname für die Auswahl** — der Name erscheint in der Zertifikatsabfrage des Browsers
> bzw. des Handys. Deshalb `lhpc-` voranstellen: Auf einem Gerät mit mehreren Zertifikaten sagt
> `laptop` nichts, `lhpc-laptop` schon.

> **Reihenfolge mit der Firewall:** `expose` → Firewall anwenden → `webserver apply`. Umgekehrt
> verweigert `webserver apply` mit *Firewall changes pending* und du führst das Skript zweimal aus.

**Zwei** Dateien müssen auf das Gerät: das exportierte Bundle und die Server-CA, damit der
Browser der Seite vertraut statt zu warnen. Jede mit eigenem Befehl und explizitem Ziel kopieren:

```bash
scp <benutzer>@lhpc-zero.local:lhpc-laptop.p12 .
scp <benutzer>@lhpc-zero.local:loraham-pi-control/config/tls/server-ca/ca.crt .
```

> Bei `scp a b` ist das letzte Argument **immer** das Ziel, und scp kopiert anstandslos
> remote→remote — `host:{lhpc-laptop.p12,…/ca.crt}` schreibt also die erste Datei **über die zweite** und
> zerstört dein CA-Zertifikat. Eine Datei pro Befehl.

Beide im Zertifikatsspeicher des Geräts importieren, für das Bundle die einmalige Passphrase
eingeben. Anleitung je Browser sowie für Android/iOS:
[`docs/webserver.md`](docs/webserver.md#install-the-client-certificate-in-a-browser).

Eine **öffentliche oder anmeldefreie** Freigabe geht auch — aber auf eigene Gefahr: Wer den Port
erreicht, steuert deine Funkgeräte:

```bash
lhpc webserver expose --cidr 0.0.0.0/0 --auth no-auth --confirm-phrase enable-remote-danger
```

Stack-Web-UIs laufen über dieselbe Front (ihre rohen Ports lauschen auf allen Interfaces, ganz
ohne Anmeldung — lieber proxyen als diese Ports öffnen):

```bash
lhpc webserver proxy meshtastic --mode lan --port 8445 --auth local-open-remote-auth \
     --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver proxy meshcom    --mode lan --port 8446 --auth local-open-remote-auth \
     --cidr 192.168.0.0/24 --confirm-phrase enable-remote
lhpc webserver apply
```

MeshCore hat keine Web-UI zum Proxyen — der entfernte Node Manager spricht den Node direkt auf
TCP 5000 an; die erlaubten Quellbereiche stehen in der Konfiguration des Stacks. Headless brauchst
du ihn gar nicht: Der interaktive REPL-Client `meshcore-cli` läuft direkt in deiner SSH-Sitzung
auf dem Pi (die Startzeile zeigt die Karte des Stacks im Dashboard; Details unter
[meshcore](docs/stacks/meshcore.md)).

Ports jenseits von Loopback zu öffnen verlangt eine Firewall ([`docs/firewall.md`](docs/firewall.md));
Details samt Browser-Runbook für Client-Zertifikate: [`docs/webserver.md`](docs/webserver.md).

## Autostart

Die Installation richtet die Konsole für den Systemstart ein (rootlose User-Units + Lingering).
Stacks, die **vor einem Neustart liefen, werden automatisch wieder gestartet** (Standard: an):
Beim Booten startet `lhpc-boot-restore.service` jeden Stack neu, der per LHPC gestartet und vor
dem Reboot nicht gestoppt wurde — über den normalen Startpfad mit der **gespeicherten**
Konfiguration. Alle Prüfungen (Hardware, Band-Arbitrierung, Rufzeichen, Firewall-Exposure)
gelten unverändert; das TX-Verhalten kommt strikt aus der gespeicherten Konfiguration, nie aus
einmaligen Overrides einer früheren Sitzung.

```bash
lhpc autostart          # Schalter und letztes Boot-Restore-Ergebnis anzeigen
lhpc autostart off      # abschalten (gilt ab dem NÄCHSTEN Boot)
lhpc autostart on       # wieder einschalten (Standard)
```

Derselbe Schalter steht im Webserver-Panel der Konsole ("Boot restore"). Die Wiederherstellung
läuft nur, wenn die Web-Konsolen-Unit aktiviert und unverändert ist — eine deaktivierte oder
angepasste Konsole schaltet sie wirksam ab. Ein Stack, dessen Wiederherstellung fehlschlägt,
wird **nicht erneut versucht**; starte ihn selbst mit `lhpc stack start <id>` (das sagt auch das
Dashboard-Banner).

```bash
systemctl --user disable lhpc-nginx lhpc-web     # Konsole: nicht beim Booten starten
systemctl --user enable lhpc-nginx lhpc-web      # wieder beim Booten starten (Standard)
systemctl --user stop lhpc-nginx lhpc-web        # jetzt stoppen
systemctl --user start lhpc-nginx lhpc-web       # jetzt starten
```

## Binary-Kanal (vorkompiliert)

Drei Stacks kompilieren lange — der LoRaHAM-Daemon, meshtasticd und MeshComs QEMU. Für die kann
lhpc statt eines Quell-Builds ein **vorkompiliertes Binary** installieren: Wo eines für deine
Plattform veröffentlicht ist (aarch64 / Debian Trixie), ist das der Standard — aus einer
mehrstündigen Installation werden wenige Minuten Download.

```bash
lhpc install daemon --yes                    # Binary, wo veröffentlicht (Standard)
lhpc install daemon --source pinned --yes    # stattdessen aus Quellen bauen
lhpc status --versions                       # zeigt: binary  binary@<sha>  built_from=<commit>
```

Jedes Artefakt wird über HTTPS geladen und **vor dem Entpacken per sha256 und Größe geprüft**,
muss aus exakt den Commits gebaut sein, die dieses lhpc pinnt, und den verpflichtenden
Smoke-Test des Builders bestanden haben. Schlägt eine Prüfung fehl, verweigert die Installation
und bietet den Quell-Kanal an — niemals ein stiller Rückfall.

Was der Binary-Kanal praktisch bedeutet:

- **Updates** bleiben im Kanal: Gibt es ein neueres Artefakt, wird binary→binary aktualisiert.
  Hinkt das Binary der gepinnten Version hinterher, sagt lhpc das und lässt dich den (langen)
  Quell-Build wählen — Abbrechen behält das laufende Binary.
- **Build und Host-Tests brauchen Quellen.** Eine Binary-Installation hat kein Checkout, diese
  Aktionen verweigern mit dem Befehl zum Umschalten.
- **MeshCom läuft in diesem Kanal ohne Auth**: Die veröffentlichte Firmware wird ohne
  Mesh-Passwort gebaut, deshalb läuft die Bridge ohne Passwort und Passwortänderungen sind
  deaktiviert, bis du aus Quellen installierst.
- **Die Konsole bietet dieselbe Wahl.** Install wählt Binary vor, wo eines veröffentlicht ist;
  scheitert der Download oder eine Prüfung, fragt die Bestätigungsseite *„Stattdessen aus Quellen
  bauen?"* — der Wechsel bleibt deine Entscheidung.
- **Eine abgebrochene Installation wird zurückgenommen, nicht halbfertig gelassen.** Dateien,
  Beleg (Receipt) und die MeshCom-Passworteinstellung gehören zu einer journalisierten
  Transaktion: Bis sie committet, meldet der Status den Stack als klärungsbedürftig, und die
  nächste Binary-Operation stellt die vorherige Installation wieder her.
- Benötigt `zstd` (Teil von `bootstrap-deps.sh`).

Gebaut und veröffentlicht werden die Binaries von
[lhpc-binaries](https://github.com/makrohard/lhpc-binaries) — ein Workflow pro Stack, mit
Provenienz (Quell-Commits, Rezept-Commit, Container-Digest) im Index.

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
| „optionale Abhängigkeiten fehlen" im Headless-Betrieb | GUI-Komponenten absichtlich übersprungen | ignorieren, oder `--with-gui` |
| Web-Konsole von einem anderen Rechner nicht erreichbar | nicht freigegeben / Firewall | [Fernzugriff](#fernzugriff); [Firewall](docs/firewall.md) |
| SSH **während der Installation** abgerissen, Lauf gestoppt | Orchestrator bekam SIGHUP; abgekoppelte Build-Schritte laufen ggf. weiter | `lhpc auto-install` erneut ausführen (setzt am Cache auf); tmux nutzen (Schritt 1). **Betrifft nur die Installation** — laufende Stacks hängen an systemd bzw. laufen abgekoppelt und überstehen WLAN-Abbrüche; im Normalbetrieb ist danach nichts neu zu installieren. Auf einem Zero 2W umgeht ein USB-LAN-Adapter das Problem bei der Installation ganz |
| Board während eines langen Builds nicht erreichbar | Boards mit wenig RAM verlieren unter Last das Netz | Konsole prüfen, NetworkManager neu starten oder rebooten, dann erneut ausführen; [field-notes](docs/field-notes.md) |
| Quell-Installation meldet „GitHub clone failed" | der Clone — oder ein Schritt danach (Checkout des gepinnten Commits) — hat aufgegeben | der Grund steht am Ende von `logs/adopt-<Komponente>.log` (`[fail] <Schritt>: …`); Installation erneut starten, eine langsame Leitung wird nicht gemerkt |
| `auto-install` verweigert den Start nach einem abgebrochenen Lauf | übrig gebliebene Lauf-Marker | `lhpc auto-install --status`, dann `lhpc auto-install --recover`; [field-notes](docs/field-notes.md) |

## Dokumentation

| Gruppe | Doku |
|---|---|
| Verstehen | [Architektur](docs/architecture.md) |
| Benutzen | [CLI](docs/cli.md) · [Betrieb & Sicherheit](docs/operations.md) · [Wartung](docs/maintenance.md) · [Feldnotizen](docs/field-notes.md) |
| Web-Konsole & Fernzugriff | [Deployment](docs/deployment.md) · [Webserver (HTTPS + mTLS)](docs/webserver.md) · [WLAN-Access-Point](docs/wifi-access-point.md) · [Firewall](docs/firewall.md) · [Migration](docs/deployment-migration.md) |
| Stacks | [Stack hinzufügen](docs/adding-a-stack.md) · [daemon](docs/stacks/daemon.md) · [kiss](docs/stacks/kiss.md) · [graywolf](docs/stacks/graywolf.md) · [aprs](docs/stacks/aprs.md) · [meshcore](docs/stacks/meshcore.md) · [meshcom](docs/stacks/meshcom.md) · [meshtastic](docs/stacks/meshtastic.md) · [voice](docs/stacks/voice.md) |
| Referenz & Richtlinien | [Härtung](docs/hardening-0.1.md) · [Provenienz](docs/provenance.md) |

Gesamtindex: [`docs/README.md`](docs/README.md).
