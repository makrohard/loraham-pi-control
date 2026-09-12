"""`rflog.parse_line`: the RF-log line as a record. Exactness is the contract — the page's table,
the CLI's decrypted view and the decoders' keys are all built on these fields — and nothing is
ever dropped: a line the parser does not know is still a record with its raw text."""

from __future__ import annotations

import hashlib

from lhpc.core import rflog

DAEMON = ('2026-09-12T16:03:44.129Z RX rssi=-71.00 snr=9.75 len=52 band=433 hex=82a0a4b0646840 '
          'ascii="....dh@"')
TNC_TX = ('2026-09-12T16:05:01.020Z TX rssi=- snr=- len=52 outcome=ok tnc2="DJ0ABC-7>APRS,WIDE1-1:'
          '=4912.34N/00834.56E-test" hex=82a0 ascii="..."')
UNCONFIRMED = '2026-09-12T16:05:02.000Z TX rssi=- snr=- len=3 outcome=unconfirmed hex=010203 ascii="..."'
MT_RX = ('{"timestamp":1789228997,"rssi":-67,"snr":11.25,"from":1128227980,"to":4294967295,'
         '"size":41,"bytes":"FFFFFFFF8C9F3F43"}')
MT_TX = '{"timestamp":1789228998,"from":0,"to":1128227980,"size":10,"bytes":"0A0B"}'


def _key(raw):
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def test_the_common_line_maps_every_field_exactly():
    assert rflog.parse_line(DAEMON + "\n") == {
        "key": _key(DAEMON), "raw": DAEMON, "ts": "2026-09-12T16:03:44.129Z", "dir": "RX",
        "rssi": -71.0, "snr": 9.75, "len": 52, "outcome": "", "band": "433", "summary": "",
        "hex": "82a0a4b0646840", "ascii": "....dh@"}
    r = rflog.parse_line(TNC_TX)
    assert (r["dir"], r["rssi"], r["snr"], r["outcome"], r["band"]) == ("TX", None, None, "ok", "")
    assert r["summary"] == "DJ0ABC-7>APRS,WIDE1-1:=4912.34N/00834.56E-test"
    assert rflog.parse_line(UNCONFIRMED)["outcome"] == "unconfirmed"


def test_meshtastics_json_trace_maps_onto_the_same_keys():
    r = rflog.parse_line(MT_RX)
    assert r["ts"] == "2026-09-12T16:03:17Z" and r["dir"] == "RX"
    assert (r["rssi"], r["snr"], r["len"]) == (-67.0, 11.25, 41)
    assert r["summary"] == "!433f648c → ^all" and r["hex"] == "ffffffff8c9f3f43"
    assert r["ascii"] == "" and r["band"] == "" and r["outcome"] == ""
    t = rflog.parse_line(MT_TX)
    assert t["dir"] == "TX" and t["summary"] == "local → !433f648c" and t["rssi"] is None


def test_unknown_lines_are_kept_raw_and_never_raise():
    for line in ("", "garbage", "{not json", "[1, 2]", "{\"timestamp\": \"x\"}",
                 '2026-09-12T16:03:44.129Z RX rssi=-71.00 len=52 hex=00 ascii="."'):
        r = rflog.parse_line(line)
        assert r["raw"] == line and r["key"] == _key(line)
        assert set(r) == {"key", "raw"} or "ts" in r
    assert rflog.parse_line("{\"timestamp\": \"x\"}")["ts"] == ""      # a bad stamp, still a record
    assert rflog.parse_line('{"from": 1, "size": "big"}')["len"] == 0      # a bad size, still a record


def test_the_key_is_the_line_and_only_the_line():
    assert rflog.parse_line(DAEMON)["key"] == rflog.parse_line(DAEMON + "\n")["key"]
    assert rflog.parse_line(DAEMON)["key"] != rflog.parse_line(DAEMON + " ")["key"]
    assert [r["raw"] for r in rflog.parse_lines([DAEMON, "x"])] == [DAEMON, "x"]
