#!/usr/bin/env python3
"""mcset.py: the kit's pre-step M helper (from the RF sheet's MCSET-PRESTEP-v4), run ON A BOX from stdin, one instance per call.

argv: <job> <sid> <source> <rid> <keys> <web port> <runtime>; the runner puts one line OVERRIDE = '<value>' before this
text on stdin (never in an argv). It copies the job's keys from the stack's source file (config/stacks/<source>) into the
instance <sid>@<rid> through `lhpc config <sid>@<rid>` (M0 list, M1 each key's parameter, KEEP a value the instance file
holds, EQUAL a value already set, U-rec the undo, M2 set, M3 read back with no other list line changed, M4 the web page
shows it). OVERRIDE "-" (the source's value), "global" (the same) or one value for the first key. The changes are KEPT
(the lab's settings); its own undo runs only on its own M2/M3 failure. Prints one verdict line
"JOB <job> <inst> PASS|NOOP|STOP|UNRESOLVED[: why]" and exits 0; any other exit = the instance's state is unknown.
"""
import html
import os
import re
import ssl
import subprocess
import sys
import tomllib
import urllib.error
import urllib.request

job, sid, src, rid, keys, port = sys.argv[1:7]
override = globals().get("OVERRIDE", "-")
TIMEOUT = globals().get("LH_TIMEOUT", 600)            # seconds per lhpc call (tests set it on the stdin prelude)
RT = os.path.expanduser(sys.argv[7])
LH = RT + "/venv/lhpc/bin/lhpc"
ST = RT + "/config/stacks"
inst = f"{sid}@{rid}"
# read-only checks, never copied: MeshCore in a repeater mode relays every frame it hears (sends no tx counts), so its job
# READS the instance's current mode and STOPs before any set unless it is one of these; a job asking to copy it STOPs
CHECK = {"meshcore": ("mode", ("chat",))}


def mask(t):
    t = re.sub(r"-?[0-9]{1,3}\.[0-9]{5,}", lambda m: "#" * len(m.group(0)), t)
    return re.sub(r"![0-9a-f]{8}", "!########", t)


def say(t):
    print(mask(t), flush=True)


def done(v, why=""):
    say(f"JOB {job} {inst} {v}" + (f": {why}" if why else ""))
    sys.exit(0)


def first(t, n):
    return ((t.strip().splitlines() or ["?"])[0])[:n]


def lh(*a):
    """One `lhpc config` call: (rc, output). A hang (rc 124) or a call that cannot start (rc 127) is a failed call, so
    a failed set goes through the undo like any other (an exception here would skip it)."""
    try:
        p = subprocess.run([LH, "config", inst, *a], capture_output=True, text=True, timeout=TIMEOUT, check=False)
        rc, t = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired:
        rc, t = 124, f"TIMEOUT after {TIMEOUT} s"
    except OSError as e:
        rc, t = 127, f"NOT STARTED: {e}"
    say(f"--- lhpc config {inst} {' '.join(a)} (rc {rc})\n{t}")
    return rc, t


def page():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    try:
        r = urllib.request.urlopen(f"https://127.0.0.1:{port}/stacks?radio={rid}&cfg={sid}", context=c, timeout=60)
        return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except OSError as e:
        return 0, str(e)


def tstr(v):
    return ("true" if v else "false") if isinstance(v, bool) else str(v)


def shown(t):
    vs = [ln.split("=", 1)[1].strip() for ln in t.splitlines() if ln.startswith("  value   = ")]
    return None if len(vs) != 1 else ("" if vs[0] == "(empty)" else vs[0])


