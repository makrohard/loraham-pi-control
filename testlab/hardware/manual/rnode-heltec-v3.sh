#!/bin/bash
# Reflash the Heltec LoRa32 V3 RNode peer. Manual only: no lane runs anything under manual/.
# The recorded flash: `rnodeconf --autoinstall <port>` from rns 1.5.4, menu answers 8 (Heltec LoRa32 v3), Enter (the
# experimental notice), 2 (868 MHz: the 850-950 MHz variant), y; RNode firmware 1.86. rnodeconf downloads the firmware and
# validates its signature; no image sha was recorded. It writes nothing before its y/N summary.
# Before: no process may hold the port (the PC's gpsd takes CP2102 ports, see docs/hardware-proof.md).
# usage: rnode-heltec-v3.sh /dev/serial/by-id/<the RNode's name> <path of rnodeconf> --yes
set -euo pipefail
[ $# = 3 ] && [ "$3" = --yes ] || { echo "usage: rnode-heltec-v3.sh <by-id port> <rnodeconf> --yes" >&2; exit 2; }
port=$1 conf=$2
[ -e "$port" ] || { echo "no such port: $port" >&2; exit 2; }
if command -v fuser > /dev/null && fuser "$port" 2> /dev/null; then echo "a process holds $port" >&2; exit 2; fi
printf '8\n\n2\ny\n' | "$conf" --autoinstall "$port"
