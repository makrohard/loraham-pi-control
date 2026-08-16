#!/bin/sh
# Test-lab fake daemon "build": the daemon IS the committed script — just make it
# executable (mirrors the real build.sh contract: bin exists and runs afterwards).
set -eu
cd "$(dirname "$0")"
chmod +x loraham_daemon
./loraham_daemon --version
