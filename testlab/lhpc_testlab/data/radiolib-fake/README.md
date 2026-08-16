# radiolib-fake (LHPC test lab)

Build-dependency stub: the real daemon's build needs the RadioLib checkout; the fake
daemon's build does not, but the manifest dependency edge stays real — this repo
satisfies it without any network clone.
