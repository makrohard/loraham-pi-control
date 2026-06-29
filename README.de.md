# LoRaHAM Pi Control (`lhpc`) — Kurzanleitung (Deutsch)

`lhpc` installiert, konfiguriert und betreibt die LoRa-Amateurfunk-Stacks auf einem
Raspberry Pi — über eine Kommandozeile und eine lokale Web-Konsole. Es übernimmt den
Quellcode jedes Stacks, baut ihn, startet/stoppt ihn in Abhängigkeitsreihenfolge,
sorgt dafür, dass pro Funkband nur ein Stack das Radio nutzt, schreibt die
Konfiguration jeder App und überwacht und tunt den LoRaHAM-Daemon live.

> Diese deutsche Anleitung richtet sich an Funkamateur-Betreiber. Code,
> Oberflächentexte und die übrigen Dokumente sind auf Englisch (siehe `README.md`).

## Installation

```bash
git clone https://github.com/makrohard/loraham-pi-control.git
cd loraham-pi-control
python3 -m venv .venv && . .venv/bin/activate
pip install -e .

lhpc bootstrap              # Laufzeitverzeichnis ~/loraham-pi-control anlegen
lhpc install daemon --yes   # Quelle übernehmen/prüfen …
lhpc build daemon           # … dann bauen
```

Rufzeichen einmalig auf der Web-Konfigseite setzen; bis dahin nutzen die Apps
`N0CALL`.

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

- Web-Konsole lauscht nur lokal (`127.0.0.1`/`::1`); Aktionen sind POST + CSRF.
- Geheimnisse (Rufzeichen, Passwörter, HMAC-Schlüssel) nur in lokaler,
  nicht versionierter Konfiguration (`config/secrets.toml`, Rechte `0600`).

Mehr: `README.md`, `docs/operations.md`, `docs/stacks/`.
