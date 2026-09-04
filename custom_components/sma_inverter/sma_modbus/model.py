"""The parsed, engineering-unit snapshot of the inverter.

THE SENTINEL PROBLEM, which is the reason this module exists.

An SMA inverter does not raise an error for a register it cannot answer: per
SS6 (p.64) it returns a NaN sentinel instead - 0xFFFFFFFF for a U32,
0x80000000 for an S32, 0xFFFFFD for a TAGLIST. An unsupported register, a
write-only register and an overloaded Modbus server all look like a perfectly
valid number. Left alone, those values reach the consumer as 4.29 GW of PV
production or a 42949672.95 s timeout, and they poison min/max statistics and
long-term history permanently.

HOW THIS MODULE HANDLES IT: every field is checked against a PLAUSIBILITY BAND
and returns ``None`` when it falls outside, so the consumer can render "unknown"
instead of a fabricated value. The band, not an equality test against the
sentinel, is the check that survived contact with this device: a wrong scale
factor produces a value that is out of range without ever equalling a sentinel,
and it needs rejecting just as much.

The bands are deliberately GENEROUS rather than snug to the nameplate - the job
here is to reject nonsense, not to second-guess a real reading. A 5 kW inverter
that reports 9 kW is reporting something worth seeing; one that reports 4.29 GW
is reporting a failed read.

BAND EDGES ARE INCLUSIVE, and that is not pedantry: for a bounded quantity the
edges are frequently the readings worth acting on, and an exclusive band
silently discards exactly those.
"""

from __future__ import annotations

from dataclasses import dataclass

from .components import (
    SmaCapabilities,
    SmaDiagnostics,
    SmaGridLimit,
    SmaTelemetry,
)
from .enums import Condition, Derating, FallbackMode, LimitSource, OperatingMode

# --- plausibility bands ----------------------------------------------------
# Power in watts. The lower edge is negative because an inverter legitimately
# consumes at night, and reactive power is signed by nature.
_POWER_MIN_W = -100_000.0
_POWER_MAX_W = 100_000.0
# Cumulative yield, kWh after the FIX3 scale. 1e7 kWh is far past any plausible
# lifetime and far below the scaled U32 sentinel (4.29e6 kWh -> see below).
_YIELD_MAX_KWH = 1_000_000.0
_VOLTAGE_MAX_V = 1_000.0
_CURRENT_MAX_A = 1_000.0
_FREQUENCY_MIN_HZ = 10.0
_FREQUENCY_MAX_HZ = 100.0
# The fallback timeout register's own documented range.
_TIMEOUT_MIN_S = 0.0
_TIMEOUT_MAX_S = 1_800.0

# --- register scale factors ------------------------------------------------
# Applied here rather than in the components, so a sentinel is rejected while it
# is still recognisably a sentinel. Names match SMA's FIXn notation.
_FIX2 = 0.01
_FIX3 = 0.001


