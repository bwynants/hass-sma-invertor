"""Select platform: the TARGET operating mode and fallback mode.

These pick what a button will write; selecting does not write anything by
itself. That separation is the whole point: registers 40210 and 41193 live in
flash, and SS2.3 (p.9) warns that "cyclical changing of these parameters leads
to destruction of the flash memory of the devices". A dropdown that wrote on
every change would be exactly the loaded gun that warning describes - so the
choice and the write are two deliberate steps.

MODE 1078 IS ABSENT FROM THE OPERATING-MODE OPTIONS, on purpose. Its setpoint
register is 40214, in flash; a limit that follows the sun would rewrite it all
day. 1077 has the same problem via 40212. Both are still DECODED by the
sensors, because the inverter can be in them - they are just not something this
integration will put it into. 303 stays selectable because turning the
limitation off entirely is a legitimate thing to want.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import SmaConfigEntry
from .coordinator import SmaCoordinator
from .entity import SmaEntity
from .sma_modbus import FallbackMode, OperatingMode

# Only modes whose setpoint is NOT a flash register, plus "off".
SELECTABLE_MODES: tuple[OperatingMode, ...] = (
    OperatingMode.EXTERNAL_SETPOINT,  # 1079 -> setpoint 40016, cyclic-safe
    OperatingMode.OFF,  # 303 -> no active power limitation at all
)
SELECTABLE_FALLBACKS: tuple[FallbackMode, ...] = (
    FallbackMode.USE_FALLBACK,  # 2507 -> limit lapses; heartbeat needed
    FallbackMode.MAINTAIN_VALUES,  # 2506 -> limit persists; heartbeat pointless
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SmaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the selects."""
    coordinator = entry.runtime_data
    async_add_entities(
        [SmaTargetModeSelect(coordinator), SmaTargetFallbackSelect(coordinator)]
    )


class SmaTargetModeSelect(SmaEntity, SelectEntity, RestoreEntity):
    """The operating mode that "write operating mode" will send to 40210."""

    _attr_translation_key = "target_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [mode.name.lower() for mode in SELECTABLE_MODES]

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the select."""
        super().__init__(coordinator, "target_mode")

    @property
    def available(self) -> bool:
        """Always available: it is a target, not a device reading."""
        return True

    @property
    def current_option(self) -> str:
        """The currently selected target mode."""
        return self.coordinator.target_mode.name.lower()

    async def async_select_option(self, option: str) -> None:
        """Remember the target mode. Writes nothing - the button does that."""
        for mode in SELECTABLE_MODES:
            if mode.name.lower() == option:
                await self.coordinator.async_set_target_mode(mode)
                return

    async def async_added_to_hass(self) -> None:
        """Restore the selection."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            for mode in SELECTABLE_MODES:
                if mode.name.lower() == last.state:
                    self.coordinator.target_mode = mode
                    return


class SmaTargetFallbackSelect(SmaEntity, SelectEntity, RestoreEntity):
    """The fallback mode that "write fallback config" will send to 41193."""

    _attr_translation_key = "target_fallback_mode"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = [mode.name.lower() for mode in SELECTABLE_FALLBACKS]

    def __init__(self, coordinator: SmaCoordinator) -> None:
        """Initialize the select."""
        super().__init__(coordinator, "target_fallback_mode")

    @property
    def available(self) -> bool:
        """Always available: it is a target, not a device reading."""
        return True

    @property
    def current_option(self) -> str:
        """The currently selected target fallback mode."""
        return self.coordinator.target_fallback_mode.name.lower()

    async def async_select_option(self, option: str) -> None:
        """Remember the target. Writes nothing - the button does that."""
        for mode in SELECTABLE_FALLBACKS:
            if mode.name.lower() == option:
                await self.coordinator.async_set_target_fallback_mode(mode)
                return

    async def async_added_to_hass(self) -> None:
        """Restore the selection."""
        await super().async_added_to_hass()
        if (last := await self.async_get_last_state()) is not None:
            for mode in SELECTABLE_FALLBACKS:
                if mode.name.lower() == last.state:
                    self.coordinator.target_fallback_mode = mode
                    return
