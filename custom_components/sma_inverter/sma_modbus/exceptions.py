"""Exceptions for the sma-modbus device library.

Transport failures surface as :class:`SmaConnectionError`, which subclasses
``modbus_connection.ModbusError`` as well - so a consumer that already catches
the neutral Modbus hierarchy (as the Home Assistant coordinator does) catches
these without knowing about this library's own types.
"""

from __future__ import annotations

from modbus_connection import ModbusError


class SmaModbusError(Exception):
    """Base error for the SMA Modbus device library."""


class SmaConnectionError(SmaModbusError, ModbusError):
    """Communication with the inverter failed."""


class SmaValueError(SmaModbusError, ValueError):
    """A value to write is outside the allowed range or option set."""


class SmaFlashProtectionError(SmaModbusError):
    """A write was refused because it would needlessly wear the inverter's flash.

    The configuration registers (40210, 40212, 40214, 41193, 41525) live in
    flash and the SMA documentation (SS2.3, p.9) is explicit: "Cyclical changing
    of these parameters leads to destruction of the flash memory of the
    devices." Writing a value that is already in place buys nothing and costs a
    flash cycle, so the device library refuses it rather than trusting every
    caller to check first.
    """
