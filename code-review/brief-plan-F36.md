# CLOUD BRIEF · PLAN for F36: re-apply the firewall after a self-update that changes the firewall helper

Read-only on the code; output = ONE commit on this routine's branch adding `plans/PLAN-F36.md`; never touch main/dev/code-review/brief; no pull request; no attribution lines. Base origin/main e5187f70 (v0.11.10). Read docs/architecture.md, docs/firewall.md, docs/deployment.md, tests/README.md first.

## The finding (0.11.10 live, 2026-10-01)
On a box with the firewall installed and remote exposure configured, an LHPC update that changes the firewall helper makes the loaded firewall read `update-required` (helper-revision mismatch → live_ok False, service_firewall.py ~226-234); the next reboot's boot gate (`webserver --firewall-boot-gate`, ~758-806) fails closed to a loopback-only nginx until the operator runs the sudo re-apply + `lhpc webserver apply`. Fail-closed is right; the surprise is not. 0.11.10 shipped an upgrade note only.

## The plan must settle
1. The exact trigger (which files/hashes make the helper revision), and every update path (console self-update, CLI self-update, boot restore) and what each can do without root (the helper re-apply needs sudo: `firewall-apply.sh`).
2. Options, sized: (a) the self-update detects the helper change and TELLS the operator before the reboot (console banner + CLI line + `lhpc doctor` non-OK: 'firewall re-apply required — run … before you reboot') and the boot gate's loopback fallback says the same; (b) the boot gate keeps the last VERIFIED listener when ONLY the helper revision changed and the ruleset hash is unchanged (is that provably safe?); (c) a privileged re-apply scheduled by the self-update (needs a sudoers rule or a root helper unit — a new mechanism; size and risk). Recommend the simplest that removes the surprise; (a) is the floor.
3. Tests (red before) and the live proof on the Pi 5 (an update that changes the helper → the banner/doctor line → re-apply → reboot → console stays on the LAN).
4. Commits, docs (docs/firewall.md one place), CHANGELOG line, open questions with recommendations, self-check. ≤ 200 lines.
