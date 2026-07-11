# LoRaHAM Pi Control (`lhpc`) — Anleitung (Deutsch)

Die LoRa-Amateurfunk-Software-Stacks auf einem Raspberry Pi von einer Stelle aus
installieren, konfigurieren und betreiben — über eine Terminal-CLI und eine lokale Web-Konsole.

Die Stacks des Pi teilen sich einen Satz LoRa-Funkgeräte. `lhpc` übernimmt den Quellcode jedes
Stacks, baut ihn, startet und stoppt ihn in Abhängigkeitsreihenfolge, sorgt dafür, dass immer nur
ein Stack ein Funkband nutzt, schreibt die Konfiguration jeder App von einer Stelle aus und
überwacht und tunt den LoRaHAM-Daemon live.

> Diese deutsche Anleitung richtet sich an Funkamateur-Betreiber. Code, Oberflächentexte und die
> übrigen Dokumente sind auf Englisch (siehe `README.md`).

## Inhalt

- [Stacks](#stacks)
- [Installation (self-hosted)](#installation-self-hosted)
- [CLI](#cli)
- [Web-Konsole](#web-konsole)
- [Deployment & Selbst-Update](#deployment--selbst-update)

## Stacks

| Stack | Band | Was es ist |
|---|---|---|
| `daemon` | 433 + 868 | LoRaHAM-Daemon — besitzt die Funkgeräte, stellt pro Band Sockets bereit. Die Basis für die App-Stacks. |
| `chat` | 433 | APRS-/Chat-TUI (interaktiv — im Terminal ausführen). |
| `igate` | 433 | APRS-iGate. |
| `voice` | 433 / 868 | LoRa-Sprache (GUI). |
| `kiss` | 433 / 868 | KISS-TNC über TCP (Port 8001). |
| `meshtastic` | 433 / 868 | Meshtastic (rootless `meshtasticd`; Web 9443, API 4403). Nutzt das Funkgerät direkt. |
| `meshcom` | 433 | MeshCom-Firmware in QEMU, an den Daemon gebrückt (Web 18083, Bridge 7000). |
| `meshcore` | 868 | MeshCore-Pi-Node (TCP 5000); optional CLI + Node-GUI. |

Daemon-gestützte Stacks (chat, igate, voice, kiss, meshcom, meshcore) starten den Daemon
automatisch. Meshtastic steuert das Funkgerät selbst und kann daher nicht laufen, während der
Daemon dessen Band bedient — `lhpc` blockiert den Konflikt.

## Installation (self-hosted)

Benötigt Python 3.11+. Ein Deployment ist **self-hosted**: Das Laufzeitverzeichnis
`~/loraham-pi-control` ist ein reiner Container, und LHPCs eigener Checkout liegt *darunter* unter
`src/loraham-pi-control` (wie die verwalteten Stacks), das venv AUSSERHALB des Checkouts unter
`venv/lhpc`. So sind `lhpc self-update` und der laufende Code ein Baum.

**Ein-Kommando-Installation** — `install.sh` macht die komplette Neuinstallation aus dem
kanonischen Repository (Branch `main`): Klonen → venv → Editable-Install → `bootstrap` →
`lhpc`-Symlink nach `~/.local/bin` → Web-Konsolen-Dienst aktivieren → Controller-Identität prüfen.
Nur für die **Erstinstallation** — verweigert einen vorhandenen Checkout, keine destruktiven
git-Operationen; **Updates später mit `lhpc self-update`**.

```bash
sudo apt install -y nginx     # Voraussetzung für die HTTPS-Konsole (weglassen für nur-lokal)
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   oder aus einem Checkout:  ./install.sh
#   Optionen:  --target <verzeichnis>   --no-service (kein Web-Dienst)   --no-path (kein CLI-Symlink)
```

**Konsole öffnen.** Ist `nginx` vorhanden, hat `install.sh` die verwaltete HTTPS-Konsole schon
gestartet (es führt `lhpc webserver init` + `start-service` für dich aus): im Browser
**`https://127.0.0.1:8443/`** öffnen — der Browser warnt vor der selbstsignierten CA, bis du sie
importierst (siehe [`docs/webserver.md`](docs/webserver.md)). Ohne nginx die lokale Konsole mit
**`lhpc web`** → `http://127.0.0.1:8770/` starten. Für den Zugriff von einem anderen Rechner die
[mTLS-Freigabe-Anleitung](docs/webserver.md#expose-to-your-lan-with-mtls--runbook) befolgen.

**Stacks bereitstellen.** Rufzeichen setzen, dann alle Stacks in einem geführten Lauf
installieren/bauen/testen:

```bash
lhpc config operator --callsign DL1ABC    # dein Rufzeichen (erben alle lizenzierten Stacks)
lhpc install-all                          # alle Stacks installieren + bauen + testen (auch --source, --no-tests, --tx)
```

Dasselbe geht über die Seite **Stacks** der Web-Konsole. Danach starten, was du brauchst
(`lhpc stack start <stack>` oder der Start-Button).

<details><summary>Oder von Hand</summary>

```bash
# 1. LHPC in src/ des Laufzeitverzeichnisses klonen (das macht es self-hosted)
mkdir -p ~/loraham-pi-control/src
git clone https://github.com/makrohard/loraham-pi-control.git \
    ~/loraham-pi-control/src/loraham-pi-control

# 2. venv AUSSERHALB des Checkouts anlegen und installieren
python3 -m venv ~/loraham-pi-control/venv/lhpc
~/loraham-pi-control/venv/lhpc/bin/pip install -e ~/loraham-pi-control/src/loraham-pi-control

# 3. Laufzeit-Layout + Default-Config anlegen (nur Eigentümer, Modus 0700)
~/loraham-pi-control/venv/lhpc/bin/lhpc bootstrap --yes

# 4. Alle Stacks übernehmen + bauen + testen (venv/lhpc/bin in den PATH aufnehmen)
export PATH="$HOME/loraham-pi-control/venv/lhpc/bin:$PATH"
lhpc config operator --callsign DL1ABC   # dein Rufzeichen
lhpc install-all                         # geführt: installieren + bauen + testen
lhpc web                                 # http://127.0.0.1:8770/  (lokale Konsole)
```
Für die HTTPS-/mTLS-Konsole statt `lhpc web` nginx installieren und
[`docs/webserver.md`](docs/webserver.md) folgen.
</details>

`lhpc status` zeigt die Controller-Zeile dann als **identity ok**. Für den Dauerbetrieb als
User-Service siehe [`docs/deployment.md`](docs/deployment.md) (die Vorlage `deploy/lhpc-web.service`
nutzt dieses Layout bereits). Ein One-Click-Update stoppt und startet die Konsole selbst; nur das
manuelle `lhpc self-update --apply` erfordert, dass sie vorher gestoppt ist.

Rufzeichen einmalig mit `lhpc config operator --callsign <RUFZEICHEN>` (oder in den **Settings**
eines Stacks) setzen; bis dahin nutzen die Apps `N0CALL`. Geheimnisse (Passwörter, HMAC-Schlüssel)
liegen nur in `~/loraham-pi-control/config/secrets.toml`.

**Dienst steuern** — `install.sh` betreibt die Web-Konsole als systemd-User-Dienst (nicht im
Terminal); der Installer gibt diese am Ende ebenfalls aus:

```bash
systemctl --user stop lhpc-web        # jetzt stoppen (nur vor manuellem `self-update --apply` nötig)
systemctl --user status lhpc-web      # Status prüfen
systemctl --user start lhpc-web       # wieder starten
systemctl --user disable lhpc-web     # Autostart beim Booten abschalten
journalctl --user -u lhpc-web -f      # Live-Logs
```

**Deinstallieren** entfernt **LHPC selbst, nicht die verwalteten Stacks** — Daemon/Apps laufen
weiter, bis du sie stoppst. `./uninstall.sh` entfernt Code, venv, State und den Dienst, **behält
aber `config/`** (Einstellungen + Secrets); `./uninstall.sh --purge` löscht alles, inkl. Config.
(`--target <verzeichnis>`, `--yes` überspringt die Rückfrage.) Die Skripte liegen im Checkout unter
`~/loraham-pi-control/src/loraham-pi-control/`, nicht im Laufzeitverzeichnis.

> An LHPC selbst arbeiten? Irgendwo klonen und `pip install -e .` in einem venv für einen
> Dev-Checkout — diese Instanz ist absichtlich *nicht* self-hosted (die Controller-Zeile zeigt
> „not self-hosted"). Von dort committen und pushen; self-hosted wie oben deployen.
>
> Einen Stack hinzufügen oder pflegen? Siehe [`docs/adding-a-stack.md`](docs/adding-a-stack.md).

## CLI

```bash
lhpc status                  # was läuft (nur lesend, begrenzt — kein Netz)
lhpc list                    # Stacks im Manifest
lhpc explain <stack>         # Komponenten, Startreihenfolge, Ressourcen

lhpc install <stack> --yes   # Quelle übernehmen/prüfen
lhpc build <stack>           # bauen
lhpc config <stack> call DL1ABC  # eine Stack-Einstellung setzen (z. B. Rufzeichen) — validiert
lhpc stack start <stack>     # starten (startet den Daemon bei Bedarf mit)
lhpc stack stop <stack>      # stoppen
lhpc logs <stack>            # Komponenten-Log anzeigen

lhpc daemon <433|868>                       # RSSI / Stats / CAD überwachen
lhpc daemon 433 --set TXMODE=DIRECT --yes   # Live-Einstellung setzen (Whitelist)
lhpc test <stack> --tx --yes                # ein HF-Testframe pro Band (echtes HF — Dummy-Load!)

lhpc update | uninstall <stack>
```

Verändernde Befehle zeigen erst einen Plan und führen erst nach Bestätigung (oder `--yes`) aus.
**HF wird nie automatisch gesendet.** Vollständige Befehlsreferenz: [`docs/cli.md`](docs/cli.md).

## Web-Konsole

```bash
lhpc web                     # http://127.0.0.1:8770/  (nur lokal)
```

- **Dashboard** — pro Band: der Daemon-Monitor (Live-RSSI/Stats), die auf diesem Band laufenden
  Stacks und eine Steuerung, um einen weiteren zu starten.
- **Stack-Seiten** — Install / Build / Start / Stop / Test / Update / Uninstall, jeweils mit Plan
  und Bestätigung. Interaktive (TUI-)Apps zeigen den selbst auszuführenden Befehl; GUI-/Headless-Apps
  starten und stoppen direkt.
- **Settings** — Einstellungen pro Stack (Rufzeichen, Frequenzen, Presets …), in die jeweils eigene
  Config-Datei der App geschrieben.

Dieser einfache `lhpc web`-Modus ist **nur lokal** (POST-Aktionen sind CSRF-geschützt,
`Content-Security-Policy: default-src 'self'`) und wird nicht ins Netz freigegeben. Für den Zugriff
von einem anderen Rechner das produktive HTTPS-+-mTLS-Frontend (nginx) nutzen — siehe
[`docs/webserver.md`](docs/webserver.md).

## Deployment & Selbst-Update

Das unterstützte Deployment ist **self-hosted**: Das Laufzeitverzeichnis `~/loraham-pi-control` ist
ein reiner Container, LHPCs eigener Checkout liegt darunter unter `src/loraham-pi-control` (neben den
verwalteten Stack-Quellen), das venv unter `venv/lhpc`. Die systemd-Unit setzt `LHPC_RUNTIME_ROOT`
explizit. LHPCs Checkout ist eine **Controller-Identität** — beobachtbar und via `lhpc self-update`
aktualisierbar, aber nie installiert/gebaut/gestartet/gelöscht usw.; jeder generische Befehl darauf
wird verweigert und verweist auf `lhpc self-update`, und `lhpc status` zeigt eine eigene
`[controller]`-Zeile.

In der Web-Konsole sind die Controller-Zeile (erster Eintrag unter **Stacks**) und die
Versionsanzeige in der Fußzeile bei jedem Seitenaufruf **nur aus dem Cache** — kein Zugriff auf den
Checkout, `.git`, das Netz oder die Identität beim Rendern. Die Konsole prüft **automatisch im
Hintergrund** auf Updates (Standard: alle 12 h, einstellbar über `[web] update_check_hours`, `0` =
aus) — „Update →" erscheint von selbst in der Fußzeile; **„Check for updates"** tut dasselbe auf
Anforderung.

**Updaten ist ein Klick**: Nach der Bestätigung schreibt die Konsole eine Anforderungs-Markierung,
die eine statische `lhpc-selfupdate.path`-Unit in einen Lauf der gesandboxten Helper-Unit umsetzt —
diese stoppt die Konsole, wendet das Update an (Live-Identitätsprüfung, alle Locks), synchronisiert
das venv und lässt systemd sie wieder starten. Die Konsole kann **kein** `systemctl` aufrufen (ihre
Unit sperrt den Benutzer-D-Bus), und One-Click läuft nur, wenn die vier verwalteten Units byte-genau
kanonisch sind — ein manipuliertes Frontend kann so nicht ausbrechen. Manueller Weg:
`systemctl --user stop lhpc-web && lhpc self-update --apply`; `lhpc self-update --repair-integration`
installiert die verwalteten Units (neu). Details: [`docs/deployment.md`](docs/deployment.md).

Die systemd-Unit des Web-Dienstes ist **least-privilege**: Dateisystem nur lesbar außer
Laufzeitverzeichnis und `/tmp`, kein breiter Schreibzugriff auf `$HOME`/`/var`, Benutzer-D-Bus
gesperrt, Build-/Tool-Caches laufzeit-eigen unter `build/tool-cache/` (nie `~/.platformio`,
`~/.espressif` oder `~/.cache`). Siehe [`docs/deployment.md`](docs/deployment.md) und das
Umzugs-Runbook in [`docs/deployment-migration.md`](docs/deployment-migration.md).

**Backup:** Einstellungen, Secrets und Zertifikate liegen alle unter
`~/loraham-pi-control/config/` (plus Known-Working-Datensätze in `profiles/`); mit einem einzigen
`tar` sichern — siehe [`docs/operations.md`](docs/operations.md#backup--restore).
