#!/usr/bin/env bash
# Sourced (as the lab user, from the checkout copy) by the testlab lane and by its offline proof,
# so both run the same commands: the lane's own Python environment from the wheels acquired
# before the lane (/xfer/wheels), never an index. The flags are on these installs only, never in
# a settings file pip reads: the product's own pip steps (the Meshtastic CLI venv of the reset, the
# meshcore builds of the acceptance lane) fetch from PyPI as they do for a user. Leaves /tmp/venv
# active.
python3 -m venv /tmp/venv
. /tmp/venv/bin/activate
pip install -q --no-index --find-links /xfer/wheels -e .            # the product
pip install -q --no-index --find-links /xfer/wheels -e ./testlab    # the SEPARATE lab package
pip install -q --no-index --find-links /xfer/wheels pytest playwright
