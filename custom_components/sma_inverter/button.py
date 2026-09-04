"""Button platform: the deliberate, one-shot actions.

TWO OF THESE THREE WRITE FLASH. 40210, 41193 and 41525 are configuration
registers, and SS2.3 (p.9) is blunt about repeated writes destroying the flash
memory. A button is the right shape for that: a person presses it once, on
purpose. Do not move these into an automation that repeats.

The device library refuses a write that would not change anything, so a second
press is free - the guard is in the library rather than here, where it cannot be
forgotten by a future caller.

GRID GUARD: 40210 appears in this inverter's Grid Guard table (SS5.3, p.51),
which in principle requires a code in register 43090 before it can be changed.
On this device the write succeeds without that login - established repeatedly.
Were it ever refused, the Modbus exception shows up in the log and the mode can
be set in the inverter's own UI instead; a Grid Guard login over Modbus is
exclusive, IP-bound and lost on an inverter restart, so it is not something to
hold open from Home Assistant.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SmaConfigEntry
from .coordinator import SmaCoordinator
from .entity import SmaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            SmaWriteOperatingModeButton(coordinator),
            SmaWriteFallbackButton(coordinator),
            SmaRefreshDiagnosticsButton(coordinator),
        ]
    )


class SmaWriteOperatingModeButton(SmaEntity, ButtonEntity):
    """Write the selected operating mode to register 40210 (FLASH).

    A mode change is not always effective immediately: the write lands, but the
    inverter may not apply it until restarted, and in that half-applied state it
    clamps to 20% of WMax and ignores every setpoint write. If nothing happens
    after pressing this, restart the inverter before looking further.
    """

    _attr_translation_key = "write_operating_mode"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "write_operating_mode")

    async def async_press(self) -> None:
        """Write the mode, then wait out the reaction time and re-read."""
        await self.coordinator.async_write_operating_mode()


class SmaWriteFallbackButton(SmaEntity, ButtonEntity):
    """Write the fallback timeout (41525) then the mode (41193). Both FLASH.

    The timeout goes first, so an armed fallback is never left running on a
    stale timeout, and the two writes are spaced by 10 s per SS7.2. The press
    therefore takes around 20 seconds - fine for a manual action.
    """

    _attr_translation_key = "write_fallback_config"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "write_fallback_config")

    async def async_press(self) -> None:
        """Write the fallback pair and move the heartbeat flag to match."""
        await self.coordinator.async_write_fallback_config()


class SmaRefreshDiagnosticsButton(SmaEntity, ButtonEntity):
    """Read the on-demand diagnostic registers.

    These registers are read once at setup and then never on their own, so
    this button is how they get refreshed - it is not how they first get a
    value. It also produces a SYNCHRONOUS snapshot, which is the only way to
    compare PV production against AC output meaningfully: polled separately
    they can be a minute apart, and under moving cloud the comparison is
    worthless.
    """

    _attr_translation_key = "refresh_diagnostics"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "refresh_diagnostics")

    async def async_press(self) -> None:
        """Read the diagnostic registers now."""
        await self.coordinator.async_refresh_diagnostics()
