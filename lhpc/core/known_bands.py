"""The radio bands LHPC knows, by name (MHz). The ONE list: every band check reads it at call time.
To add a band (e.g. "915"), see docs/adding-a-band.md: the daemon, hardware, claims and stacks need it too."""
ALL = ("433", "868")


def is_known(value) -> bool:
    return isinstance(value, str) and value in ALL
