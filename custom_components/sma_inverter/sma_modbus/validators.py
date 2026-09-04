"""Write-value validation for the SMA inverter.

A ``WriteValidator`` runs inside ``Component.write`` *before* the value is
scaled and encoded, so these functions see (and return) engineering units:
whole percent for the power limit, seconds for the fallback timeout. Returning
a value lets a validator normalise as well as reject; raising rejects the write
before any Modbus traffic happens.
"""

from __future__ import annotations

from .enums import FallbackMode, OperatingMode
from .exceptions import SmaValueError

# 40016 is S16 FIX0: whole percent of WMax, 0..100. 0 is full curtailment and a
# legitimate value, so it is not treated as "no limit".
POWER_LIMIT_MIN_PCT = 0
POWER_LIMIT_MAX_PCT = 100

# 41525 "External active power setpoint, timeout" is U32 FIX2 and the parameter
# list gives its range as 0.01 .. 1800.00 s. Below 10 s is rejected here: the
# heartbeat is derived from half the timeout, and anything shorter cannot be
# fed reliably over a link that SS7.2 wants 10 s of spacing on anyway.
FALLBACK_TIMEOUT_MIN_S = 10
FALLBACK_TIMEOUT_MAX_S = 1800


def validate_power_limit_pct(value: float) -> int:
    """Validate the active power limit as whole percent of WMax."""
    pct = int(round(float(value)))
    if not POWER_LIMIT_MIN_PCT <= pct <= POWER_LIMIT_MAX_PCT:
        raise SmaValueError(
            f"power limit must be {POWER_LIMIT_MIN_PCT}..{POWER_LIMIT_MAX_PCT} %"
            f" of WMax, got {value}"
        )
    return pct


def validate_operating_mode(value: float) -> int:
    """Validate a target operating mode for register 40210.

    Only documented codes are allowed through: 40210 is a flash configuration
    register, and writing an undocumented value into it is the one mistake that
    cannot be walked back.
    """
    mode = OperatingMode.from_raw(int(value))
    if mode is OperatingMode.UNREADABLE:
        raise SmaValueError(
            f"operating mode must be one of "
            f"{sorted(m.value for m in OperatingMode if m.value > 0)}, got {value}"
        )
    return int(mode.value)


def validate_fallback_mode(value: float) -> int:
    """Validate a target fallback mode for register 41193."""
    fallback = FallbackMode.from_raw(int(value))
    if fallback is FallbackMode.UNREADABLE:
        raise SmaValueError(
            f"fallback mode must be "
            f"{FallbackMode.MAINTAIN_VALUES.value} or "
            f"{FallbackMode.USE_FALLBACK.value}, got {value}"
        )
    return int(fallback.value)


def validate_fallback_timeout(value: float) -> float:
    """Validate the fallback timeout in seconds."""
    seconds = float(value)
    if not FALLBACK_TIMEOUT_MIN_S <= seconds <= FALLBACK_TIMEOUT_MAX_S:
        raise SmaValueError(
            f"fallback timeout must be {FALLBACK_TIMEOUT_MIN_S}.."
            f"{FALLBACK_TIMEOUT_MAX_S} s, got {seconds}"
        )
    return seconds
