"""Enumerations for the SMA Modbus device model.

Every enum has an ``UNREADABLE`` member and a ``from_raw`` classmethod, because
a register that the inverter cannot answer returns a NaN sentinel rather than an
error (SS6, p.64) - so "not readable" is a normal, expected value here and must
never crash a decode.

THE MEMBER IS ``UNREADABLE`` AND NOT ``UNKNOWN``, and that is not cosmetic. These
names are lowercased into Home Assistant enum-sensor states, and ``unknown`` is
a RESERVED state there meaning "this entity has no value". A sentinel called
UNKNOWN therefore renders identically to a register that was never read, which
collapses the exact distinction the rest of this library works to preserve:
"I cannot see it" is a different fact from "it has not been looked at".
"""

from __future__ import annotations

from enum import IntEnum

# SMA's NaN sentinels. TAGLIST / status registers use 0xFFFFFD; plain numeric
# registers use 0xFFFFFFFF (U32) or 0x80000000 (S32). Register 30219 (derating)
# is documented to report the TAGLIST sentinel on this device, so this is a
# value seen in normal operation, not a fault.
TAGLIST_NAN = 16777213
UINT32_NAN = 4294967295
INT32_NAN = -2147483648

NAN_SENTINELS = frozenset({TAGLIST_NAN, UINT32_NAN, INT32_NAN})


def _decode[T](cls: type[T], value: int | None) -> T:
    """Map a raw register value onto ``cls``, falling back to UNREADABLE.

    Sentinels are rejected explicitly rather than being left to fall through
    the ValueError path, so that "the inverter told me it cannot read this" is
    handled as the documented outcome it is instead of as a surprise.
    """
    if value is None or int(value) in NAN_SENTINELS:
        return cls.UNREADABLE  # type: ignore[attr-defined,return-value]
    try:
        return cls(int(value))  # type: ignore[call-arg]
    except (ValueError, TypeError):
        return cls.UNREADABLE  # type: ignore[attr-defined,return-value]


class OperatingMode(IntEnum):
    """Operating mode of the active power limitation (register 40210).

    Only :attr:`EXTERNAL_SETPOINT` (1079) has a working write path in this
    library. The other modes put the setpoint in a flash configuration register
    (40212 for watts, 40214 for percent), and a limit that follows the sun does
    not belong in flash - see :class:`SmaFlashProtectionError`. They are decoded
    because the inverter can be *in* them, not because we can drive them.
    """

    OFF = 303
    WATT = 1077  # setpoint = 40212 (config/flash) - not written
    PERCENT_OF_WMAX = 1078  # setpoint = 40214 (config/flash) - not written
    EXTERNAL_SETPOINT = 1079  # setpoint = 40016 (grid management) - written
    UNREADABLE = -1

    @classmethod
    def from_raw(cls, value: int | None) -> OperatingMode:
        """Map a raw register value to the enum, defaulting to UNREADABLE."""
        return _decode(cls, value)

    @property
    def is_writable(self) -> bool:
        """Whether a power-limit setpoint can be written in this mode.

        Only true for 1079. In every other mode a write would either be ignored
        by the inverter or land in flash, so the caller must not write at all.
        """
        return self is OperatingMode.EXTERNAL_SETPOINT


class FallbackMode(IntEnum):
    """Behaviour when the active power limitation stops arriving (reg 41193).

    ``USE_FALLBACK`` is what makes a heartbeat meaningful: the inverter drops
    the limit when Home Assistant goes quiet, so the limit has to be refreshed.
    With ``MAINTAIN_VALUES`` the last written limit simply stays in force, which
    means a heartbeat buys nothing - and that releasing a limit takes an
    explicit write of 100%.
    """

    MAINTAIN_VALUES = 2506
    USE_FALLBACK = 2507
    UNREADABLE = -1

    @classmethod
    def from_raw(cls, value: int | None) -> FallbackMode:
        """Map a raw register value to the enum, defaulting to UNREADABLE."""
        return _decode(cls, value)

    @property
    def is_armed(self) -> bool:
        """Whether the inverter really falls back when the link goes quiet."""
        return self is FallbackMode.USE_FALLBACK


class LimitSource(IntEnum):
    """Which source imposes the currently binding power limit (reg 35547).

    ``ACTIVE_POWER_SETPOINT`` (2357) is our own write to 40016. Anything else
    means the limit is not coming from Home Assistant: the P(f) and P(V) curves,
    an infeed limit and the grid-service codes are the inverter or the grid
    operator overriding our setpoint, which is correct behaviour and not a
    fault. ``NONE`` (302) is the normal state when nothing is limiting.
    """

    NONE = 302
    ACTIVE_POWER_SETPOINT = 2357
    ACTIVE_POWER_SETPOINT_2 = 4554
    P_F_CURVE = 4555
    P_V_CURVE = 4556
    INFEED_LIMIT = 4557
    FCR = 4998
    INTEGRAL_LOCAL_FREQUENCY = 5184
    UNBALANCED_POWER_LIMITATION = 5510
    UNREADABLE = -1

    @classmethod
    def from_raw(cls, value: int | None) -> LimitSource:
        """Map a raw register value to the enum, defaulting to UNREADABLE."""
        return _decode(cls, value)

    @property
    def is_ours(self) -> bool:
        """Whether the binding limit is the one we wrote to 40016."""
        return self in (
            LimitSource.ACTIVE_POWER_SETPOINT,
            LimitSource.ACTIVE_POWER_SETPOINT_2,
        )

    @property
    def is_limiting(self) -> bool:
        """Whether anything is limiting at all."""
        return self not in (LimitSource.NONE, LimitSource.UNREADABLE)


class Derating(IntEnum):
    """Why the inverter is derating (register 30219).

    Anything other than ``NOT_ACTIVE`` is a strong signal that the inverter is
    throttling for its own reasons, and says which - so a limit that comes in
    lower than requested can be explained instead of reported as a failed write.
    """

    NONE = 302
    NOT_ACTIVE = 884
    TEMPERATURE = 557
    WMAX_DERATING = 1704
    FREQUENCY = 1705
    PV_CURRENT_LIMITATION = 1706
    UNREADABLE = -1

    @classmethod
    def from_raw(cls, value: int | None) -> Derating:
        """Map a raw register value to the enum, defaulting to UNREADABLE."""
        return _decode(cls, value)

    @property
    def is_derating(self) -> bool:
        """Whether the inverter is actively derating."""
        return self not in (Derating.NONE, Derating.NOT_ACTIVE, Derating.UNREADABLE)


class Condition(IntEnum):
    """Inverter condition (register 30201)."""

    FAULT = 35
    OFF = 303
    OK = 307
    WARNING = 455
    UNREADABLE = -1

    @classmethod
    def from_raw(cls, value: int | None) -> Condition:
        """Map a raw register value to the enum, defaulting to UNREADABLE."""
        return _decode(cls, value)
