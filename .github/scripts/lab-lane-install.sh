#!/usr/bin/env bash
# Sourced (as the lab user, from the checkout copy) by the testlab lane and by its offline proof,
# so both run the same commands: the lane's Python environment from the wheels acquired before the
# lane (/xfer/wheels), never an index. The pip.conf is the lab user's own (~/.config/pip), so the
# product's own pip steps (the Meshtastic CLI venv of the reset) read it too: the product's
# command runner passes HOME on and drops PIP_*. Leaves /tmp/venv active.
mkdir -p ~/.config/pip
printf '[global]\nno-index = true\nfind-links = /xfer/wheels\n' > ~/.config/pip/pip.conf
python3 -m venv /tmp/venv
. /tmp/venv/bin/activate
pip install -q -e .            # the product
pip install -q -e ./testlab    # the SEPARATE lab package
pip install -q pytest playwright
