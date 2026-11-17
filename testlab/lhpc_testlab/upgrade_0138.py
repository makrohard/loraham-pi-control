"""The "Upgrading from 0.13.x" self-update step, ONE source: the CHANGELOG gives these lines (a unit test compares
them there), and the slow-build lane runs them as written, in the checkout, at three cut points of 0.13.8's helper."""

# In the checkout: what the cut wrote and any file of the operator's the reset will replace, ignored ones included
# (`!!`: a .git/info/exclude entry, an untracked directory's ignored contents), the new commit over the half-written
# files, then the update run again (the venv sync and the unit refresh 0.13.8 never reached).
STEPS = ("git status --short --ignored", "git reset --hard origin/main", "lhpc self-update --apply --yes")
# If the reset fails with this text, the lock line (once no git runs in that checkout), then the reset again.
LOCK = ("index.lock': File exists", "rm .git/index.lock")
