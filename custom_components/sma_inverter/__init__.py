"""The SMA Inverter (Modbus) integration.

Borrows two unit handles on a Modbus connection the Modbus integration shares
between integrations (see `connection.py`), wraps them in the vendored
`sma_modbus` device library, and adds the curtailment policy the device
library deliberately does not have.

WHAT THIS REPLACES: a YAML package of 21 individually-timed Modbus sensors,
five template problem sensors and four automations. The register knowledge that
package accumulated - which unit id answers writes, which registers live in
flash, which values are NaN sentinels, which operating mode makes a setpoint
effective - now lives in the device library instead of in Jinja templates.
"""

from __future__ import annotations

import logging

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryError,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from .connection import build_modbus_params
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
        params = build_modbus_params(settings)
        unit_id = int(settings.get(CONF_UNIT_ID, DEFAULT_UNIT_ID))
        unit_id_alt = int(settings.get(CONF_UNIT_ID_ALT, DEFAULT_UNIT_ID_ALT))
    except (KeyError, TypeError, ValueError) as err:
        raise ConfigEntryNotReady(
            f"The SMA config entry has no usable connection data: {err}"
        ) from err

    # The Modbus integration owns the connection: it opens on first use, is
    # shared with any other integration describing the same device, and
    # closes when the last config entry holding a unit on it unloads. Each
    # hold's release is registered on this entry by async_get_unit itself, so
    # there is nothing to close here.
    #
    # Two holds over ONE socket: the shared integration keys connections by
    # device, so both units ride the same link. Unit 3 answers reads and
    # writes, unit 2 is where a few read-only registers are documented, and
    # requests across both are serialized by the connection - which is what
    # SS7.2 wants.
    try:
        unit3 = async_get_unit(hass, entry, params, unit_id)
        unit2 = async_get_unit(hass, entry, params, unit_id_alt)
    except HomeAssistantError as err:
        # Another entry holds this device over different link settings. That
        # is a configuration clash, not a transient fault: it wants a
        # reconfiguration rather than a retry.
        raise ConfigEntryError(str(err)) from err

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
    # still means something lower. Home Assistant is the authority on the
    # limit, not the inverter. The connection is shared and reconnects on its
    # own, so the entry is NOT reloaded for it (that would churn a connection
    # other integrations may hold); the coordinator re-asserts the limit once
    # polling succeeds again.
    entry.async_on_unload(unit3.on_connection_lost(coordinator.mark_connection_lost))

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
