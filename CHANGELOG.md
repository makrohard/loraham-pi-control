# Changelog

## 0.1.1 — hardening

Hardening (see `docs/hardening-0.1.md`):

- Descriptor-anchored source transactions, fail-closed session tokens, thin launcher runtime, owned journals; dead-code/docs cleanup; MIT license.


## 0.1.0 — initial version

Terminal CLI and local web console to install, configure and run the LoRaHAM Pi
LoRa stacks (daemon, chat, igate, voice, kiss, meshtastic, meshcom, meshcore).
Adopts and builds each stack's source, starts/stops in dependency order with
per-band radio-conflict gating, writes each app's config, and monitors and
live-tunes the daemon. Bounded read-only status probes; explicit gated mutations;
one-frame TX test on dummy loads. Loopback-only web console (CSRF, CSP).
Validated live on the Raspberry Pi.
