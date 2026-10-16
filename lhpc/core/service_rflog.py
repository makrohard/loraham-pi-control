"""ControllerService mixin: the RF-log console, its switches, clearing, records and decoding.

Adapters import lhpc.core.services only. Split from service_lifecycle_ops.py unchanged."""
from __future__ import annotations

import os

from . import meshcore_mode as _meshcore_mode
from . import rflog as _rflog
from . import runtime_fs, validators
from .model import RunState
from .paths import PathContainmentError
from .service_base import ActionResult
from .snapshot_memo import invalidates_snapshot


class RflogOpsMixin:

    # ------------------------------------------------------------------
    # RF logs — everything reads `rflog.REGISTRY`; nothing is rediscovered from the manifest.
    # ------------------------------------------------------------------

    def _rflog_path(self, job: str):
        return self._paths.under("logs", job)

    def _rflog_lock(self, job: str):
        """An exclusive flock per RF job, held across a native roll or a Clear. Two console
        workers (or the CLI and a worker) can otherwise both pass the size check; the second
        would copy the already-truncated live file over `.1` and the retained history is gone.
        The size is re-checked UNDER the lock for exactly that reason."""
        import fcntl
        from contextlib import contextmanager

        @contextmanager
        def _held():
            fh = runtime_fs.open_lock(self._paths, self._paths.under("state", "locks", f"rflog-{job}.lock"))
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                fh.close()                       # releases the flock
        return _held()

    def _rflog_tail(self, p, lines: int) -> list[str]:
        """The last `lines` of the logical concatenation <job>.1 + <job> — exactly `lines` in
        total, so the page does not go blank right after a rollover."""
        cur = runtime_fs.tail(self._paths, p, lines)
        if len(cur) >= lines:
            return cur
        prev = runtime_fs.tail(self._paths, p.with_name(_rflog.previous(p.name)), lines - len(cur))
        return prev + cur

    def _rflog_roll_native(self, p) -> None:
        """meshtasticd's TraceFile is append-only and its own rotation belongs to another
        facility, so LHPC rolls it OPPORTUNISTICALLY — at stack start and when a read finds it
        over the cap: at most the last ~5 MB go to <job>.1, the live file is truncated in place
        (same inode: meshtasticd keeps appending). Not a hard cap; the docs say so."""
        st = runtime_fs.stat_leaf_nofollow(self._paths, p)
        if st is None or st.st_size <= _rflog.MAX_BYTES:
            return                               # cheap pre-check, no lock
        try:
            with self._rflog_lock(p.name):
                self._rflog_roll_native_locked(p)
        except (OSError, PathContainmentError):
            pass

    def _rflog_roll_native_locked(self, p) -> None:
        try:
            fd = runtime_fs._open_leaf(self._paths, p, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                                       0o644, create_dirs=False)
        except (OSError, PathContainmentError):
            return
        try:
            runtime_fs._require_regular_fd(fd, p)
            size = os.fstat(fd).st_size
            if size <= _rflog.MAX_BYTES:
                return                           # a concurrent roll or Clear got here first
            tail = os.pread(fd, _rflog.MAX_BYTES, max(0, size - _rflog.MAX_BYTES))
            nl = tail.find(b"\n")
            tail = tail[nl + 1:] if nl >= 0 else tail          # whole lines only
            with runtime_fs.open_log_truncate(self._paths, p.with_name(_rflog.previous(p.name))) as prev:
                prev.write(tail.decode("utf-8", "replace"))
            os.ftruncate(fd, 0)
        except (OSError, PathContainmentError):
            pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    @invalidates_snapshot
    def rflog_roll_native_all(self) -> None:
        """The console's pass: try the roll on every NATIVE RF log (the Meshtastic trace, whose
        writer never rolls it). Non-native RF logs are rotated by their own writers and never touched
        here. A roll that fails is left for the next pass. Never raises."""
        for e in _rflog.REGISTRY:
            if e.native:
                for _band, job in e.jobs:
                    try:
                        self._rflog_roll_native(self._rflog_path(job))
                    except Exception:
                        pass

    def _rflog_roll_at_start(self, comp_id: str) -> None:
        for e in _rflog.REGISTRY:
            if e.native and e.writer == comp_id:
                for _band, job in e.jobs:
                    try:
                        self._rflog_roll_native(self._rflog_path(job))
                    except (OSError, PathContainmentError):
                        pass

    def rflog_job(self, target: str, job) -> dict | None:
        """The registry entry behind `/logs/<target>?job=<job>` — None unless `job` is a
        registered RF log AND `target` is its writer. The one authorization for the RF viewer,
        the switcher and Clear (no prefix semantics: `rf-made-up.log` is nobody's)."""
        if not job:
            return None
        rf = _rflog.by_job(str(job))
        if rf is None or rf[0].writer != target:
            return None
        e, band = rf
        return {"surface": e.surface, "owner": e.owner, "writer": e.writer, "band": band,
                "job": job, "label": f"{e.surface} {band}".strip(), "native": e.native,
                "decoder": e.decoder}

    def rflog_running(self, job: str) -> bool:
        """The badge: the WRITER's run state (the daemon's per band), never a job marker."""
        rf = _rflog.by_job(str(job or ""))
        if rf is None:
            return False
        e, band = rf
        if e.writer == self.DAEMON_ID:
            try:
                return bool(self.daemon_view(band).reachable)
            except Exception:
                return False
        for ss in self.build_snapshot(sources=False).stacks:     # a run state: no git per poll
            st = ss.components.get(e.writer)
            if st is not None:
                return st.run_state in (RunState.RUNNING, RunState.DEGRADED)
        return False

    def _rflog_entries(self) -> list:
        """The registry entries this manifest installs, in registry order."""
        return [e for e in _rflog.REGISTRY if self.stack(e.surface) is not None]

    def _rflogs_for_band(self, band: str, running: list) -> list[dict]:
        """The dashboard's RF-log links for a band: the daemon's file for it, then the file of
        every running stack the registry knows."""
        up = {s["id"] for s in running}
        out = []
        for e in self._rflog_entries():
            if e.banded:
                out.append({"label": f"{e.surface} {band}", "target": e.writer, "job": e.job(band)})
            elif e.surface in up:
                out.append({"label": e.surface, "target": e.writer, "job": e.job()})
        return out

    def rflog_switcher(self, job: str, band: str = "") -> dict:
        """The log page's two rows for a registered `job`: every band, and the RF logs of the
        shown band — the daemon's file for it plus every registered stack whose declared bands
        include it. The shown band is `band` when valid, else the job's own band (the daemon),
        else the band the shown stack runs on (its declared band when it is not running), else
        the first active band."""
        e, jband = _rflog.by_job(job)
        stack_band = next((c.band for c in self.stack(e.surface).components if c.band), "")
        shown = (band if band in self.RADIO_BANDS else "") or jband \
            or self.running_band(e.surface, stack_band) or (self.active_bands() or self.RADIO_BANDS)[0]
        stacks = []
        for x in self._rflog_entries():
            if x.banded:
                xjob, xband = x.job(shown), shown
            elif shown in self._declared_bands(self.stack(x.surface)):
                xjob, xband = x.job(), ""
            else:
                continue
            stacks.append({"target": x.writer, "job": xjob, "surface": x.surface, "band": xband,
                           "label": f"{x.surface} {xband}".strip(), "current": xjob == job})
        # A band pill keeps the shown file, except the daemon's: there it opens that band's file.
        bands = [{"band": b, "current": b == shown, "target": e.writer,
                  "job": e.job(b) if e.banded else job} for b in self.RADIO_BANDS]
        return {"band": shown, "bands": bands, "stacks": stacks}

    def _rflog_switch_value(self, e) -> str:
        return (_rflog.ON if _rflog.is_on(self._resolved_param_value(
            e.owner, e.kind, e.writer, _rflog.RF_LOG_PARAM, "")) else _rflog.OFF)

    def rflog_switch(self, surface: str) -> dict | None:
        """The shown stack's switch as the log page's bottom form renders it: the owner, its
        configured value, and whether a restart is still pending for it."""
        e = _rflog.entry(surface)
        if e is None or self.stack(e.owner) is None:
            return None
        marker = self.restart_required(e.owner) or {}
        return {"owner": e.owner, "value": self._rflog_switch_value(e),
                "restart_required": _rflog.RF_LOG_PARAM in (marker.get("params") or [])}

    def rflog_logging_state(self) -> str:
        """Every installed stack's switch at once: "on" or "off" when they agree, else "mixed"."""
        values = {self._rflog_switch_value(e) for e in self._rflog_entries()}
        return values.pop() if len(values) == 1 else "mixed"

    @invalidates_snapshot
    def set_rflog_all(self, value) -> ActionResult:
        """Save the switch on every config owner the manifest installs, one owner at a time
        through the same one-key bundle path as `set_rflog` (so every owner's restart marker
        comes for free). One result, one detail line per owner."""
        v = str(value or "").strip().lower()
        if v not in (_rflog.ON, _rflog.OFF):
            return ActionResult(False, "RF log: the switch is 'on' or 'off'.",
                                data={"reason": "invalid-choice"})
        owners = dict.fromkeys((e.owner, e.key) for e in self._rflog_entries())
        results = [(owner, self.save_config_bundle(owner, values={key: v})) for owner, key in owners]
        ok = all(r.ok for _o, r in results)
        return ActionResult(ok, f"RF logging {v} on every stack." if ok
                            else "RF logging: not every stack was saved.",
                            [f"{owner}: {'saved' if r.ok else r.summary}" for owner, r in results])

    @invalidates_snapshot
    def rflog_clear_all(self) -> ActionResult:
        """Clear every registered RF log the manifest installs, one job at a time through
        `rflog_clear` (the same lock and the same refusals). Run logs are never in the loop."""
        results = [(job, self.rflog_clear(e.writer, job))
                   for e in self._rflog_entries() for _band, job in e.jobs]
        ok = all(r.ok for _j, r in results)
        return ActionResult(ok, "Every RF log cleared." if ok else "Not every RF log was cleared.",
                            [f"{job}: {'cleared' if r.ok else r.summary}" for job, r in results])

    @invalidates_snapshot
    def set_rflog(self, stack_id: str, value) -> ActionResult:
        """Save the switch on the CONFIG OWNER through the one-key bundle path (values merge; no
        band — the param is band-less). Graywolf's page deliberately writes the kiss store."""
        e = _rflog.entry(stack_id)
        if e is None or self.stack(e.owner) is None:
            return ActionResult(False, f"'{stack_id}' has no RF log.")
        v = str(value or "").strip().lower()
        if v not in (_rflog.ON, _rflog.OFF):
            return ActionResult(False, "RF log: the switch is 'on' or 'off'.")
        return self.save_config_bundle(e.owner, values={e.key: v})

    @invalidates_snapshot
    def rflog_clear(self, target: str, job) -> ActionResult:
        """Clear ONE RF log: truncate the live file in place (the writer keeps its descriptor —
        same inode, it simply continues) and remove its previous segment. Only a registered job
        of `target` — re-validated exactly like `log_tail`; nothing else is ever touched."""
        rf = self.rflog_job(target, job)
        if rf is None:
            return ActionResult(False, "Not an RF log of this component; nothing cleared.")
        try:
            name = validators.path_component(str(job), field="job log")
            if not name.endswith(".log"):
                raise validators.ValidationError("not a .log")
            p = self._rflog_path(name)
        except (validators.ValidationError, PathContainmentError) as exc:
            return ActionResult(False, f"RF log not cleared: {exc}")
        st = runtime_fs.stat_leaf_nofollow(self._paths, p)
        try:
            with self._rflog_lock(name):         # never interleaved with a native roll
                if st is not None:
                    # A symlink/non-regular leaf is refused by the anchored open, never truncated.
                    runtime_fs.open_log_truncate(self._paths, p).close()
                runtime_fs.unlink(self._paths, p.with_name(_rflog.previous(name)))
        except (OSError, PathContainmentError) as exc:
            return ActionResult(False, f"RF log not cleared: {exc}")
        return ActionResult(True, f"RF log cleared: {name}")

    # ---- records + decode ---------------------------------------------------------------
    # The page and the CLI work on RECORDS (rflog.parse_line). Decoding runs the stack's own
    # decoder under the stack's own interpreter, where its libraries and its keys already live;
    # the controller only relays lines and enforces the protocol. Results are cached per job by
    # record key (the raw line's hash) — no rotation state — and one decode runs per job at a time.

    RFLOG_DECODE_TIMEOUT_S = 10.0
    RFLOG_DECODE_MAX_OUT = 1 << 20          # bytes of decoder stdout ACCEPTED per batch (checked after the run)
    RFLOG_KIND_MAX, RFLOG_PEER_MAX, RFLOG_DECODED_MAX = 40, 120, 4000   # per-field caps on what a decoder may say
    RFLOG_LRU_MAX = 2000                    # cached decoded records per job
    RFLOG_NOKEY_TTL_S = 30.0                # a `no-key` answer is kept this long before the frame is retried

    def _rflog_meshcore_mode(self) -> str:
        """The mode the MeshCore RF log is being WRITTEN in: the running node's mode while it
        runs (a saved-but-not-restarted change must not switch the decoder's identity and
        stores under a live writer), the saved mode otherwise."""
        if self.stack_running(_meshcore_mode.STACK_ID):
            return _meshcore_mode.normalize(self.meshcore_running_mode())
        return _meshcore_mode.normalize(self.meshcore_mode())

    def rflog_records(self, target: str, job, lines: int = 300) -> tuple:
        """(path, records) — the RF viewer's data: the two-segment tail as parsed records."""
        path, raw = self.log_tail(target, lines, job=job)
        return path, _rflog.parse_lines(raw)

    def _rflog_decoder_argv(self, e) -> tuple:
        """(argv, error): the decoder script under the stack's interpreter, or a typed reason."""
        try:
            return self._rflog_decoder_argv_inner(e)
        except PathContainmentError as exc:
            return (), f"{e.surface}: a store path is refused ({exc})"

    def _rflog_decoder_argv_inner(self, e) -> tuple:
        from pathlib import Path as _P
        script = _P(__file__).resolve().parent.parent / "data" / "rfdecode" / f"decode_{e.decoder}.py"
        rt = str(self._paths.runtime_root)
        if e.decoder == "meshtastic":
            from . import meshtastic_tool
            # The venv's `bin/python` is a symlink to the system interpreter by design, so this
            # is lexical containment (an executable we run), never `under()` (paths we write).
            py = self._paths.runtime_root.joinpath(*meshtastic_tool.MANAGED_CLI_REL[:-1], "python")
            extra = ["--prefs", str(self._paths.under("state", "meshtasticd", "prefs"))]
        elif e.decoder == "meshcore":
            comp = self.stack(e.owner).component(e.writer) if self.stack(e.owner) else None
            if comp is None or comp.source is None:
                return (), "meshcore is not installed — nothing to decode with"
            py = self._paths.resolve_source(comp.source.path) / ".venv" / "bin" / "python"
            mode = self._rflog_meshcore_mode()
            extra = ["--mode", mode, "--secrets", str(self._paths.under("config", "secrets")),
                     "--store", str(self._paths.under("state", "meshcore", "companion.db")),
                     "--repeater-store", str(self._paths.under("state", "openhop"))]
        elif e.decoder == "reticulum":
            # LXMF's venv (RNS + LXMF: the message summary) when the delivery service is built,
            # else the RNS venv (RNS only: announces, IFAC, opaque plaintext) — both this stack's.
            stack = self.stack(e.owner)
            pys = []
            for cid in ("lxmd", e.writer):
                comp = stack.component(cid) if stack else None
                if comp is not None and comp.source is not None:
                    pys.append(self._paths.resolve_source(comp.source.path) / ".venv" / "bin" / "python")
            if not pys:
                return (), "reticulum is not installed — nothing to decode with"
            py = next((p for p in pys if p.is_file()), pys[-1])
            # `--config` is RNS's configdir (state/reticulum); the decoder reads its `config` file.
            extra = ["--config", str(self._paths.under("state", "reticulum")),
                     "--meshchat", str(self._paths.under("state", "meshchat"))]
            # RNode framing is undone with the LoRa driver's own module. The driver is built
            # into the RNS venv, not LXMF's, so the decoder gets its source checkout (the
            # pinned commit) to import from — when it is installed.
            drv = next((c for c in (stack.components if stack else ())
                        if c.source is not None and c.source.path.endswith("loraham-rns-interface")), None)
            if drv is not None and self._paths.resolve_source(drv.source.path).is_dir():
                extra += ["--driver", str(self._paths.resolve_source(drv.source.path))]
        else:
            return (), f"no decoder for {e.surface}"
        if not py.is_file():
            return (), f"{e.surface} is not built — nothing to decode with ({py})"
        return (str(py), str(script), "--runtime", rt, *extra), ""

    def _rflog_lru(self, job: str):
        from collections import OrderedDict
        store = self.__dict__.setdefault("_rflog_lru_by_job", {})
        return store.setdefault(job, OrderedDict())

    def _rflog_flight(self, job: str):
        import threading
        store = self.__dict__.setdefault("_rflog_flight_by_job", {})
        return store.setdefault(job, threading.Lock())

    def rflog_decode(self, target: str, job, records: list) -> dict:
        """Decode `records` (the viewer's batch) with the job's decoder. Returns
        {"records": [...with status/kind/peer/decoded...], "error": ""} — `error` is set, and no
        record decoded, on a DECODER-LEVEL failure (venv absent, store unreadable, protocol
        violation, timeout). A healthy store that lacks this frame's key is a per-record
        `no-key`, never an error. Cached per job by record key; a `no-key` answer expires after
        RFLOG_NOKEY_TTL_S so a key that appears later is picked up without a restart."""
        rf = _rflog.by_job(str(job or ""))
        if rf is None or rf[0].writer != target or not rf[0].decoder:
            return {"records": records, "error": "not a decodable RF log"}
        e = rf[0]
        import time as _time
        # Everything from "what is missing" to "cache updated" happens under the per-job lock:
        # two polls arriving together must not both decide the same frames are misses and
        # then run the decoder twice — the second finds the first's answers in the cache.
        with self._rflog_flight(str(job)):
            lru = self._rflog_lru(str(job))
            # The meshcore job's cache is only valid for the mode the log is written in.
            if e.decoder == "meshcore":
                mode = self._rflog_meshcore_mode()
                if lru.get("__mode__") not in (None, mode):
                    lru.clear()
                lru["__mode__"] = mode
            now = _time.monotonic()
            out = []
            misses = []
            for i, r in enumerate(records):
                hit = lru.get(r["key"])
                if hit is not None and (hit.get("status") != "no-key"
                                        or now - hit.get("_at", 0) < self.RFLOG_NOKEY_TTL_S):
                    out.append({**r, **{k: v for k, v in hit.items() if k != "_at"}})
                else:
                    # A split packet (RNode framing) is two records; its first half answers
                    # "fragment" until the second is decoded IN THE SAME BATCH. So a miss takes
                    # a cached fragment right before it back into the batch — the decoder then
                    # sees the pair in order and the second half carries the message.
                    if i and out[i - 1] is not None and out[i - 1].get("kind") == "fragment":
                        out[i - 1] = None
                        misses.append(records[i - 1])
                    out.append(None)
                    misses.append(r)
            error = ""
            decoded = {}
            if misses:
                argv, error = self._rflog_decoder_argv(e)
                if not error:
                    decoded, error = self._rflog_run(argv, misses)
            for i, r in enumerate(records):
                if out[i] is None:
                    d = decoded.get(r["key"])
                    if d is None:
                        out[i] = {**r, "status": "", "kind": "", "peer": "", "decoded": ""}
                    else:
                        out[i] = {**r, **d}
                        # `no-key` is kept briefly (a key may appear without a restart) so an
                        # unchanged failure is not re-decoded every poll; anything else for good.
                        lru[r["key"]] = {**d, "_at": now} if d.get("status") == "no-key" else d
                        lru.move_to_end(r["key"])
            while len(lru) - ("__mode__" in lru) > self.RFLOG_LRU_MAX:     # the marker is not a record
                k = next(iter(lru))
                if k == "__mode__":
                    lru.move_to_end(k)
                    k = next(iter(lru))
                lru.pop(k, None)
        return {"records": out, "error": error}

    def _rflog_run(self, argv, misses) -> tuple:
        """One decoder subprocess over `misses`: N lines in, N JSON lines out, keys matching, in
        order — anything else is a protocol violation and the whole batch is rejected. stdout is
        collected whole and then refused above RFLOG_DECODE_MAX_OUT — a cap on what is ACCEPTED
        (the wall clock bounds the child), not a limit on the child's memory. stderr is captured
        and capped; only our own typed first line (exit 3) is reported, never a traceback, never
        plaintext."""
        import json as _json
        import subprocess
        payload = "".join(_json.dumps({"key": r["key"], "raw": r["raw"]}) + "\n" for r in misses)
        try:
            proc = subprocess.run(list(argv), input=payload.encode("utf-8", "surrogateescape"),
                                  capture_output=True, timeout=self.RFLOG_DECODE_TIMEOUT_S,
                                  check=False, cwd=str(self._paths.runtime_root))
        except subprocess.TimeoutExpired:
            return {}, "decoder timed out"
        except OSError as exc:
            return {}, f"decoder could not start: {exc.strerror or exc}"
        err = proc.stderr[:4096].decode("utf-8", "replace")
        if proc.returncode == 3:
            first = err.strip().splitlines()[0] if err.strip() else "decoder refused"
            return {}, first[:200]
        if proc.returncode != 0:
            return {}, f"decoder failed (exit {proc.returncode})"
        if len(proc.stdout) > self.RFLOG_DECODE_MAX_OUT:
            return {}, "decoder output too large"
        lines = proc.stdout.decode("utf-8", "replace").splitlines()
        if len(lines) != len(misses):
            return {}, f"decoder protocol violation ({len(lines)} results for {len(misses)} records)"
        result = {}
        for r, ln in zip(misses, lines, strict=True):
            try:
                obj = _json.loads(ln)
            except ValueError:
                return {}, "decoder protocol violation (not JSON)"
            if not isinstance(obj, dict) or obj.get("key") != r["key"]:
                return {}, "decoder protocol violation (key mismatch)"
            status = str(obj.get("status", ""))
            if status not in ("ok", "no-key", "undecryptable", "malformed"):
                return {}, "decoder protocol violation (status)"
            result[r["key"]] = {"status": status,
                                "kind": str(obj.get("kind", ""))[:self.RFLOG_KIND_MAX],
                                "peer": str(obj.get("peer", ""))[:self.RFLOG_PEER_MAX],
                                "decoded": str(obj.get("decoded", ""))[:self.RFLOG_DECODED_MAX]}
        return result, ""

    def rflog_tail(self, surface: str, band: str = "", lines: int = 300) -> tuple:
        """CLI: (path, lines) for a surface's RF log; the daemon needs its band, nobody else
        takes one. ("", []) with a reason in the second slot is never returned — errors raise."""
        e, job = _rflog.resolve_job(surface, band)
        return self.log_tail(e.writer, lines, job=job)
