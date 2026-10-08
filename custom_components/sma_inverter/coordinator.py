"""Coordinator and policy engine for the SMA Inverter (Modbus) integration.

The device library stays pure; ALL curtailment policy lives here, and this is
the single place that issues writes, so nothing races:

  * watt-to-percent conversion against PMAX, and the setpoint write to 40016
  * the mode gate: nothing is written unless the inverter is really in 1079
  * the heartbeat, at a period derived from the inverter's fallback timeout
  * Home-Assistant-is-boss re-assert of the limit on setup and on reconnect
    (re-asserted in place: the Modbus connection is shared, so a drop never
    reloads the entry)
  * the flash-protected configuration writes, spaced per SS7.2
  * link health, for the connectivity sensor

THE MODE GATE IS THE LOAD-BEARING RULE. Register 40016 only does anything in
operating mode 1079. In modes 1078 and 1077 the inverter's setpoint lives in a
flash register (40214 / 40212) that a sun-following limit would rewrite all day,
so this integration does not write there at all - it writes nothing and says so
through `sensor.*_operating_mode`. Never "fall back" to those registers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .const import (
    CONF_FALLBACK_TIMEOUT,
    CONF_PMAX,
    CONFIG_SETTLE_SECONDS,
    CONFIG_WRITE_SPACING_SECONDS,
    DEFAULT_FALLBACK_TIMEOUT,
    DEFAULT_PMAX,
    DOMAIN,
    HEARTBEAT_FALLBACK_SECONDS,
    HEARTBEAT_MAX_SECONDS,
    HEARTBEAT_MIN_SECONDS,
    HEARTBEAT_TICK_SECONDS,
    LINK_DOWN_GRACE,
    UPDATE_INTERVAL,
    WRITE_DEBOUNCE_SECONDS,
)
from .sma_modbus import (
    FALLBACK_TIMEOUT_MAX_S,
    FALLBACK_TIMEOUT_MIN_S,
    FallbackMode,
    OperatingMode,
    SmaData,
    SmaFlashProtectionError,
    SmaModbusDevice,
    SmaModbusError,
)

_LOGGER = logging.getLogger(__name__)


class SmaCoordinator(DataUpdateCoordinator[SmaData]):
    """Poll the inverter and own all write policy."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, device: SmaModbusDevice
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.device = device
        self._options = {**entry.data, **entry.options}

        # Policy state. This is the source of truth; entities are views of it
        # and push their restored values back into it.
        #
        # The limit is held in WATTS because that is what a person wants to set;
        # the conversion to percent happens once, at the write.
        self.requested_watt: float = self.pmax
        self.heartbeat: bool = False
        self.target_mode: OperatingMode = OperatingMode.EXTERNAL_SETPOINT
        self.target_fallback_mode: FallbackMode = FallbackMode.USE_FALLBACK

        # Write serialization and debounce.
        self._write_lock = asyncio.Lock()
        self._debounce_cancel = None
        self._heartbeat_cancel = None
        self._last_write: datetime | None = None
        self._last_success: datetime | None = None
        # Set when the link drops; the next successful poll re-asserts the limit.
        self._reassert_pending = False

    # -- tunables -----------------------------------------------------------
    @property
    def pmax(self) -> float:
        """The inverter's maximum active power in watts.

        The denominator of every watt-to-percent conversion. Register 41203
        reads the inverter's own idea of it back as a check.
        """
        return float(self._options.get(CONF_PMAX, DEFAULT_PMAX))

    @property
    def target_fallback_timeout(self) -> int:
        """The fallback timeout to write to register 41525, in seconds."""
        return int(
            self._options.get(CONF_FALLBACK_TIMEOUT, DEFAULT_FALLBACK_TIMEOUT)
        )

    @property
    def heartbeat_seconds(self) -> int:
        """How often to refresh the setpoint, derived from the fallback timeout.

        Half the timeout, clamped to a sane band - NOT a fixed 30 s. At the
        factory default of 600 s that gives 300 s.

        The source is the CONFIGURED target rather than the readback of 41525:
        the target is always available and cannot come back as a sentinel. If it
        is implausible the period falls back to something short but safe.
        """
        timeout = self.target_fallback_timeout
        if not FALLBACK_TIMEOUT_MIN_S <= timeout <= FALLBACK_TIMEOUT_MAX_S:
            return HEARTBEAT_FALLBACK_SECONDS
        return min(
            max(round(timeout / 2), HEARTBEAT_MIN_SECONDS), HEARTBEAT_MAX_SECONDS
        )

    # -- link health ---------------------------------------------------------
    @property
    def link_down(self) -> bool:
        """Whether the Modbus link has been down long enough to report it.

        A brief flap self-heals and should not raise an alarm, so this only
        turns true once polling has been failing for the whole grace period.
        """
        if self.last_update_success:
            return False
        if self._last_success is None:
            return True
        return dt_util.utcnow() - self._last_success >= LINK_DOWN_GRACE

    # -- polling ------------------------------------------------------------
    async def _async_update_data(self) -> SmaData:
        """Read the inverter."""
        try:
            data = await self.device.async_update()
        except SmaModbusError as err:
            raise UpdateFailed(str(err)) from err
        self._last_success = dt_util.utcnow()
        if self._reassert_pending:
            self._reassert_pending = False
            # Not eager: let the coordinator publish this poll first, so
            # the mode gate in the re-assert sees the fresh readback.
            self.hass.async_create_task(
                self.async_reassert_policy(), eager_start=False
            )
        return data

    async def async_refresh_diagnostics(self) -> None:
        """Read the on-demand diagnostic registers.

        Gives a SYNCHRONOUS snapshot, which is the only way to compare PV
        against AC output meaningfully - polled separately those two can be over
        a minute apart, and under moving cloud the comparison is worthless.
        """
        try:
            data = await self.device.async_update_diagnostics()
        except SmaModbusError as err:
            _LOGGER.error("SMA: reading the diagnostic registers failed: %s", err)
            return
        self.async_set_updated_data(data)

    # -- reconnect ----------------------------------------------------------
    @callback
    def mark_connection_lost(self) -> None:
        """Note that the link dropped, so the limit is re-asserted once it is back.

        The connection belongs to the Modbus integration, is shared with any
        other entry on the same device and reconnects on its own, so a drop
        must not reload this entry. What a drop invalidates is the limit: after
        a fallback timeout the inverter is back at 100% while the slider still
        means something lower, and that is put back after the next successful
        poll.
        """
        self._reassert_pending = True

    async def async_reassert_policy(self) -> None:
        """Re-assert the limit after a reconnect, if Home Assistant wants one.

        Nothing to hold when the slider is at PMAX; otherwise the write goes
        through the usual mode gate, so an inverter not in 1079 is left alone.
        """
        if not self.curtailing:
            return
        _LOGGER.info("SMA: the link is back - re-asserting the power limit")
        await self.async_write_setpoint()

    # -- the setpoint -------------------------------------------------------
    @property
    def requested_pct(self) -> int:
        """The requested limit as whole percent of PMAX, clamped to 0..100.

        Whole percent because 40016 is FIX0; clamped because a slider that
        somehow exceeded PMAX must not encode as more than 100.
        """
        pmax = self.pmax or DEFAULT_PMAX
        return min(max(round(self.requested_watt / pmax * 100), 0), 100)

    @property
    def curtailing(self) -> bool:
        """Whether Home Assistant is actually asking for a limit.

        The slider at PMAX means "no limit wanted". There is then nothing to
        hold and the heartbeat can stop: the fallback is allowed to lapse and
        the inverter stays at 100%. This is what keeps the integration from
        writing pointless 100% setpoints forever.
        """
        return self.requested_watt < self.pmax

    @callback
    def schedule_setpoint_write(self) -> None:
        """Debounce writes triggered by slider changes."""
        if self._debounce_cancel is not None:
            self._debounce_cancel()

        async def _fire(_now) -> None:
            self._debounce_cancel = None
            await self.async_write_setpoint()

        self._debounce_cancel = async_call_later(
            self.hass, WRITE_DEBOUNCE_SECONDS, _fire
        )

    async def async_write_setpoint(self) -> None:
        """Write the requested limit to register 40016, if the mode allows it.

        Refuses in any mode other than 1079. That is not caution for its own
        sake: in 1078/1077 the inverter's setpoint is a flash register, and in
        303 there is no limitation at all - so a write would either wear flash
        or be silently dropped.
        """
        data = self.data
        if data is not None and not data.mode_supports_writes:
            _LOGGER.debug(
                "SMA: not writing a setpoint in mode %s - only 1079 has a "
                "cyclic-safe setpoint register",
                data.operating_mode.name,
            )
            return

        pct = self.requested_pct
        async with self._write_lock:
            try:
                await self.device.async_set_power_limit_pct(pct)
            except SmaModbusError as err:
                _LOGGER.error("SMA: writing the power limit failed: %s", err)
                return
            self._last_write = dt_util.utcnow()
        _LOGGER.debug("SMA: wrote a limit of %s%% of PMAX to 40016", pct)

    # -- heartbeat ----------------------------------------------------------
    def start_heartbeat(self) -> None:
        """Start the heartbeat ticker."""
        self._schedule_heartbeat()

    def _schedule_heartbeat(self) -> None:
        self._heartbeat_cancel = async_call_later(
            self.hass, HEARTBEAT_TICK_SECONDS, self._heartbeat_tick
        )

    async def _heartbeat_tick(self, _now) -> None:
        """Refresh the setpoint if the derived period has elapsed.

        Ticks often and writes rarely: the tick only costs a comparison, so the
        period can be derived from the fallback timeout instead of being pinned
        to the timer interval.
        """
        if self._heartbeat_due():
            await self.async_write_setpoint()
        self._schedule_heartbeat()

    def _heartbeat_due(self) -> bool:
        """Whether a heartbeat write is warranted right now."""
        if not self.heartbeat or not self.curtailing:
            return False
        data = self.data
        if data is None or not data.mode_supports_writes:
            return False
        if self._last_write is None:
            return True
        elapsed = (dt_util.utcnow() - self._last_write).total_seconds()
        return elapsed >= self.heartbeat_seconds

    def stop_heartbeat(self) -> None:
        """Stop the heartbeat and cancel any pending debounced write."""
        if self._heartbeat_cancel is not None:
            self._heartbeat_cancel()
            self._heartbeat_cancel = None
        if self._debounce_cancel is not None:
            self._debounce_cancel()
            self._debounce_cancel = None

    # -- flash configuration writes -----------------------------------------
    async def async_write_operating_mode(self) -> None:
        """Write the selected operating mode to register 40210.

        One write per actual change: the device library refuses a no-op, so a
        second press of the button costs no flash cycle.
        """
        data = self.data
        current = data.operating_mode if data else None
        async with self._write_lock:
            try:
                await self.device.async_set_operating_mode(
                    self.target_mode, current=current
                )
            except SmaFlashProtectionError as err:
                _LOGGER.info("SMA: %s", err)
                return
            except SmaModbusError as err:
                _LOGGER.error("SMA: writing the operating mode failed: %s", err)
                return
        await self._settle_and_refresh()

    async def async_write_fallback_config(self) -> None:
        """Write the fallback timeout (41525) and then the mode (41193).

        ORDER AND SPACING BOTH MATTER. The timeout goes first so an armed
        fallback is never left running on a stale timeout, and the two writes
        are spaced because SS7.2 asks for 10 s between transfers and this
        inverter is particular about configuration registers.
        """
        data = self.data
        async with self._write_lock:
            wrote = False
            try:
                await self.device.async_set_fallback_timeout(
                    self.target_fallback_timeout,
                    current=data.fallback_timeout if data else None,
                )
                wrote = True
            except SmaFlashProtectionError as err:
                _LOGGER.info("SMA: %s", err)
            except SmaModbusError as err:
                _LOGGER.error("SMA: writing the fallback timeout failed: %s", err)

            if wrote:
                await asyncio.sleep(CONFIG_WRITE_SPACING_SECONDS)

            try:
                await self.device.async_set_fallback_mode(
                    self.target_fallback_mode,
                    current=data.fallback_mode if data else None,
                )
            except SmaFlashProtectionError as err:
                _LOGGER.info("SMA: %s", err)
            except SmaModbusError as err:
                _LOGGER.error("SMA: writing the fallback mode failed: %s", err)

        # The heartbeat only makes sense with an armed fallback, so move the
        # flag with the mode the user just chose. With 2506 the inverter keeps
        # the limit by itself and a heartbeat would be pure bus traffic.
        await self.async_set_heartbeat(
            self.target_fallback_mode is FallbackMode.USE_FALLBACK
        )
        await self._settle_and_refresh()

    async def _settle_and_refresh(self) -> None:
        """Wait out the inverter's reaction time, then re-read.

        SS7.2 gives the inverter 5-10 s to react. Without this pause a readback
        straight after a configuration write shows the OLD value and looks like
        a failed write.
        """
        await asyncio.sleep(CONFIG_SETTLE_SECONDS)
        await self.async_request_refresh()

    # -- setters used by entities ------------------------------------------
    async def async_set_requested_watt(self, watt: float) -> None:
        """Set the requested power limit in watts."""
        self.requested_watt = watt
        self.async_update_listeners()
        self.schedule_setpoint_write()

    async def async_set_heartbeat(self, enabled: bool) -> None:
        """Arm or disarm the cyclic refresh of the setpoint."""
        self.heartbeat = enabled
        self.async_update_listeners()

    async def async_set_target_mode(self, mode: OperatingMode) -> None:
        """Choose the operating mode the button will write."""
        self.target_mode = mode
        self.async_update_listeners()

    async def async_set_target_fallback_mode(self, mode: FallbackMode) -> None:
        """Choose the fallback mode the button will write."""
        self.target_fallback_mode = mode
        self.async_update_listeners()
