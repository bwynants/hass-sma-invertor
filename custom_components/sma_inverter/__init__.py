"""The SMA Inverter (Modbus) integration.

Owns a Modbus connection to the inverter (see `connection.py`), wraps two unit
handles in the vendored `sma_modbus` device library, and adds the curtailment
policy the device library deliberately does not have.

WHAT THIS REPLACES: a YAML package of 21 individually-timed Modbus sensors,
five template problem sensors and four automations. The register knowledge that
package accumulated - which unit id answers writes, which registers live in
flash, which values are NaN sentinels, which operating mode makes a setpoint
effective - now lives in the device library instead of in Jinja templates.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .connection import create_modbus_connection
from .const import (
    CONF_UNIT_ID,
    CONF_UNIT_ID_ALT,
    DEFAULT_UNIT_ID,
    DEFAULT_UNIT_ID_ALT,
)
from .coordinator import SmaCoordinator
from .sma_modbus import SmaModbusDevice

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type SmaConfigEntry = ConfigEntry[SmaCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: SmaConfigEntry) -> bool:
    """Set up an SMA inverter from a config entry."""
    settings = {**entry.data, **entry.options}
    try:
        connection = create_modbus_connection(settings)
        # Two handles over ONE socket: unit 3 answers reads and writes, unit 2
        # is where a few read-only registers are documented. Requests across
        # both are serialized by the connection, which is what SS7.2 wants.
        unit3 = connection.for_unit(int(settings.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)))
        unit2 = connection.for_unit(
            int(settings.get(CONF_UNIT_ID_ALT, DEFAULT_UNIT_ID_ALT))
        )
    except (KeyError, TypeError, ValueError) as err:
        raise ConfigEntryNotReady(
            f"The SMA config entry has no usable connection data: {err}"
        ) from err

    # Closing the link is the last thing to happen on unload, after the
    # platforms and the coordinator have stopped using it.
    entry.async_on_unload(connection.close)

    coordinator = SmaCoordinator(hass, entry, SmaModbusDevice(unit3=unit3, unit2=unit2))
    await coordinator.async_config_entry_first_refresh()

    # Read the on-demand registers ONCE, before the platforms come up, so the
    # diagnostic sensors start out with real values instead of "unknown".
    #
    # They stay off the poll loop - that is the whole point of the split, and
    # SS7.2 (p.68) is the reason - but leaving them empty until somebody finds
    # the refresh button made four sensors look broken when they were merely
    # unread. Six extra block reads once per setup is a rounding error against
    # the bus budget; a nameplate rating that never changes does not need
    # polling, it just needs reading once.
    #
    # Failure here is deliberately not fatal: async_refresh_diagnostics logs
    # and returns rather than raising, so an inverter that answers the poll but
    # not these registers still sets up.
    await coordinator.async_refresh_diagnostics()

    entry.runtime_data = coordinator

    # A dropped link invalidates the limit we asserted on the inverter - and
    # after a fallback timeout the inverter is back at 100% while the slider
    # still means something lower - so reload to re-assert it once the link is
    # back. Home Assistant is the authority on the limit, not the inverter.
    entry.async_on_unload(
        unit3.on_connection_lost(
            lambda: hass.config_entries.async_schedule_reload(entry.entry_id)
        )
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Feed the watchdog (only acts while armed, curtailing, and in mode 1079).
    coordinator.start_heartbeat()

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def _async_reload_on_options(hass: HomeAssistant, entry: SmaConfigEntry) -> None:
    """Re-apply options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: SmaConfigEntry) -> bool:
    """Tear down."""
    entry.runtime_data.stop_heartbeat()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
