"""Shared domain enums used by both intelligence/ and strategies/."""

from enum import Enum


class Regime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    SIDEWAYS = "SIDEWAYS"
    HIGH_VOL = "HIGH_VOL"
    UNKNOWN = "UNKNOWN"
