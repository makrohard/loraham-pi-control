# Todo after 0.11.0

**Temporary working document.** It lives on the branch `notes/todo-after-0.11.0` only. It is not part of any
release, is not merged into `main` or `dev`, and is deleted when its rows have moved into plans, the changelog or
the docs. State: 2026-09-28, written during the 0.11.0 release run; updated the same day (one line removed
that a re-check refuted, three tasks added).

How to read it: section 1 is the agreed order of work. Sections 2 and 3 are decided work. Section 4 is parked and
gets decided when the next release is planned. Section 5 comes last, before 1.0.0.

Every work item runs to its audit point (plan or change written, read by a second reader, frozen). Nothing lands on
`dev`, is released, or is posted to another project beyond that point without the maintainer.

## 1 · Order of work after the release

| # | Item | Needs a decision first |
|---|---|---|
| 0 | Fix for the three moved sources (Reticulum, NomadNet, LXMF), **only if needed**: the bot's release of them fails, or NomadNet does not start with its new text-UI library. If needed, it goes before everything below | no |
| 1 | Release bot's schedule back on, once the 0.11.0 images are built | no |
| 2 | Reference box: console identity restored from the kept copy, served certificate read back | no |
| 3 | MeshCom: the firmware source goes back from the temporary fork to upstream, before the next manual release | no (decided 2026-09-28) |
| 3 | Override for a binary blocked by the pin check (`--accept-pin-mismatch`, with the hint "update LHPC first" in the CLI and the console) | four open points of its plan |
| 4 | Upstream fixes prepared for Graywolf (three items), through both review gates | variant for one of them |
| 4 | Log rotation (section 2.1) | limits |
| 4 | Certificates (section 2.2) | several, in its plan |
| 4 | Supervision of running stacks (section 2.3) | its plan |
| 4 | Disk-space warning (section 2.4) | its plan |
| 5 | The smaller items of section 3 | no |
| 6 | One look at the route guard for the MeshCore repeater dashboard (section 2.5), low priority | risk tends to be accepted |

## 2 · Decided work items

### 2.1 Log rotation
| Part | Content |
|---|---|
| Start logs | capped while the stack runs: above 8 MiB the last 1 MiB is kept as `<name>.prev.log`, then the log is truncated. One commit exists, proven on a Pi 5; its review is outstanding. Limits to be confirmed |
| Scope extended 2026-09-28 | the logs that grow without a bound today: the web server's access and error log, the console's own logs (web, self-update, boot restore), and Meshtastic's trace log, which is only rolled at a start, a page read or a Clear |

### 2.2 Certificates
| Part | Content | Priority |
|---|---|---|
| Validity cap | the HTTPS server certificate's whole validity is 825 days or fewer. Today it is 826: the 825 configured days plus one day of backdating. With a test on the validity in days | normal |
| Homework before the cap | the requirements of Apple devices are read at the source and quoted with link and date; each one is checked against the real certificates with openssl (server certificate, both CAs, a client bundle); only then the change, then a test on a real iPhone. Until that is done, the 825-day figure counts as unverified | before the cap |
| Automatic renewal | the server certificate renews by itself before it expires, under the same CA, so clients keep their trust. The plan settles: how long before expiry, the trigger, never on an unverified clock, the web server's reload | normal |
| Expiry made visible | today the server certificate's expiry date is shown nowhere and nothing warns. Client certificates cannot renew silently (a new bundle has to reach the device), so they get a warning | normal |
| Low-priority part | the CAs run 3650 days without a warning; the healing of an expired revocation list runs only while the console runs; `lhpc webserver verify` does not flag an expired list | low |

### 2.3 Supervision of running stacks
`lhpc` is not a supervisor: a stack that crashes stays down until a reboot or a manual start, and a failed boot
restore is not retried; a banner and `lhpc autostart` are the only signs (docs/operations.md, "Not a supervisor").
Analysis and plan first: what may restart by itself, how often, and what never does (transmitting without a fresh
start gate, licence and call sign checks, band arbitration).

### 2.4 Disk-space warning
The console shows a disk bar that changes colour at 80 % and 90 %. No warning was found in `lhpc doctor` or the
status. Analysis first (what exists today), then a plan.

### 2.5 Route guard for the MeshCore repeater dashboard, low priority
The release bot moves the repeater's source to upstream's tip. The dashboard proxy uses a deny list, and its test
covers the routes known today. A guard that fails on new, unreviewed routes was proposed and never planned. One
look, low priority; the maintainer tends to accept the risk.

## 3 · Smaller items

