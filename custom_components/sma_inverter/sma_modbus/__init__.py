"""sma-modbus: a backend-neutral device library for SMA hybrid inverters.

Built on ``modbus-connection``: the library models the inverter and nothing
else, so it runs over pymodbus, tmodbus, or the in-memory mock, and carries no
Home Assistant dependency. Curtailment *policy* belongs to the consumer.

Developed against a Sunny Boy Smart Energy 5.0 (ennexOS generation). The
generic SMA Modbus document from 2014 disagrees with this device in places;
where they differ, this library follows the device's own parameter list.
"""

from __future__ import annotations

from .components import (
    SmaCapabilities,
    SmaDiagnostics,
    SmaGridLimit,
    SmaSettings,
    SmaTelemetry,
)
from .device import SmaModbusDevice
from .enums import Condition, Derating, FallbackMode, LimitSource, OperatingMode
from .exceptions import (
    SmaConnectionError,
    SmaFlashProtectionError,
    SmaModbusError,
    SmaValueError,
)
from .model import SmaData
from .validators import (
    FALLBACK_TIMEOUT_MAX_S,
    FALLBACK_TIMEOUT_MIN_S,
    POWER_LIMIT_MAX_PCT,
    POWER_LIMIT_MIN_PCT,
)

__version__ = "0.1.0"

__all__ = [
    "FALLBACK_TIMEOUT_MAX_S",
    "FALLBACK_TIMEOUT_MIN_S",
    "POWER_LIMIT_MAX_PCT",
    "POWER_LIMIT_MIN_PCT",
    "Condition",
    "Derating",
    "FallbackMode",
    "LimitSource",
    "OperatingMode",
    "SmaCapabilities",
    "SmaConnectionError",
    "SmaData",
    "SmaDiagnostics",
    "SmaFlashProtectionError",
    "SmaGridLimit",
    "SmaModbusDevice",
    "SmaModbusError",
    "SmaSettings",
    "SmaTelemetry",
    "SmaValueError",
    "__version__",
]
