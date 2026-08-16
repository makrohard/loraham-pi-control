#!/bin/sh
# Test-lab fake daemon self-test: --version answers and the script parses.
set -eu
cd "$(dirname "$0")"
python3 -m py_compile loraham_daemon
./loraham_daemon --version
