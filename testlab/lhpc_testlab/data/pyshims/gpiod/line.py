"""Test-lab fake gpiod.line — the enum surface loraham-rns-interface imports."""
import enum


class Direction(enum.Enum):
    INPUT = 1
    OUTPUT = 2


class Value(enum.Enum):
    INACTIVE = 0
    ACTIVE = 1


class Bias(enum.Enum):
    AS_IS = 0
    DISABLED = 1
    PULL_UP = 2
    PULL_DOWN = 3


class Edge(enum.Enum):
    NONE = 0
    RISING = 1
    FALLING = 2
    BOTH = 3