@dataclass(frozen=True, slots=True)
class SmaData:
    """An immutable snapshot, produced by ``SmaModbusDevice.async_update()``.

    Numeric members are ``None`` when the inverter did not return a plausible
    value, so a consumer can show "unknown" rather than a fabricated number.
    The enum members always carry a value, falling back to their ``UNREADABLE``
    member.

    ``None`` here means "could not be read", never "zero".
    """

    # -- production and grid (polled) --------------------------------------
    pv_power: float | None  # W, real PV generation (35469)
    ac_power: float | None  # W, AC output (30775)
    apparent_power: float | None  # VA
    reactive_power: float | None  # var
    ac_voltage: float | None  # V
    ac_current: float | None  # A
    grid_frequency: float | None  # Hz
    total_yield: float | None  # kWh, lifetime

    # -- limit control (polled) --------------------------------------------
    operating_mode: OperatingMode  # 40210
    limit_source: LimitSource  # 35547
    power_limit_applied: float | None  # W, the binding limit (31405)
    fallback_mode: FallbackMode  # 41193
    fallback_timeout: float | None  # s (41525)

    # -- diagnostics (on demand; None until read) --------------------------
    condition: Condition
    derating: Derating
    message: int | None
    wmax: float | None  # W
    nominal_power_ok: float | None  # W
    nominal_pv_power: float | None  # W
    available_power: float | None  # W

    # -- derived -----------------------------------------------------------
    @property
    def mode_supports_writes(self) -> bool:
        """Whether a setpoint write does anything in the current mode.

        False in modes 1078/1077/303, where the inverter's setpoint lives in a
        flash register this library refuses to touch, and in UNREADABLE, where we
        simply cannot tell. A caller must not write while this is False.
        """
        return self.operating_mode.is_writable

    @property
    def readback_usable(self) -> bool:
        """Whether the operating mode could be read at all.

        When 40210 answers with a sentinel, every safety check that depends on
        the mode is blind. That is worth reporting on its own: "I cannot see"
        is a different failure from "it is not working", and conflating the two
        is how a broken readback passes for a healthy inverter.
        """
        return self.operating_mode is not OperatingMode.UNREADABLE

    @property
    def watchdog_meaningful(self) -> bool:
        """Whether a heartbeat has any purpose right now.

        Only with an ARMED fallback (41193 = 2507) does the inverter drop the
        limit when the writes stop, which is the only situation where the limit
        has to be refreshed. With 2506 the limit simply persists - a heartbeat
        buys nothing, and releasing the limit takes an explicit 100% write.
        """
        return self.fallback_mode.is_armed and self.operating_mode.is_writable

    @classmethod
    def from_components(
        cls,
        telemetry: SmaTelemetry,
        grid_limit: SmaGridLimit,
        diagnostics: SmaDiagnostics | None = None,
        capabilities: SmaCapabilities | None = None,
    ) -> SmaData:
        """Build a snapshot from freshly-read components.

        ``diagnostics`` and ``capabilities`` are optional because they are read
        separately from the poll; until they have been read once their fields
        come out as ``None``/``UNREADABLE`` rather than being faked. The Home
        Assistant integration reads them once at setup so they are populated
        from the start, then only on request.
        """
        return cls(
            pv_power=_band(telemetry.pv_power, _POWER_MIN_W, _POWER_MAX_W),
            ac_power=_band(telemetry.ac_power, _POWER_MIN_W, _POWER_MAX_W),
            apparent_power=_band(
                telemetry.apparent_power, _POWER_MIN_W, _POWER_MAX_W
            ),
            reactive_power=_band(
                telemetry.reactive_power, _POWER_MIN_W, _POWER_MAX_W
            ),
            ac_voltage=_band(telemetry.ac_voltage, 0.0, _VOLTAGE_MAX_V, _FIX2),
            ac_current=_band(telemetry.ac_current, 0.0, _CURRENT_MAX_A, _FIX3),
            grid_frequency=_band(
                telemetry.grid_frequency,
                _FREQUENCY_MIN_HZ,
                _FREQUENCY_MAX_HZ,
                _FIX2,
            ),
            total_yield=_band(telemetry.total_yield, 0.0, _YIELD_MAX_KWH, _FIX3),
            operating_mode=OperatingMode.from_raw(telemetry.operating_mode),
            limit_source=LimitSource.from_raw(telemetry.limit_source),
            power_limit_applied=_band(
                grid_limit.power_limit_applied, 0.0, _POWER_MAX_W
            ),
            fallback_mode=FallbackMode.from_raw(telemetry.fallback_mode),
            fallback_timeout=_band(
                telemetry.fallback_timeout, _TIMEOUT_MIN_S, _TIMEOUT_MAX_S, _FIX2
            ),
            condition=Condition.from_raw(
                diagnostics.condition if diagnostics else None
            ),
            derating=Derating.from_raw(
                diagnostics.derating if diagnostics else None
            ),
            message=_as_int(
                _band(diagnostics.message, 0.0, 1e6) if diagnostics else None
            ),
            wmax=_band(diagnostics.wmax, 0.0, _POWER_MAX_W) if diagnostics else None,
            nominal_power_ok=(
                _band(diagnostics.nominal_power_ok, 0.0, _POWER_MAX_W)
                if diagnostics
                else None
            ),
            nominal_pv_power=(
                _band(capabilities.nominal_pv_power, 0.0, _POWER_MAX_W)
                if capabilities
                else None
            ),
            available_power=(
                _band(capabilities.available_power, 0.0, _POWER_MAX_W)
                if capabilities
                else None
            ),
        )


def _band(
    value: float | int | None,
    low: float,
    high: float,
    scale: float = 1.0,
) -> float | None:
    """Scale a raw register value, or return ``None`` if it is not plausible.

    The band is tested on the SCALED value and both edges are INCLUSIVE. Order
    matters: scaling first and testing second means the band can be written in
    the units a reader thinks in, while every SMA NaN sentinel is far enough
    outside any real range that scaling cannot pull it back in.
    """
    if value is None:
        return None
    scaled = float(value) * scale
    if low <= scaled <= high:
        return scaled
    return None


def _as_int(value: float | None) -> int | None:
    """Narrow a decoded number to int, preserving ``None``."""
    return None if value is None else int(value)
