# Live tests

The newest completed live run on the reference box, and nothing else. Measured values only. Which
releases run what is defined in [maintenance.md](maintenance.md#branches-and-releases); the
full-matrix procedure is [test-matrix.md](test-matrix.md). CI and the [testlab](testlab.md) prove
the code and the console.

Earlier runs — the 0.3.0 and 0.2.10 release matrices and the 2026-09-05 on-air silicon test other
pages cite for their measured numbers — live in this file's git history:
`git log --follow -p -- docs/live-test.md`.

## 0.3.9 — locally added files survive an update, 2026-09-09

Run on `lhpc-e293` (aarch64, Trixie Lite) against the release candidate, from a scratch
`LHPC_RUNTIME_ROOT` so the box's own installation, config and running stacks were untouched: a
scratch root holds no ownership records, so it can never signal their processes. `daemon` was
stopped for the binary rows (which took `meshcom` with it); `meshtastic` kept running on 868. No
radio is needed for any row — a source update requires its consumers stopped, so no band is
claimed. Earlier the same day the same candidate was exercised against real GitHub clones of
`loraham-kiss-tnc` and `openhop-core` on a workstation; only the rows that need aarch64 or a
running consumer are listed here.

| row | what was done | result |
|---|---|---|
| additions survive | plain, `.gitignore`d, nested, mode `755`, across two consecutive updates | pass — every file byte-identical afterwards, mode preserved; the ignored one is invisible to `git status` and still survived |
| runtime artifacts | two bound unix sockets (one in `.run/`, one at the checkout root) and a FIFO, plus an ordinary log the app wrote | pass — none blocked the update, none was carried, the log WAS carried, and `.prev` was cleaned with the socket and FIFO inside it |
| upstream changes refuse | modified · deleted · staged · `git add`ed · modified-plus-added | pass — refused in every case, tree byte-identical afterwards, `HEAD` unmoved, the operator's change untouched; the mixed case named only the modified file |
| collision | local `settings.json`, then upstream ships that path | pass — refused naming the file, old source still active, tree byte-identical, no residue |
| parent collision | local `conf/mine.ini`, upstream ships `conf` as a file | pass — refused naming `conf`, old tree intact |
| carry failure | an added file made unreadable (real `EACCES`, no fault injection) | pass — refused naming it, prior restored and authoritative, transaction resolved |
| crash during carry | `SIGKILL` of a real update during a 3 GB carry | pass — candidate held a partial copy; recovery refused to promote and, unable to prove the candidate, retained `.prev` + candidate + journal. No data lost: the archive held the complete prior. Manual resolution restored it and the retry carried all 3 GB |
| crash before carry | `SIGKILL` at journal state `prior-archived`, 30 000 added files widening the window | pass — recovery rolled the transaction back automatically; all 30 000 files and the addition intact; the retry then carried them |
| late `.prev` addition | a file created in `.prev` at journal state `activated`, after the carry | pass — `prior-dirty`, `.prev` retained whole, the file never deleted, new source active, registry truthful; automatic recovery refused twice |
| substituted `.prev` | `.prev` replaced between the crash and recovery | pass — `recovery-required`, nothing promoted or deleted, substitute untouched |
| binary install strictness | `notes.txt` in `src/meshcom-qemu-raspi`, then `install meshcom --source binary` | pass — refused: *"has local changes — the artifact runs scripts from that checkout"*. The same file in `src/loraham-daemon` does **not** block a daemon binary install: the gate covers `clone_required` components only |
| the asymmetry, one tree | source update, then binary install, then remove the file | pass — update carried it, binary install refused it by name, removing it let the install proceed |
| binary → source switch | switch with an addition present | pass — binary retired, addition survived, no residue |
| auto-install | binary channel; source channel with an addition; source channel with a modified upstream file | pass — 1/1 successful, 1/1 successful with the addition carried, **1 blocked** for the modification; a channel switch correctly refused as *"an install, not an update"* |
| running consumer | update attempted with `loraham-daemon` running | pass — *"component(s) using the affected source(s) are running … an update never stops or restarts a stack itself"* — refused on the consumer, not on the addition |
| LHPC-patched checkout | openHop patched by its build step, plus an added file | pass — the declared patch stays exempt, the update carries the addition, an edit beyond the patch still refuses; `probe_source` reports `patched: lhpc` with no `-dirty` |

After every row: no `.prev`, no candidate, no journal, `lhpc doctor` clean, the ownership record's
commit equal to `HEAD`, and no traceback in the console log.

Not covered on hardware: forcing a real inode to be recycled, which no filesystem does on demand —
that case is proved by a unit test that forges the recorded identity instead.

