#!/usr/bin/env bash
#
# rns-wheel.sh build <wheel dir>     (cwd: the Reticulum node's checkout; .venv is its venv)
# rns-wheel.sh install <wheel dir>   (cwd: a client's checkout; .venv is its venv)
#
# The ONE Reticulum wheel the node builds from its checkout and the clients (nomadnet, lxmd,
# Sideband) install, so every venv runs the node's RNS. The console builds the clients in one
# parallel round: they only read the wheel, nothing is cloned or built per client.
#
# build:   remove <wheel dir> STRICTLY (a symlink is unlinked, a directory removed; what cannot be
#          removed fails the step), build the checkout's wheel into it, require exactly one rns wheel
#          there, and force-install that file into the node's own venv.
# install: require exactly one rns wheel in <wheel dir> (none, or two, fail the step even when an
#          rns is already importable) and force-install THAT FILE.
#
# Both installs name the file, never the package, with no index and no pip configuration: every
# PIP_* variable is unset and PIP_CONFIG_FILE=/dev/null stops pip reading any config file (global,
# per-venv and user), so no configured index or find-links offers a second rns. --force-reinstall
# replaces an rns the venv already sees (--system-site-packages, or an earlier install of the same
# version with other code). --no-deps: the client's own install pulls RNS's dependencies.
#
# Both installs then write .venv/lhpc-rns-constraint.txt = `rns @ file://<that wheel>`. Every later pip install of the
# node and of each client runs with `-c` on it, so pip may take RNS from that FILE only: a dependency that wants another
# RNS (lxmf 1.2.1 requires rns>=1.5.7) makes pip choose an older release of that dependency, or fail the build, and a
# reinstall a pip configuration forces takes the same file again, never another wheel of the same version.
set -euo pipefail

usage() { echo "usage: rns-wheel.sh build|install <wheel dir>" >&2; exit 2; }
[ $# -eq 2 ] || usage
MODE=$1 DIR=$2
PIP=.venv/bin/pip
PY=.venv/bin/python

one_wheel() {
    local found=()
    shopt -s nullglob
    found=("$DIR"/rns-*.whl)
    shopt -u nullglob
    if [ ${#found[@]} -ne 1 ]; then
        echo "rns-wheel: expected exactly one rns wheel in $DIR, found ${#found[@]}" >&2
        exit 1
    fi
    WHEEL=${found[0]}
}

install_wheel() {
    one_wheel
    echo "rns-wheel: installing $WHEEL"
    (
        while IFS='=' read -r name _; do unset "$name"; done < <(env | grep '^PIP_' || true)
        export PIP_CONFIG_FILE=/dev/null
        "$PIP" install --disable-pip-version-check --no-index --no-deps --force-reinstall "$WHEEL"
    )
    "$PY" -I - "$WHEEL" <<'EOF'    # -I: not the checkout's own rns.egg-info in the cwd
import importlib.metadata as m, pathlib, sys
d = m.distribution("rns")
print("rns-wheel: rns", d.version, d.locate_file("RNS/__init__.py"))
line = f"rns @ {pathlib.Path(sys.argv[1]).resolve().as_uri()}"
with open(".venv/lhpc-rns-constraint.txt", "w") as f:
    f.write(line + "\n")
print(f"rns-wheel: wrote .venv/lhpc-rns-constraint.txt: {line}")
EOF
}

case $MODE in
    build)
        if [ -L "$DIR" ]; then
            rm -- "$DIR"
        elif [ -e "$DIR" ]; then
            rm -r -- "$DIR"
        fi
        if [ -e "$DIR" ] || [ -L "$DIR" ]; then
            echo "rns-wheel: could not remove $DIR" >&2
            exit 1
        fi
        "$PIP" wheel --disable-pip-version-check --no-deps -w "$DIR" .
        install_wheel
        ;;
    install)
        install_wheel
        ;;
    *)
        usage
        ;;
esac