| Item | Kind |
|---|---|
| `adopt_source` can fail when the source checkout's `.git` is being repacked during the copy; the single retry of 0.3.16 is not always enough. Seen once in CI. Cause proven, fix plan outstanding | defect |
| MeshCom emulator latency patch: the measurement on the Pi Zero was interrupted half way; finish it, then decide | measurement |
| Meshtastic: a node that is fresh after a purge logs its node-info at start but does not transmit it (2 of 2 boxes in the 0.11.0 run). Find the cause. It is a known issue in the 0.11.0 Release text | analysis |
| `lhpc self-update --run-service` does not check that it runs inside systemd; analysis done, a refusal is recommended | defect |
| Keeping the MeshCom fork current: the overlay no longer applies to upstream's development branch; the rebased overlay is reviewed and parked; one hunk breaks on every upstream version bump; the weekly proof pins an old overlay | upkeep |
| The watch on the open pull requests in other projects resumes | upkeep |
| Docs: MAINTAINING.md's maintainer-patch lane still puts the binaries after the push to `main`; the ruleset on `main` needs them before | docs |
| Docs: the matrix record of the 0.11.0 run and the release pre-flight checklist, as the first docs commit after the tag | docs |
| Rows of the old list that could not be mapped with certainty and are checked by their owners: the known-working label "(dev)" on kiss after a pinned reinstall; the docs sentence on a hand-edited Meshtastic config; the rest of the notes on the revocation fix; the doubled "(staying armed)" in the images' first-boot text; the exact case of the boot-restore reason; the headless build guard for MeshCom upstream | to check |
| Test rig: the 433 power guard script on the second test box discards the result of its reset | test rig |
| Changelog: shorter entries; it is not meant to explain | docs |
| GitHub rulesets: the required checks shall count for every push; later | repository |

### Minor issues seen in the 0.11.0 run
| Item |
|---|
| MeshCore's command-line client prints "no_event_received" although its frames go out |
| A purge resets Reticulum's `rnode_framing` to "no"; an RNode peer needs "yes" again (documented) |
| Voice over the air was not tested: neither test box has an audio input |

## 4 · Parked: decided when the next release is planned

Nobody works on these rows until then. When the plan for the next release is started, they are put to the maintainer
as one table: keep, drop, or when.

| Item |
|---|
| High power: why +20 dBm gives only +1 dB over 17; measure the radiated power (needs a calibrated instrument) |
| Old MeshCore repeaters keep their 32-character admin password; over-the-air admin login fails there |
| MeshCom bridge sets the power at start and again 18 s later: analysed, not a defect; one docs sentence proposed |
| Boot restore does not bring back the optional MeshCore web UI |
| MeshCore build memory on the Pi Zero |
| Meshtastic: `max_power 20` is possible without the high-power switch |
| MeshCore admin login over Bluetooth |
| Plugin manager's spawn hardened |
| Testlab lanes independent of each other |
| Power cap for boards with an SX1262 |
| Dashboard tiles count stacks, not components |
| The MeshCore plugin manager is not restarted after a crash, and the same boot refuses a replacement (by design today) |
| The system journal is volatile: a reboot erases the previous boot's journal (documented drop-in for bench boxes only) |
| Backup of identities, PKI and secrets is by hand |
| The reference box's Wi-Fi flaps between the home network and its own access point; the reason codes were never read |
| MeshCom bridge: the pong timeout (90 s) has less than twice the measured round trip (58 s) as margin |
| Daemon settings: read-back of what the chip took (shadow, and from the chip on demand); choice of the parameters the dashboard shows |
| The transmit guard delays on 433 and 868 still need a bench comparison |
| The overlay script of the emulator repo writes its errors to a shared file in /tmp |
| The MeshCore stop takes 97 s: one docs line |

## 5 · Before 1.0.0: the last items before the first major

Not to be started before 1.0.0 is planned.

| # | Item |
|---|---|
| 1 | A full review of the code |
| 2 | A full docs run: every doc against the code and against the behaviour on a box |
| 3 | A full test run, including the check that the house rules for tests (`tests/README.md`) were kept since the last check |

## 6 · What 0.11.0 took from the previous list

MeshCom nodes get their own node ID; the self-update clean-up path; the clock line in `lhpc doctor`; chat's default
receive frequency; purge as a full wipe; the voice client's audio devices; the hmac refusal before the preview; the
auto-install version select; purge removing `state/post/`; start and boot restore naming the daemon's real failure;
the test matrix against the binary default; the browser proof for the call sign row; docs for a reflashed Meshtastic
node; revocation enforced at once; the decrypted login kept out of the log.