def load(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def main():
    srcf = os.path.join(ST, src)
    if not os.path.isfile(srcf):
        done("NOOP", f"no source file {src}")
    data = load(srcf)
    want = [k for k in keys.split(",") if k]
    if sid in CHECK and "file_" + CHECK[sid][0] in want:
        done("STOP", f"M1: file_{CHECK[sid][0]} is checked read-only, never copied; nothing set")
    vals = {k: tstr(data[k]) for k in want if k in data and not isinstance(data[k], (dict, list))}
    if override not in ("-", "global"):
        vals = {want[0]: override}                   # J1: the per-radio value given at the start
    if not vals:
        done("NOOP", "none of the job's keys in the source")
    gf = os.path.join(ST, sid + ".toml")
    ga = sum(1 for k in load(gf) if k.startswith("autostart_")) if os.path.isfile(gf) else 0
    inf = os.path.join(ST, inst + ".toml")
    pref = os.path.isfile(inf)
    idat = load(inf) if pref else {}
    say(f"M0 source={src} keys={','.join(vals)} gauto={ga} prefile={'yes' if pref else 'no'}")
    rc, pre = lh("list")
    if rc != 0:
        done("STOP", "M0: the instance list is refused (" + first(pre, 160) + ")")
    st, p0 = page()
    say(f"--- PAGE0 status {st} ({len(p0)} bytes)")
    if st != 200:
        done("STOP", f"M0: PAGE0 status {st}")
    from lhpc.core.services import ControllerService
    fields = ControllerService().config_param_fields(sid)
    if sid in CHECK:                                      # before any set: the instance's own value, read only
        key, ok = CHECK[sid]
        m = [f for f in fields if f["kind"] == "file" and f["key"] == key]
        if len(m) != 1:
            done("STOP", f"M1: the {key} check maps to {len(m)} parameters of {sid}; nothing set")
        rc, t = lh(m[0]["name"])
        cur = shown(t)
        if rc != 0 or not t.startswith("OK") or cur not in ok:
            done("STOP", f"M1: the node's {key} reads '{cur}', not {'/'.join(ok)}: row {job[1:]}'s node would relay frames "
                         "the kit never counts; nothing set")
        say(f"CHECK {key}: {cur} (read only)")
    plan = []
    for k, v in vals.items():
        m = [f for f in fields if (("file_" + f["key"]) if f["kind"] == "file" else f["key"]) == k]
        if len(m) != 1:
            done("STOP", f"M1: '{k}' maps to {len(m)} parameters of {sid}")
        f = m[0]
        rc, t = lh(f["name"])
        pv = shown(t)
        if rc != 0 or not t.startswith("OK"):
            done("STOP", f"M1: '{f['name']}' is refused ({first(t, 120)})")
        if pv is None:
            done("STOP", f"M1: '{f['name']}' shows no single value line")
        if f'name="{f["field"]}"' not in p0:
            done("STOP", f"M1: the field {f['field']} is absent from PAGE0")
        if k in idat:
            say(f"KEEP {k}: the instance file holds it (a 0.14.0 entry wins): SKIP-SET")
            continue
        if pv == v:
            say(f"EQUAL {k}: already the value")
            continue
        plan.append((f["name"], f["field"], v, pv, f["flag"]))
    if not plan:
        done("PASS", "nothing to set (kept or equal)")
    reset = not pref and ga == 0
    say("U-rec: " + (f"lhpc config {inst} --reset --yes" if reset
                     else "; ".join(f"lhpc config {inst} {n} <its PRE value>" for n, *_ in plan)))

    def undo(why):
        if reset:
            lh("--reset", "--yes")
        else:
            for n, _f, _v, pv, _b in plan:
                lh(n, pv)
        rc, now = lh("list")
        back = rc == 0 and now == pre
        done("STOP" if back else "UNRESOLVED", why + ("; the undo restored PRE" if back else "; the undo did NOT restore PRE"))

    for n, _f, v, _pv, _b in plan:
        rc, _t = lh(n, v)
        if rc != 0:
            undo(f"M2: set {n} rc {rc}")
    rc, post = lh("list")
    if rc != 0:
        undo("M3: the list after the set is refused")
    names = [n for n, *_ in plan]
    bad = [ln for ln in set(pre.splitlines()) ^ set(post.splitlines())
           if not any(re.match(rf"^    {re.escape(n)}( \*)? = ", ln) for n in names)]
    if bad:
        undo(f"M3: {len(bad)} other list line(s) changed")
    for n, _f, v, _pv, _b in plan:
        rc, t = lh(n)
        if rc != 0 or shown(t) != v:
            undo(f"M3: {n} does not read back its value")
    st, p1 = page()
    say(f"--- PAGE1 status {st} ({len(p1)} bytes)")
    if st != 200:
        done("STOP", f"M4: page status {st} (the set is kept; analysis first)")
    for n, fld, v, _pv, b in plan:
        if b:
            say(f"M4 {n}: a flag (checkbox): its value attribute is not asserted")
            continue
        if f'name="{fld}" value="{html.escape(v, quote=True)}"' not in p1:
            done("STOP", f"M4: the field {fld} does not show the value (the set is kept; analysis first)")
    done("PASS", "set " + ",".join(names))


main()
