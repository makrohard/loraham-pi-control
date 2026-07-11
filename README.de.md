# LoRaHAM Pi Control (`lhpc`) — Kurzanleitung (Deutsch)

`lhpc` installiert, konfiguriert und betreibt die LoRa-Amateurfunk-Stacks auf einem
Raspberry Pi — über eine Kommandozeile und eine lokale Web-Konsole. Es übernimmt den
Quellcode jedes Stacks, baut ihn, startet/stoppt ihn in Abhängigkeitsreihenfolge,
sorgt dafür, dass pro Funkband nur ein Stack das Radio nutzt, schreibt die
Konfiguration jeder App und überwacht und tunt den LoRaHAM-Daemon live.

> Diese deutsche Anleitung richtet sich an Funkamateur-Betreiber. Code,
> Oberflächentexte und die übrigen Dokumente sind auf Englisch (siehe `README.md`).

## Inhalt

- [Installation (self-hosted)](#installation-self-hosted)
- [Wichtige Befehle](#wichtige-befehle)
- [Sicherheit](#sicherheit)

## Installation (self-hosted)

Ein Deployment ist **self-hosted**: Das Laufzeitverzeichnis `~/loraham-pi-control` ist ein
reiner Container, LHPCs eigener Checkout liegt darunter unter `src/loraham-pi-control` (wie
die verwalteten Stacks), das venv außerhalb des Checkouts unter `venv/lhpc`. So sind
`lhpc self-update` und der laufende Code ein Baum.

**Ein-Kommando-Installation** — `install.sh` macht die komplette Neuinstallation aus dem
kanonischen Repository (Branch `main`): Klonen → venv → Editable-Install → `bootstrap` →
`lhpc`-Symlink nach `~/.local/bin` → Web-Konsolen-Dienst aktivieren → Identität prüfen. Nur
für die **Erstinstallation** — verweigert einen vorhandenen Checkout, keine destruktiven
git-Operationen; **Updates später mit `lhpc self-update`**.

```bash
sudo apt install -y nginx     # Voraussetzung für die HTTPS-Konsole (weglassen für nur-lokal)
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
#   oder aus einem Checkout:  ./install.sh
#   Optionen:  --target <verzeichnis>   --no-service (kein Web-Dienst)   --no-path (kein CLI-Symlink)
```

**Konsole öffnen.** Ist `nginx` vorhanden, hat `install.sh` die verwaltete HTTPS-Konsole schon
gestartet (es führt `lhpc webserver init` + `start-service` aus): im Browser
**`https://127.0.0.1:8443/`** öffnen — der Browser warnt vor der selbstsignierten CA, bis du sie
importierst (siehe [`docs/webserver.md`](docs/webserver.md)). Ohne nginx die lokale Konsole mit
**`lhpc web`** → `http://127.0.0.1:8770/` starten. Für den Zugriff von einem anderen Rechner die
[mTLS-Freigabe-Anleitung](docs/webserver.md#expose-to-your-lan-with-mtls--runbook) befolgen.

**Stacks bereitstellen.** Rufzeichen setzen, dann alle Stacks in einem geführten Lauf
installieren/bauen/testen:

```bash
lhpc config operator --callsign DL1ABC    # dein Rufzeichen (erben alle lizenzierten Stacks)
lhpc install-all                          # alle Stacks installieren + bauen + testen
```

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
```
</details>

`lhpc status` zeigt die Controller-Zeile dann als **identity ok**. Für den Dauerbetrieb als
User-Service siehe [`docs/deployment.md`](docs/deployment.md). Einen Stack hinzufügen oder
pflegen? Siehe [`docs/adding-a-stack.md`](docs/adding-a-stack.md).

**Dienst steuern** (`install.sh` betreibt die Web-Konsole standardmäßig als systemd-User-Dienst,
nicht im Terminal):

```bash
systemctl --user stop lhpc-web        # jetzt stoppen (nur vor manuellem `self-update --apply` nötig)
systemctl --user status lhpc-web      # Status prüfen
systemctl --user start lhpc-web       # wieder starten
systemctl --user disable lhpc-web     # Autostart beim Booten abschalten
journalctl --user -u lhpc-web -f      # Live-Logs
```

**Deinstallieren** entfernt **LHPC selbst, nicht die verwalteten Stacks** — Daemon/Apps laufen
weiter, bis du sie stoppst. `./uninstall.sh` entfernt Code, venv, State und den Dienst,
**behält aber `config/`** (Einstellungen + Secrets); `./uninstall.sh --purge` löscht alles,
inkl. Config. Die Skripte liegen im Checkout unter `~/loraham-pi-control/src/loraham-pi-control/`,
nicht im Laufzeitverzeichnis.

**Controller-Status & Updates:** Die Controller-Zeile (erster Eintrag unter **Apps**) und die
Versionsanzeige in der Fußzeile sind bei jedem Seitenaufruf **nur aus dem Cache** — kein
Zugriff auf den Checkout, `.git`, das Netz oder die Identität beim Rendern. Die Konsole prüft
**automatisch im Hintergrund** auf Updates (Standard: alle 12 h, einstellbar über
`[web] update_check_hours` in `config/local.toml`, `0` = aus) — „Update →" erscheint von
selbst in der Fußzeile. **Updaten ist ein Klick**: Nach der Bestätigung schreibt die Konsole
eine Anforderungs-Markierung, die eine statische `lhpc-selfupdate.path`-Unit in einen Lauf der
gesandboxten Helper-Unit umsetzt — diese stoppt die Konsole, wendet das Update an
(Live-Identitätsprüfung, alle Locks), synchronisiert das venv und lässt systemd sie wieder
starten. Die Konsole kann **kein** `systemctl` aufrufen (ihre Unit sperrt den Benutzer-D-Bus),
und One-Click läuft nur, wenn die vier verwalteten Units byte-genau kanonisch sind — ein
manipuliertes Frontend kann so nicht ausbrechen. Manueller Weg:
`systemctl --user stop lhpc-web && lhpc self-update --apply`;
`lhpc self-update --repair-integration` installiert die verwalteten Units (neu). Details:
[`docs/deployment.md`](docs/deployment.md).
Der systemd-Dienst ist **least-privilege**:
Dateisystem nur lesbar außer Laufzeitverzeichnis und `/tmp`, kein breiter Schreibzugriff auf
`$HOME`/`/var`, Benutzer-D-Bus gesperrt; Build-/Tool-Caches liegen laufzeit-eigen unter
`build/tool-cache/` (nie `~/.platformio`, `~/.espressif` oder `~/.cache`).

Rufzeichen einmalig mit `lhpc config operator --callsign <RUFZEICHEN>` (oder in den
**Settings** eines Stacks) setzen; bis dahin nutzen die Apps `N0CALL`.

## Wichtige Befehle

```bash
lhpc status                  # was läuft (nur lesend, kein Netz)
lhpc stack start <stack>     # starten (startet den Daemon bei Bedarf mit)
lhpc stack stop <stack>      # stoppen
lhpc daemon 433 --set TXMODE=DIRECT --yes   # Live-Einstellung (Whitelist)
lhpc test <stack> --tx --yes # ein HF-Testframe pro Band (echtes HF — Dummy-Load!)
lhpc web                     # Web-Konsole, nur 127.0.0.1:8770
```

Verändernde Befehle zeigen erst einen Plan und führen erst nach Bestätigung (oder
`--yes`) aus. **HF wird nie automatisch gesendet.**

## Sicherheit

- `lhpc web` lauscht nur lokal (`127.0.0.1`/`::1`); Aktionen sind POST + CSRF. Für den
  Fernzugriff die HTTPS-/mTLS-Konsole hinter nginx nutzen (siehe [`docs/webserver.md`](docs/webserver.md)).
- Geheimnisse (Rufzeichen, Passwörter, HMAC-Schlüssel) nur in lokaler,
  nicht versionierter Konfiguration (`config/secrets.toml`, Rechte `0600`).

Mehr: `README.md`, `docs/operations.md`, `docs/stacks/`.
