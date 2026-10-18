#!/usr/bin/env python3
"""Check the manifest's `preset_defaults` pins against the daemon's own presets. Reads files, no git.

    check_preset_pins.py <daemon checkout>/loraham_daemon/hardware_profile.cpp

A file param with `preset_defaults` (meshtastic's cs/irq; only file params may carry it, a run param with
it fails to load) names a pin per daemon `--hw` preset; the daemon
source at the manifest pin is the authority for those pins. The Uputronics presets are read from
`profile_fill`'s `if (ce0) { … } else { … }` branch (`p->cs = N;  p->irq = M;`). Exit 0 when every
value matches, 1 on a mismatch or an unreadable preset, 2 on a usage error. Run by CI's pin validation.
"""
import pathlib
import re
import sys
import tomllib

FIELDS = {"cs": "cs", "irq": "irq"}          # manifest param name -> the daemon profile's field


def daemon_pins(cpp: str) -> dict:
    """{preset: {field: pin}} for uputronics-ce0/ce1."""
    m = re.search(r"if \(ce0\) \{(.*?)\} else \{(.*?)\}", cpp, re.DOTALL)
    if not m:
        return {}
    pins = lambda body: {k: int(v) for k, v in re.findall(r"p->(\w+) = (\d+);", body)}
    return {"uputronics-ce0": pins(m.group(1)), "uputronics-ce1": pins(m.group(2))}


def main(argv) -> int:
    if len(argv) != 2:
        print(__doc__.splitlines()[2].strip(), file=sys.stderr)
        return 2
    have = daemon_pins(pathlib.Path(argv[1]).read_text())
    manifest = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "data" / "manifest.example.toml"
    bad, checked = [], 0
    for st in tomllib.loads(manifest.read_text())["stack"]:
        for c in st.get("component", []):
            for p in (c.get("config_file") or {}).get("param", []):
                for preset, value in (p.get("preset_defaults") or {}).items():
                    checked += 1
                    got = have.get(preset, {}).get(FIELDS.get(p["name"], p["name"]))
                    if got is None or str(got) != str(value):
                        bad.append(f"{c['id']}.{p['name']} {preset}: manifest {value}, daemon {got}")
    if bad or not checked:
        print("preset pins differ from the daemon: " + ("; ".join(bad) or "no preset_defaults found"), file=sys.stderr)
        return 1
    print(f"preset pins OK: {checked} values match the daemon's presets")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
