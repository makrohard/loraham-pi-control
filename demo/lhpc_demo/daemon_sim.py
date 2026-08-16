"""In-memory simulated LoRaHAM daemon telemetry for the demo — the one piece that makes the
console feel LIVE without a real radio or daemon process. Produces time-varying STATUS/STATS/
CHANNEL dicts and a rolling RX/TX packet feed, in the shape the real daemon's CONF socket
returns (so the dashboard's live monitor renders it unchanged). No sockets, no processes."""
from __future__ import annotations

import math

_CALLS = ("DL0LAB", "DL1ABC", "OE3XYZ", "DK5HH", "DB0RES", "PA3FOO", "F4ABC", "G7XYZ")


_T0 = None


def _elapsed() -> float:
    """Seconds since the daemon telemetry first started — counters/feed grow from here, so
    they read as small realistic numbers (not the Unix epoch)."""
    global _T0
    try:
        import time
        t = time.time()                          # Pyodide maps this to Date.now()
    except Exception:
        t = 0.0
    if _T0 is None:
        _T0 = t
    return max(0.0, t - _T0)


def _live_rssi(band: str) -> int:
    """A plausibly wandering live noise-floor RSSI in dBm (~ -106..-78), phase-shifted
    per band so 433 and 868 don't read identically."""
    e = _elapsed()
    ph = 0.0 if band == "433" else 1.7
    return int(-92 + 14 * math.sin(e / 4.0 + ph))


def status(band: str) -> dict:
    e = _elapsed()
    # TX active: mostly idle, a brief burst every ~11 s (matches the beacon cadence in feed()).
    tx_active = "1" if int(e) % 11 == 0 else "0"
    return {"RADIO": "READY", "TXMODE": "MANAGED", "TX": tx_active, "TXRESULT": "OK",
            "TXQUEUE": "0", "CADMONITOR": "1", "CADRSSI": "-90", "CADTXAFTERTIMEOUT": "0",
            "CADWAIT": "1500", "CADIDLE": "28"}


def channel(band: str) -> dict:
    e = _elapsed()
    # CAD state: idle most of the time, "DETECTED" briefly when a packet is inbound.
    cadstate = "DETECTED" if int(e) % 5 == 0 else "IDLE"
    # Last received packet RSSI — stronger than the noise floor, drifting on its own phase.
    pktrssi = int(-74 + 9 * math.sin(e / 3.3 + (0.0 if band == "433" else 0.9)))
    return {"FREQ": "433.775000" if band == "433" else "868.100000", "SF": "12",
            "BW": "125.0", "CR": "5", "CRC": "1", "PREAMBLE": "8", "SYNC": "0x12",
            "POWER": "17", "LDRO": "AUTO", "LIVERSSI": str(_live_rssi(band)),
            "CADSTATE": cadstate, "PACKETRSSI": str(pktrssi)}


def stats(band: str) -> dict:
    e = _elapsed()
    n = int(e)
    snr = round(6.0 + 3.0 * math.sin(e / 2.7), 1)
    rx = 128 + n // 2                             # a packet ~ every 2 s, from a small base
    txok = 24 + n // 7                            # a beacon ~ every 7 s
    txerr = n // 130                              # the rare failed transmit
    return {"RSSI": str(_live_rssi(band)), "SNR": str(snr), "RX": str(rx),
            "TXOK": str(txok), "TXERR": str(txerr), "UPTIME": str(n)}


def feed(band: str, lines: int) -> list:
    """A rolling tail of RX/TX log lines (a new one ~ every 2 s), in the real daemon's
    `[RX<band>]` / `[TX<band>]` token format the feed filter expects."""
    n = max(1, min(lines, 60))
    base = int(_elapsed() // 2) + n              # grow from a small base so seq starts sane
    out = []
    for i in range(n):
        k = base - (n - 1 - i)
        if k < 0:
            continue
        if k % 5 == 0:
            out.append(f"[TX{band}] seq={k} DL0LAB-9>APDR16: !4812.34N/01623.45E# "
                       f"beacon  result=TXOK")
        else:
            call = _CALLS[k % len(_CALLS)]
            rssi = -92 + (k * 7) % 22
            out.append(f"[RX{band}] seq={k} RX_PACKET {call}>APRS "
                       f"rssi={rssi} snr={6 + k % 4}: msg #{k}")
    return out[-n:]
