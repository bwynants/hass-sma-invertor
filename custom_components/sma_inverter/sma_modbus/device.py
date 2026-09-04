"""The SMA Modbus device.

Wraps the ``modbus_connection.ModbusUnit`` handles a consumer gets from a
connection it owns (or borrows from a shared one) and presents the inverter as
a device rather than a register map::

    device = SmaModbusDevice(unit3=conn.for_unit(3), unit2=conn.for_unit(2))
    data = await device.async_update()             # -> SmaData
    await device.async_set_power_limit_pct(76)     # whole percent of WMax

TWO UNITS, not one. Reading works on either unit id on this inverter, but
writing to 40016 only works on unit 3 - measured, repeatedly, even though the
parameter list places that register on unit 2. A few read-only registers are
only documented on unit 2. So the device takes both handles and each component
is bound to the unit that actually answers for it.

FLASH PROTECTION IS ENFORCED HERE, not left to the caller. The three
configuration writes each refuse a no-op, because SS2.3 (p.9) warns that
"cyclical changing of these parameters leads to destruction of the flash
memory of the devices" and the cheapest way to honour that is to make a
redundant write impossible rather than merely discouraged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from modbus_connection import ModbusError
from modbus_connection.model import ComponentGroup

from .components import (
    SmaCapabilities,
    SmaDiagnostics,
    SmaGridLimit,
    SmaSettings,
    SmaTelemetry,
)
from .enums import FallbackMode, OperatingMode
from .exceptions import SmaConnectionError, SmaFlashProtectionError
from .model import SmaData

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

# What a probe reads: the operating mode of the active power limitation. Chosen
# over a production register on purpose - production reads 0 at night on a
# perfectly healthy inverter, whereas 40210 answers at any hour. It is also the
# register whose readability the whole safety story depends on, so a probe that
# passes means the interesting half of the map is reachable.
PROBE_ADDRESS = 40210
PROBE_COUNT = 2


class SmaModbusDevice:
    """An SMA hybrid inverter reachable over Modbus TCP."""

    def __init__(self, unit3: ModbusUnit, unit2: ModbusUnit) -> None:
        """Bind the device to its two unit handles on an open connection."""
        self._unit3 = unit3
        self._unit2 = unit2

        self.telemetry = SmaTelemetry(unit3)
        self.grid_limit = SmaGridLimit(unit2)
        self.diagnostics = SmaDiagnostics(unit3)
        self.capabilities = SmaCapabilities(unit2)
        # Never grouped and never read - 40016 is write-only. See SmaSettings.
        self.settings = SmaSettings(unit3)

        # One group per unit, so each poll issues the fewest block reads the
        # declared ranges allow instead of one round trip per datapoint.
        self._poll_unit3 = ComponentGroup(unit3, [self.telemetry])
        self._poll_unit2 = ComponentGroup(unit2, [self.grid_limit])
        self._diag_unit3 = ComponentGroup(unit3, [self.diagnostics])
        self._diag_unit2 = ComponentGroup(unit2, [self.capabilities])

        # Whether the on-demand components hold anything real yet, so a
        # snapshot reports "not read" rather than inventing zeroes.
        self._diagnostics_read = False

    @staticmethod
    async def async_probe(unit: ModbusUnit) -> None:
        """Check that an SMA inverter answers on this unit.

        Lets a config flow tell "wrong host, port or unit id" from a working
        inverter without a full poll.

        Raises :class:`SmaConnectionError` if the inverter does not answer.

        NOTE that this cannot fail on a *wrong value*: an unsupported register
        answers with a NaN sentinel rather than an exception (SS6, p.64), so a
        successful read proves the inverter is reachable and speaking Modbus,
        not that 40210 is readable. Whether the value is usable is
        :attr:`SmaData.readback_usable`'s job, once polling starts.
        """
        try:
            await unit.read_holding_registers(PROBE_ADDRESS, PROBE_COUNT)
        except ModbusError as err:
            raise SmaConnectionError(f"no SMA inverter answered: {err}") from err

    async def async_update(self) -> SmaData:
        """Read the polled registers and return a snapshot.

        The on-demand diagnostic components are folded in from whatever they
        last read; they are not refreshed here. Raises
        :class:`SmaConnectionError` if the inverter could not be read.
        """
        try:
            await self._poll_unit3.async_update()
            await self._poll_unit2.async_update()
        except ModbusError as err:
            raise SmaConnectionError(f"reading the inverter failed: {err}") from err
        return self._snapshot()

    async def async_update_diagnostics(self) -> SmaData:
        """Read the on-demand registers too, and return a full snapshot.

        Reading is always safe - an unsupported register answers NaN instead of
        raising - but it costs bus time, so this is driven by an explicit
        request rather than the poll loop.

        It also produces a SYNCHRONOUS snapshot, which is the only way to
        compare powers meaningfully: polled separately, PV and AC output can be
        over a minute apart, and under moving cloud a comparison between them is
        worthless.
        """
        try:
            await self._diag_unit3.async_update()
            await self._diag_unit2.async_update()
        except ModbusError as err:
            raise SmaConnectionError(
                f"reading the diagnostic registers failed: {err}"
            ) from err
        self._diagnostics_read = True
        return self._snapshot()

    def _snapshot(self) -> SmaData:
        """Assemble a snapshot from whatever the components currently hold."""
        return SmaData.from_components(
            self.telemetry,
            self.grid_limit,
            self.diagnostics if self._diagnostics_read else None,
            self.capabilities if self._diagnostics_read else None,
        )

    # -- the cyclic-safe write ----------------------------------------------
    async def async_set_power_limit_pct(self, percent: float) -> None:
        """Write the active power limit as whole percent of WMax (register 40016).

        This is the ONLY write here that may be repeated freely: 40016 is a
        grid-management setpoint, not a configuration register, and with an
        armed fallback it MUST be repeated or the inverter drops the limit.

        The caller is responsible for checking
        :attr:`SmaData.mode_supports_writes` first - in any other mode the
        inverter accepts this write and silently ignores it.
        """
        await self._write("power_limit_pct", percent)

    # -- flash configuration writes -----------------------------------------
    # Each refuses a no-op. `current` is passed in by the caller from the last
    # poll rather than read here: re-reading before every write would double the
    # bus traffic for a value that is already on hand, and on this inverter the
    # poll is the authority on what is in the register.
    async def async_set_operating_mode(
        self, mode: OperatingMode | int, current: OperatingMode | None = None
    ) -> None:
        """Write the operating mode of the active power limitation (reg 40210).

        Raises :class:`SmaFlashProtectionError` if the inverter is already in
        this mode.

        A MODE CHANGE IS NOT ALWAYS EFFECTIVE IMMEDIATELY. The write lands, but
        the inverter may not apply it until it is restarted - and in that
        half-applied state it clamps to 20% of WMax and ignores every setpoint
        write, whichever register they go to. If nothing happens after a mode
        change, restart the inverter before looking any further.
        """
        target = OperatingMode.from_raw(int(mode))
        self._refuse_noop("operating mode", target, current)
        await self._write("operating_mode", int(target.value))

    async def async_set_fallback_mode(
        self, mode: FallbackMode | int, current: FallbackMode | None = None
    ) -> None:
        """Write the absent-limit behaviour (register 41193).

        Raises :class:`SmaFlashProtectionError` if it is already set.
        """
        target = FallbackMode.from_raw(int(mode))
        self._refuse_noop("fallback mode", target, current)
        await self._write("fallback_mode", int(target.value))

    async def async_set_fallback_timeout(
        self, seconds: float, current: float | None = None
    ) -> None:
        """Write the fallback timeout in seconds (register 41525).

        Raises :class:`SmaFlashProtectionError` if it is already at this value.

        WRITE ORDER MATTERS when changing both: set the TIMEOUT first and the
        MODE second, so an armed fallback is never left running on a stale
        timeout.
        """
        if current is not None and abs(float(current) - float(seconds)) < 0.5:
            raise SmaFlashProtectionError(
                f"the fallback timeout is already {seconds} s; refusing a "
                f"redundant write to flash register 41525"
            )
        await self._write("fallback_timeout", float(seconds))

    @staticmethod
    def _refuse_noop(what: str, target: object, current: object | None) -> None:
        """Reject a configuration write that would not change anything."""
        if current is not None and current == target:
            raise SmaFlashProtectionError(
                f"the {what} is already {target!r}; refusing a redundant write "
                f"to a flash configuration register"
            )

    async def _write(self, field: str, value: float | int) -> None:
        """Write one settings field, normalising transport errors.

        The field's validator runs first and raises ``SmaValueError`` for an
        out-of-range value, before any Modbus traffic.
        """
        try:
            await self.settings.write(field, value)
        except ModbusError as err:
            raise SmaConnectionError(f"writing {field} failed: {err}") from err
